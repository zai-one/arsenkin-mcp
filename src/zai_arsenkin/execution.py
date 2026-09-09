from __future__ import annotations

import copy
from functools import partial
from typing import Any
from uuid import UUID

from zai_arsenkin.adapter import active_task_count, build_batch_plan
from zai_arsenkin.models import JobRecord, has_provider_reference
from zai_arsenkin.policy_errors import BudgetDenied
from zai_arsenkin.transport import (
    ProviderHttpError,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransientHttpError,
    ProviderTransportError,
)


def _arsenkin_micro_batch_requests(jobs: list[JobRecord]) -> list[list[JobRecord]]:
    """Group new compatible single jobs without crossing principal boundaries."""
    groups: list[list[JobRecord]] = []
    for job in jobs:
        if job.provider != "arsenkin" or not job.operation.startswith("submit:") or job.result is not None:
            continue
        placed = False
        for group in groups:
            if group[0].principal_id != job.principal_id:
                continue
            candidate = [
                {
                    "client_ref": str(item.job_id),
                    "profile": str(item.payload.get("profile") or ""),
                    "payload": dict(item.payload.get("payload") or {}),
                }
                for item in [*group, job]
            ]
            try:
                plan = build_batch_plan(candidate, 0)
            except ValueError:
                continue
            if int(plan.get("provider_task_count", 0)) == 1:
                group.append(job)
                placed = True
                break
        if not placed:
            groups.append([job])
    return [group for group in groups if len(group) > 1]


def _micro_batch_plan(jobs: list[JobRecord], cost_per_query: float) -> dict[str, Any]:
    return build_batch_plan(
        [
            {
                "client_ref": str(job.job_id),
                "profile": str(job.payload["profile"]),
                "payload": dict(job.payload["payload"]),
            }
            for job in jobs
        ],
        cost_per_query,
    )


def _micro_batch_job(job: JobRecord, plan: dict[str, Any] | None = None) -> JobRecord:
    value = copy.copy(job)
    value.operation = "submit_batch"
    if plan is not None:
        value.payload = {"batch_plan": plan}
    return value


def _micro_batch_marker(leader: JobRecord, members: list[JobRecord]) -> dict[str, Any]:
    return {
        "leader_job_id": str(leader.job_id),
        "member_job_ids": [str(job.job_id) for job in members],
        "member_count": len(members),
        "automatic": True,
    }


def _arsenkin_state(value: Any) -> str:
    """Extract the provider task state from documented nested responses."""
    if not isinstance(value, dict):
        return ""
    for key in ("status", "state"):
        state = str(value.get(key) or "").strip().lower()
        if state:
            return state
    nested = value.get("result")
    nested_state = _arsenkin_state(nested) if isinstance(nested, dict) else ""
    return nested_state or str(value.get("code") or "").strip().lower()


def _new_batch_state(job: JobRecord) -> dict[str, Any]:
    plan = job.payload.get("batch_plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("groups"), list):
        raise ValueError("Arsenkin batch job has no valid batch_plan")
    groups: list[dict[str, Any]] = []
    for item in plan["groups"]:
        if not isinstance(item, dict):
            raise ValueError("Arsenkin batch group must be an object")
        groups.append({**copy.deepcopy(item), "status": "queued"})
    return {
        "batch": True,
        "request_hash": plan.get("request_hash"),
        "input_request_count": plan.get("input_request_count", 0),
        "provider_task_count": plan.get("provider_task_count", len(groups)),
        "deduplicated_query_count": plan.get("deduplicated_query_count", 0),
        "groups": groups,
    }


def _arsenkin_batch_state(job: JobRecord) -> dict[str, Any]:
    """Return durable batch state, rebuilding metadata-only retry results safely."""
    result = job.result
    if isinstance(result, dict) and isinstance(result.get("groups"), list):
        return copy.deepcopy(result)
    if isinstance(result, dict) and (
        has_provider_reference(result) or result.get("submission_outcome_unknown")
    ):
        # Never rebuild over evidence that a paid submission may already exist.
        raise ValueError("Arsenkin batch state is malformed after provider submission")
    state = _new_batch_state(job)
    if isinstance(result, dict) and isinstance(result.get("retry_after_seconds"), int):
        state["retry_after_seconds"] = result["retry_after_seconds"]
    return state


def _batch_summary(state: dict[str, Any]) -> dict[str, int]:
    groups_value = state.get("groups")
    groups: list[Any] = groups_value if isinstance(groups_value, list) else []
    counts = {"queued": 0, "waiting": 0, "completed": 0, "failed": 0, "cancelled": 0}
    for group in groups:
        status = str(group.get("status") or "queued") if isinstance(group, dict) else "failed"
        counts[status if status in counts else "failed"] += 1
    return counts


_DEFINITE_SUBMIT_REJECTION_STATUSES = frozenset({400, 401, 403, 404, 405, 422})


def _provider_rejection(
    exc: ProviderHttpError,
    *,
    stage: str,
    operation: str,
    definite_no_submit: bool,
) -> dict[str, Any]:
    rejection: dict[str, Any] = {
        "stage": stage,
        "operation": operation,
        "http_status": exc.status_code,
        "definite_no_submit": definite_no_submit,
    }
    if exc.body_preview:
        rejection["body_preview"] = exc.body_preview
    return rejection


def _finish_batch_state(state: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
    summary = _batch_summary(state)
    state["summary"] = summary
    terminal = summary["completed"] + summary["failed"] + summary["cancelled"]
    total = sum(summary.values())
    if terminal < total:
        return "waiting", state, None
    if summary["completed"]:
        return "completed", state, "provider_partial_failure" if summary["failed"] else None
    return "failed", state, "provider_failed"


async def _paid_submit(
    job: JobRecord,
    registry: Any,
    operation: str,
    factory: Any,
    *,
    before: dict[str, Any] | None = None,
    group_id: str | None = None,
) -> Any:
    # The standalone registry checkpoints under its lease. Gateway state and
    # lifecycle remain owned by the central platform.
    checkpointed = getattr(registry, "paid_submit", None)
    if checkpointed is not None:
        return await checkpointed(job, operation, factory, before=before, group_id=group_id)
    return await registry.call(job.principal_id, "arsenkin", operation, factory)


async def _execute_arsenkin_batch(job: JobRecord, registry: Any) -> tuple[str, dict[str, Any], str | None]:
    state = _arsenkin_batch_state(job)
    # Retry-After is scheduling metadata, not durable provider-result data.  A
    # successful retry must not leak an obsolete cooldown into later cycles.
    state.pop("retry_after_seconds", None)
    groups = state.get("groups")
    if not isinstance(groups, list):
        raise ValueError("Arsenkin batch state has no groups")
    group = next(
        (
            item
            for item in groups
            if isinstance(item, dict)
            and str(item.get("status") or "queued") not in {"completed", "failed", "cancelled"}
        ),
        None,
    )
    if group is None:
        return _finish_batch_state(state)

    adapter = registry.arsenkin()
    task_id = str(group.get("provider_task_id") or "")
    if task_id:
        provider_status = await registry.read(
            job.principal_id,
            "arsenkin",
            "task_status",
            {"task_id": task_id},
            lambda: adapter.task_status(task_id),
        )
        provider_state = _arsenkin_state(provider_status)
        group["provider_status"] = provider_status
        if provider_state in {"done", "completed", "success", "ready", "finish", "finished"}:
            group["result"] = await registry.read(
                job.principal_id,
                "arsenkin",
                "result",
                {"task_id": task_id},
                lambda: adapter.result(task_id),
            )
            group["status"] = "completed"
            return _finish_batch_state(state)
        if provider_state in {"failed", "error", "cancelled"}:
            group["status"] = "failed"
            group["error_code"] = "provider_failed"
            return _finish_batch_state(state)
        group["status"] = "waiting"
        state["summary"] = _batch_summary(state)
        return "waiting", state, None

    try:
        provider_status = await registry.read(job.principal_id, "arsenkin", "status", {}, adapter.status)
    except ProviderTransientHttpError:
        raise
    except ProviderHttpError as exc:
        error_code = f"provider_rejected_http_{exc.status_code}"
        state["provider_rejection"] = _provider_rejection(
            exc,
            stage="preflight",
            operation="info",
            definite_no_submit=True,
        )
        state["summary"] = _batch_summary(state)
        return "failed", state, error_code
    active_count = active_task_count(provider_status)
    if active_count is None:
        state["summary"] = _batch_summary(state)
        return "waiting", state, "provider_inflight_unknown"
    if active_count >= registry.settings.arsenkin_max_inflight:
        state["summary"] = _batch_summary(state)
        return "waiting", state, "provider_inflight_limit"
    try:
        response = await _paid_submit(
            job,
            registry,
            "submit_batch_group",
            lambda: adapter.submit(str(group["profile"]), dict(group["payload"])),
            before=state,
            group_id=group["group_id"],
        )
    except (
        ProviderResponseError,
        ProviderTimeoutError,
        ProviderTransientHttpError,
        ProviderTransportError,
    ):
        # The provider may have accepted the task even though the response was
        # lost: never blind-resubmit a paid POST and never refund this group.
        group["status"] = "failed"
        group["error_code"] = "submission_outcome_unknown"
        state["summary"] = _batch_summary(state)
        # Stop the whole platform job.  Returning ``waiting`` here would let a
        # later worker cycle submit another queued group after an ambiguous
        # paid POST.
        return "failed", state, "submission_outcome_unknown"
    except ProviderHttpError as exc:
        definite_no_submit = exc.status_code in _DEFINITE_SUBMIT_REJECTION_STATUSES
        group["status"] = "failed"
        group["provider_rejection"] = _provider_rejection(
            exc,
            stage="submit",
            operation="set",
            definite_no_submit=definite_no_submit,
        )
        if definite_no_submit:
            error_code = f"provider_rejected_http_{exc.status_code}"
            group["error_code"] = error_code
        else:
            error_code = "submission_outcome_unknown"
            group["error_code"] = error_code
        state["summary"] = _batch_summary(state)
        # A deterministic contract rejection or an ambiguous paid POST both
        # terminate the whole platform job.  Later queued groups must never be
        # attempted automatically after either outcome.
        return "failed", state, error_code
    task_id_value = response.get("task_id") or response.get("id") if isinstance(response, dict) else None
    if task_id_value in (None, ""):
        # A successful HTTP response without a durable task reference cannot
        # be retried safely: the provider may already have accepted the work.
        group["status"] = "failed"
        group["error_code"] = "submission_outcome_unknown"
        state["summary"] = _batch_summary(state)
        return "failed", state, "submission_outcome_unknown"
    group["provider_task_id"] = str(task_id_value)
    group["submission"] = response
    group["status"] = "waiting"
    state["summary"] = _batch_summary(state)
    return "waiting", state, None


async def execute_job(job: JobRecord, registry: Any) -> tuple[str, dict[str, Any] | None, str | None]:
    try:
        await registry.ensure_enabled(job.provider)
        if job.provider == "arsenkin":
            marker = job.result.get("micro_batch") if isinstance(job.result, dict) else None
            if isinstance(marker, dict):
                leader_id = UUID(str(marker.get("leader_job_id")))
                if leader_id != job.job_id:
                    leader = await registry.store.get_job(leader_id, job.principal_id)
                    if leader is None:
                        return ("failed", job.result, "micro_batch_leader_missing")
                    if leader.status in {"completed", "failed", "cancelled"}:
                        result = copy.deepcopy(leader.result)
                        if isinstance(result, dict) and isinstance(result.get("micro_batch"), dict):
                            result["micro_batch"]["client_job_id"] = str(job.job_id)
                        return (leader.status, result, leader.error_code)
                    return ("waiting", job.result, leader.error_code)
                return await _execute_arsenkin_batch(_micro_batch_job(job), registry)
            if job.operation == "submit_batch":
                return await _execute_arsenkin_batch(job, registry)
            arsenkin_adapter = registry.arsenkin()
            if job.result and job.result.get("provider_task_id"):
                task_id = str(job.result["provider_task_id"])
                status = await registry.read(
                    job.principal_id,
                    "arsenkin",
                    "task_status",
                    {"task_id": task_id},
                    lambda: arsenkin_adapter.task_status(task_id),
                )
                state = _arsenkin_state(status)
                if state in {"done", "completed", "success", "ready", "finish", "finished"}:
                    result = await registry.read(
                        job.principal_id,
                        "arsenkin",
                        "result",
                        {"task_id": task_id},
                        lambda: arsenkin_adapter.result(task_id),
                    )
                    return ("completed", result, None)
                if state in {"failed", "error", "cancelled"}:
                    return ("failed", status, "provider_failed")
                return ("waiting", {"provider_task_id": task_id, "provider_status": status}, None)
            provider_status = await registry.read(
                job.principal_id, "arsenkin", "status", {}, arsenkin_adapter.status
            )
            active_count = active_task_count(provider_status)
            if active_count is None:
                return ("waiting", None, "provider_inflight_unknown")
            if active_count >= registry.settings.arsenkin_max_inflight:
                return ("waiting", None, "provider_inflight_limit")
            profile = str(job.payload["profile"])
            try:
                response = await _paid_submit(
                    job,
                    registry,
                    "submit",
                    lambda: arsenkin_adapter.submit(profile, dict(job.payload["payload"])),
                    before=job.result,
                )
            except (
                ProviderResponseError,
                ProviderTimeoutError,
                ProviderTransientHttpError,
                ProviderTransportError,
            ):
                return ("failed", {"submission_outcome_unknown": True}, "submission_outcome_unknown")
            task_id_value = (
                response.get("task_id") or response.get("id") if isinstance(response, dict) else None
            )
            if task_id_value in (None, ""):
                return ("failed", {"submission_outcome_unknown": True}, "submission_outcome_unknown")
            task_id = str(task_id_value)
            return ("waiting", {"provider_task_id": task_id, "submission": response}, None)
        return ("failed", None, "unsupported_provider")
    except ProviderRateLimited as exc:
        result = (
            _arsenkin_batch_state(job)
            if job.provider == "arsenkin" and job.operation == "submit_batch"
            else copy.deepcopy(job.result)
            if isinstance(job.result, dict)
            else {}
        )
        if exc.retry_after_seconds is not None:
            result["retry_after_seconds"] = exc.retry_after_seconds
        return ("waiting", result or None, "provider_rate_limited")
    except BudgetDenied:
        return "waiting", job.result, "budget_denied"
    except PermissionError:
        return ("waiting", job.result, "provider_disabled")
    except (ProviderResponseError, ProviderTimeoutError, ProviderTransientHttpError, ProviderTransportError):
        return ("waiting", job.result, "provider_transient_error")
    except Exception as exc:
        return ("failed", job.result, type(exc).__name__)


async def execute_cancellation(
    job: JobRecord, registry: Any
) -> tuple[str, dict[str, Any] | None, str | None]:
    try:
        if job.provider == "arsenkin" and job.operation == "submit_batch":
            await registry.ensure_enabled("arsenkin")
            state = _arsenkin_batch_state(job)
            groups = state.get("groups")
            if not isinstance(groups, list):
                raise ValueError("Arsenkin batch state has no groups")
            for group in groups:
                if not isinstance(group, dict):
                    continue
                group_status = str(group.get("status") or "queued")
                if group_status == "queued":
                    group["status"] = "cancelled"
                    continue
                task_id = str(group.get("provider_task_id") or "")
                if task_id and group_status == "waiting":
                    adapter = registry.arsenkin()
                    group["cancellation"] = await registry.call(
                        job.principal_id,
                        "arsenkin",
                        "cancel",
                        partial(adapter.cancel, task_id),
                    )
                    group["status"] = "cancelled"
                    remaining = any(
                        isinstance(item, dict)
                        and str(item.get("status") or "queued") == "waiting"
                        and item.get("provider_task_id")
                        for item in groups
                    )
                    state["summary"] = _batch_summary(state)
                    return ("cancel_requested" if remaining else "cancelled"), state, None
            state["summary"] = _batch_summary(state)
            return "cancelled", state, None
        if job.provider == "arsenkin" and job.result and job.result.get("provider_task_id"):
            task_id = str(job.result["provider_task_id"])
            adapter = registry.arsenkin()
            response = await registry.call(
                job.principal_id,
                "arsenkin",
                "cancel",
                lambda: adapter.cancel(task_id),
            )
            return "cancelled", {**job.result, "cancellation": response}, None
        return "cancelled", job.result, None
    except ProviderRateLimited as exc:
        result = copy.deepcopy(job.result) if isinstance(job.result, dict) else {}
        if exc.retry_after_seconds is not None:
            result["retry_after_seconds"] = exc.retry_after_seconds
        return "cancel_requested", result or None, "provider_rate_limited"
    except PermissionError:
        return "cancel_requested", job.result, "provider_disabled"
    except Exception as exc:
        return "cancel_requested", job.result, type(exc).__name__


def _next_attempt_seconds(
    job: JobRecord,
    registry: Any,
    status: str,
    result: dict[str, Any] | None,
    error: str | None,
) -> int:
    if status not in {"waiting", "cancel_requested"}:
        return 15
    configured_retry = 0
    if isinstance(result, dict):
        value = result.get("retry_after_seconds")
        if isinstance(value, int) and not isinstance(value, bool):
            configured_retry = value
    if error == "provider_rate_limited":
        if job.provider == "arsenkin":
            return max(
                configured_retry,
                registry.settings.arsenkin_rate_limit_cooldown_seconds,
            )
        return max(15, configured_retry)
    if job.provider != "arsenkin":
        return 15
    return registry.settings.arsenkin_poll_interval_seconds
