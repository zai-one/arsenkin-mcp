from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from zai_arsenkin.adapter import build_batch_plan, build_payload, child_request_hashes, estimate_cost
from zai_arsenkin.models import ApprovalRecord, JobRecord
from zai_arsenkin.transport import request_hash


def _within_server_limit(estimated_cost: float, server_budget_limit: float) -> bool:
    """Check one estimate against the server-side ceiling.

    A non-positive ceiling means the operator has not armed one, so the
    estimate passes unchanged. This is the only gate an unlimited standing
    policy cannot talk its way past.
    """
    if server_budget_limit <= 0:
        return True
    return estimated_cost <= server_budget_limit


class JobService:
    def __init__(
        self,
        store: Any,
        arsenkin_cost_per_query: float,
        arsenkin_server_queue_target: int = 50,
        arsenkin_micro_batch_window_seconds: int = 5,
        *,
        approval_record: Any = ApprovalRecord,
        job_record: Any = JobRecord,
    ) -> None:
        self.store = store
        self.approval_record = approval_record
        self.job_record = job_record
        self.arsenkin_cost_per_query = arsenkin_cost_per_query
        self.arsenkin_server_queue_target = arsenkin_server_queue_target
        self.arsenkin_micro_batch_window_seconds = arsenkin_micro_batch_window_seconds
        self._arsenkin_admission_lock = asyncio.Lock()

    async def prepare_arsenkin(
        self,
        principal_id: UUID,
        profile: str,
        payload: dict[str, Any],
        principal_budget_limit: float,
        principal_budget_unlimited: bool = False,
        auto_accept: bool = False,
        standing_policy_id: str = "",
        server_budget_limit: float = 0.0,
    ) -> dict[str, Any]:
        estimate = estimate_cost(profile, payload, self.arsenkin_cost_per_query)
        estimated_cost = float(estimate["estimated_cost_units"])
        valid_estimated_cost = math.isfinite(estimated_cost) and estimated_cost > 0
        # An explicit unlimited automatic policy bypasses the per-principal
        # financial gates. The approval remains a typed, expiring request-hash
        # receipt consumed atomically by submit; it is not a human or budget
        # approval in this mode. The one thing it cannot bypass is the
        # server-side ceiling below, when the operator has armed one.
        within_server_limit = _within_server_limit(estimated_cost, server_budget_limit)
        budget_unlimited = (auto_accept or principal_budget_unlimited) and within_server_limit
        budget_limit = principal_budget_limit if principal_budget_limit > 0 and not budget_unlimited else 0.0
        approval = self.approval_record(
            uuid4(),
            principal_id,
            "arsenkin",
            "submit",
            estimate["request_hash"],
            estimated_cost,
            budget_limit,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            budget_unlimited=budget_unlimited,
        )
        await self.store.create_approval(approval)
        can_accept = (
            valid_estimated_cost
            and within_server_limit
            and (budget_unlimited or approval.estimated_cost <= budget_limit)
        )
        if auto_accept and can_accept:
            if not standing_policy_id or len(standing_policy_id) > 128:
                raise ValueError("standing_policy_id is required for automatic acceptance")
            if not await self.store.accept_approval(
                approval.approval_id,
                acceptance_type="standing_policy_child",
                acceptance_origin="server",
                standing_policy_id=standing_policy_id,
            ):
                raise PermissionError("platform failed to accept the standing-policy child approval")
            approval.status = "accepted"
        return {
            **estimate,
            "approval_id": str(approval.approval_id),
            "approval_status": approval.status,
            "approval_expires_at": approval.expires_at.isoformat(),
            "budget_limit": budget_limit,
            "server_max_cost_per_approval": server_budget_limit or None,
            "budget_unlimited": budget_unlimited,
            "standing_unlimited": bool(auto_accept),
            "standing_finite_budget_required": False,
            "can_accept": can_accept,
            "approval_mode": "standing_policy_unlimited" if auto_accept else "manual",
            "acceptance_type": "standing_policy_child" if auto_accept and can_accept else None,
            "acceptance_origin": "server" if auto_accept and can_accept else None,
            "standing_policy_id": standing_policy_id if auto_accept and can_accept else None,
            "live_call": False,
        }

    async def prepare_arsenkin_batch(
        self,
        principal_id: UUID,
        requests: list[dict[str, Any]],
        principal_budget_limit: float,
        principal_budget_unlimited: bool = False,
        auto_accept: bool = False,
        standing_policy_id: str = "",
        server_budget_limit: float = 0.0,
    ) -> dict[str, Any]:
        plan = build_batch_plan(requests, self.arsenkin_cost_per_query)
        estimated_cost = float(plan["estimated_cost_units"])
        valid_estimated_cost = math.isfinite(estimated_cost) and estimated_cost > 0
        within_server_limit = _within_server_limit(estimated_cost, server_budget_limit)
        budget_unlimited = (auto_accept or principal_budget_unlimited) and within_server_limit
        budget_limit = principal_budget_limit if principal_budget_limit > 0 and not budget_unlimited else 0.0
        approval = self.approval_record(
            uuid4(),
            principal_id,
            "arsenkin",
            "submit_batch",
            str(plan["request_hash"]),
            estimated_cost,
            budget_limit,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            budget_unlimited=budget_unlimited,
        )
        await self.store.create_approval(approval)
        can_accept = (
            valid_estimated_cost
            and within_server_limit
            and (budget_unlimited or estimated_cost <= budget_limit)
        )
        if auto_accept and can_accept:
            if not standing_policy_id or len(standing_policy_id) > 128:
                raise ValueError("standing_policy_id is required for automatic acceptance")
            if not await self.store.accept_approval(
                approval.approval_id,
                acceptance_type="standing_policy_child",
                acceptance_origin="server",
                standing_policy_id=standing_policy_id,
            ):
                raise PermissionError("platform failed to accept the standing-policy child approval")
            approval.status = "accepted"
        return {
            **plan,
            "approval_id": str(approval.approval_id),
            "approval_status": approval.status,
            "approval_expires_at": approval.expires_at.isoformat(),
            "budget_limit": budget_limit,
            "server_max_cost_per_approval": server_budget_limit or None,
            "budget_unlimited": budget_unlimited,
            "standing_unlimited": bool(auto_accept),
            "standing_finite_budget_required": False,
            "can_accept": can_accept,
            "approval_mode": "standing_policy_unlimited" if auto_accept else "manual",
            "acceptance_type": "standing_policy_child" if auto_accept and can_accept else None,
            "acceptance_origin": "server" if auto_accept and can_accept else None,
            "standing_policy_id": standing_policy_id if auto_accept and can_accept else None,
        }

    async def submit_arsenkin(
        self,
        principal_id: UUID,
        profile: str,
        payload: dict[str, Any],
        approval_id: UUID,
        idempotency_key: str,
    ) -> JobRecord:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key is required and must be at most 128 characters")
        async with self._arsenkin_admission_lock:
            return await self._submit_arsenkin_locked(
                principal_id, profile, payload, approval_id, idempotency_key
            )

    async def _submit_arsenkin_locked(
        self,
        principal_id: UUID,
        profile: str,
        payload: dict[str, Any],
        approval_id: UUID,
        idempotency_key: str,
    ) -> JobRecord:
        normalized = build_payload(profile, payload)
        return await self.store.admit_paid_job(
            self.job_record(
                uuid4(),
                principal_id,
                "arsenkin",
                f"submit:{profile}",
                {"profile": profile, "payload": payload},
                idempotency_key=idempotency_key,
                approval_id=approval_id,
                next_attempt_at=datetime.now(UTC)
                + timedelta(seconds=self.arsenkin_micro_batch_window_seconds),
            ),
            approval_operation="submit",
            request_hash=request_hash(normalized),
            child_request_hashes=(request_hash(normalized),),
            backlog_limit=self.arsenkin_server_queue_target,
        )

    async def submit_arsenkin_batch(
        self,
        principal_id: UUID,
        requests: list[dict[str, Any]],
        approval_id: UUID,
        idempotency_key: str,
    ) -> JobRecord:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key is required and must be at most 128 characters")
        async with self._arsenkin_admission_lock:
            plan = build_batch_plan(requests, self.arsenkin_cost_per_query)
            return await self.store.admit_paid_job(
                self.job_record(
                    uuid4(),
                    principal_id,
                    "arsenkin",
                    "submit_batch",
                    {"batch_plan": plan},
                    idempotency_key=idempotency_key,
                    approval_id=approval_id,
                ),
                approval_operation="submit_batch",
                request_hash=str(plan["request_hash"]),
                child_request_hashes=child_request_hashes(requests),
                backlog_limit=self.arsenkin_server_queue_target,
            )
