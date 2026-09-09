from __future__ import annotations

import asyncio
import copy
import math
import re
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import UUID, uuid4

from fastmcp.server.dependencies import get_access_token

from zai_arsenkin.adapter import ArsenkinAdapter
from zai_arsenkin.admission import PolicyStore
from zai_arsenkin.coalescing import AsyncSingleFlight
from zai_arsenkin.config import ServiceConfig
from zai_arsenkin.errors import SafeToolError, safe_provider_error
from zai_arsenkin.execution import _DEFINITE_SUBMIT_REJECTION_STATUSES, _batch_summary
from zai_arsenkin.jobs import JobService
from zai_arsenkin.models import JobRecord, has_provider_reference
from zai_arsenkin.sanitizer import DURABLE_SANITIZER_LIMITS, sanitize_provider_response
from zai_arsenkin.state import StateStore
from zai_arsenkin.transport import (
    JsonHttpClient,
    ProviderAdmissionDenied,
    ProviderError,
    ProviderHttpError,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderTimeoutError,
    canonical_json,
    request_hash,
)

T = TypeVar("T")
OPERATION_TIMEOUT_SECONDS = 75.0


class RequestOwnedFlight(AsyncSingleFlight):
    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        # Do not detach shielded single-flight tasks from their actor, lease or deadline.
        return await factory()


@dataclass(frozen=True)
class Access:
    principal_id: str
    monthly_cost_limit: float
    monthly_cost_unlimited: bool = False


@dataclass
class CallState:
    execution_id: str
    actor: str
    provider: str
    policy: PolicyStore
    http: Any
    adapters: dict[str, Any] = field(default_factory=dict)


class AdmittedHttpClient(JsonHttpClient):
    def __init__(self, policy: PolicyStore, execution_id: str, actor: str):
        super().__init__(timeout=60)
        self.policy, self.execution_id, self.actor = policy, execution_id, actor
        self.admitted_count = 0

    async def _admit_attempt(self, attempt: int) -> None:
        self.policy.admit(self.execution_id, self.actor, self.admitted_count + 1)
        self.admitted_count += 1


class Registry:
    def __init__(self, runtime: Runtime):
        self.runtime, self.settings, self.store = runtime, runtime.config, runtime.store

    def arsenkin(self) -> ArsenkinAdapter:
        state = self.runtime.state.get()
        if "arsenkin" not in state.adapters:
            state.adapters["arsenkin"] = ArsenkinAdapter(
                "https://arsenkin.ru/api/tools",
                self.settings.token,
                http=state.http,
                coalescer=RequestOwnedFlight(),
            )
        return state.adapters["arsenkin"]

    async def paid_submit(self, job, operation, factory, *, before=None, group_id=None):
        claim_id, owner = self.runtime.job_claim.get()
        if claim_id != job.job_id or str(job.principal_id) != self.runtime.state.get().actor:
            raise PermissionError("paid dispatch does not own this job")
        state = self.runtime.state.get()
        admitted_before = state.http.admitted_count
        self.store.dispatch_intent(job.job_id, owner, before)
        try:
            response = await self.call(job.principal_id, "arsenkin", operation, factory)
        except Exception as exc:
            known_denial = (
                (isinstance(exc, ProviderAdmissionDenied) and state.http.admitted_count == admitted_before)
                or (isinstance(exc, ProviderRateLimited) and not isinstance(exc, ProviderAdmissionDenied))
                or (
                    isinstance(exc, ProviderHttpError)
                    and exc.status_code in _DEFINITE_SUBMIT_REJECTION_STATUSES
                )
            )
            if known_denial:
                self.store.checkpoint(
                    job.job_id, owner, before, "polling" if has_provider_reference(before) else "queued"
                )
            raise
        identifier = response.get("task_id") or response.get("id") if isinstance(response, dict) else None
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, (str, int))
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", str(identifier))
        ):
            raise ProviderResponseError("paid submission returned no durable task reference")
        if group_id is None:
            saved = {"provider_task_id": str(identifier), "submission": response}
        else:
            saved = copy.deepcopy(before)
            group = next(group for group in saved["groups"] if group["group_id"] == group_id)
            group.update(provider_task_id=str(identifier), submission=response, status="waiting")
            saved["summary"] = _batch_summary(saved)
        self.store.checkpoint(job.job_id, owner, self.runtime.clean(saved, durable=True), "polling")
        return response

    async def ensure_enabled(self, provider: str) -> None:
        if not self.runtime.config.enabled(provider):
            raise PermissionError("provider credentials are not configured")

    async def call(self, actor: Any, provider: str, operation: str, factory: Callable[[], Awaitable[T]]) -> T:
        state = self.runtime.state.get()
        if str(actor) != state.actor or provider != state.provider:
            raise PermissionError("execution identity mismatch")
        await self.ensure_enabled(provider)
        try:
            return await factory()
        except Exception as exc:
            self.runtime.cooldown(exc)
            raise

    async def read(
        self,
        actor: Any,
        provider: str,
        operation: str,
        arguments: Any,
        factory: Callable[[], Awaitable[T]],
        *,
        validate: Callable[[], Any] | None = None,
    ) -> T:
        if validate:
            validate()
        return await self.call(actor, provider, operation, factory)


class Runtime:
    def __init__(self, config: ServiceConfig, transport: str, http_factory=None):
        self.config = self.settings = config
        self.transport, self.http_factory = transport, http_factory or AdmittedHttpClient
        self.store = StateStore(config)
        self.policies = {
            "arsenkin": PolicyStore(
                config.state_path,
                canonical_json([config.account_id, "arsenkin"]),
                config.request_rate_limit,
                config.principal_rate_limit,
                config.max_concurrency,
            )
        }
        self.state: ContextVar[CallState] = ContextVar("arsenkin_call")
        self.job_claim: ContextVar[tuple[UUID, str]] = ContextVar("arsenkin_job_claim")
        self.registry = Registry(self)
        self.jobs = JobService(
            self.store,
            config.arsenkin_cost_per_query,
            config.arsenkin_server_queue_target,
            config.arsenkin_micro_batch_window_seconds,
        )

    @staticmethod
    def provider_for(tool: str) -> str:
        if not tool.startswith("arsenkin_"):
            raise ValueError("unsupported tool provider")
        return "arsenkin"

    def require_scopes(self, *scopes: str) -> Callable[[Any], bool]:
        def check(context: Any) -> bool:
            available = (
                self.config.local_scopes
                if self.transport == "stdio"
                else (context.token.scopes if context.token else [])
            )
            return set(scopes) <= set(available)

        return check

    def identity(self) -> str:
        if self.transport == "stdio":
            return self.config.principal_id
        token = get_access_token()
        claims = token.claims if token else {}
        actor, expires = claims.get("sub"), claims.get("exp")
        if (
            not isinstance(actor, str)
            or not 1 <= len(actor) <= 256
            or claims.get("account_id") != self.config.account_id
            or not isinstance(expires, (int, float))
            or isinstance(expires, bool)
            or not math.isfinite(expires)
            or expires <= time.time()
        ):
            raise PermissionError("account-bound authenticated identity required")
        return actor

    def current_access(self) -> Access:
        return Access(self.state.get().actor, self.config.principal_monthly_cost_limit)

    def clean(self, value: Any, *, durable: bool = False) -> Any:
        def literal(item: Any) -> Any:
            if isinstance(item, str):
                for credential in self.config.secrets:
                    if credential:
                        item = item.replace(credential, "***redacted***")
                return item
            if isinstance(item, list):
                return [literal(child) for child in item]
            if isinstance(item, dict):
                return {literal(key): literal(child) for key, child in item.items()}
            return item

        sanitized = (
            sanitize_provider_response(value, limits=DURABLE_SANITIZER_LIMITS)
            if durable
            else sanitize_provider_response(value)
        )
        return literal(sanitized)

    @staticmethod
    def safe_error(provider: str, exc: Exception) -> SafeToolError:
        return safe_provider_error(provider, exc)

    async def execute(
        self, tool: str, arguments: dict[str, Any], factory: Callable[[], Awaitable[Any]]
    ) -> Any:
        provider = self.provider_for(tool)
        try:
            return await self.run_owned(provider, self.identity(), tool, arguments, factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, SafeToolError):
                raise
            raise safe_provider_error(provider, exc) from None

    async def run_owned(
        self,
        provider: str,
        actor: str,
        tool: str,
        arguments: dict[str, Any],
        factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        execution_id, policy = uuid4().hex, self.policies[provider]
        policy.begin(execution_id, uuid4().hex, actor, request_hash({"tool": tool, "arguments": arguments}))
        context_token = None
        try:
            http = self.http_factory(policy, execution_id, actor)
            context_token = self.state.set(CallState(execution_id, actor, provider, policy, http))
            async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS):
                result = await factory()
            policy.finish(execution_id, "success")
            return self.clean(result, durable=True)
        except asyncio.CancelledError:
            policy.finish(execution_id, "cancelled")
            raise
        except Exception as exc:
            policy.finish(execution_id, "error")
            if isinstance(exc, TimeoutError):
                raise ProviderTimeoutError("operation deadline exceeded") from None
            raise
        finally:
            if context_token is not None:
                self.state.reset(context_token)

    def cooldown(self, exc: Exception) -> None:
        if isinstance(exc, ProviderRateLimited) and not isinstance(exc, ProviderAdmissionDenied):
            self.state.get().policy.cooldown(exc.retry_after_seconds or 60)

    async def call(self, provider: str, operation: str, factory: Callable[[], Awaitable[T]]) -> T:
        return await self.registry.call(self.state.get().actor, provider, operation, factory)

    async def read(
        self,
        provider: str,
        operation: str,
        arguments: Any,
        factory: Callable[[], Awaitable[T]],
        *,
        validate: Callable[[], Any] | None = None,
    ) -> T:
        try:
            return self.clean(
                await self.registry.read(
                    self.state.get().actor, provider, operation, arguments, factory, validate=validate
                )
            )
        except (ProviderError, PermissionError, ValueError) as exc:
            raise safe_provider_error(provider, exc) from None

    def job_view(self, job: JobRecord, *, include_result: bool = False) -> dict[str, Any]:
        result = {
            "job_id": str(job.job_id),
            "provider": job.provider,
            "operation": job.operation,
            "status": job.status,
            "error_code": job.error_code,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
        }
        if include_result:
            result["result"] = self.clean(job.result, durable=True)
        return result

    async def owned_job(self, job_id: str, *, include_result: bool = False) -> dict[str, Any]:
        job = await self.store.get_job(UUID(job_id), self.state.get().actor)
        if job is None:
            raise ValueError("job not found")
        return self.job_view(
            job, include_result=include_result and job.status in {"completed", "failed", "cancelled"}
        )
