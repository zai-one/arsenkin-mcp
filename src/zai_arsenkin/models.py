from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

_REQUEST_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def has_provider_reference(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("provider_task_id") or value.get("operation_id"):
            return True
        return any(has_provider_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(has_provider_reference(item) for item in value)
    return False


def validate_request_hashes(values: Sequence[str]) -> tuple[str, ...]:
    """Validate a bounded canonical-hash lookup and return stable unique values."""
    if not 1 <= len(values) <= 256:
        raise ValueError("request_hashes must contain between 1 and 256 items")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or _REQUEST_HASH_PATTERN.fullmatch(value) is None:
            raise ValueError("each request_hash must be a lowercase 64-character SHA-256 hex value")
        normalized.append(value)
    return tuple(dict.fromkeys(normalized))


def has_submission_outcome_unknown(result: Any, error_code: str | None = None) -> bool:
    if error_code == "submission_outcome_unknown":
        return True
    if isinstance(result, dict):
        return any(
            (key == "error_code" and value == "submission_outcome_unknown")
            or has_submission_outcome_unknown(value)
            for key, value in result.items()
        )
    if isinstance(result, list):
        return any(has_submission_outcome_unknown(value) for value in result)
    return False


def is_fully_completed_arsenkin_batch(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    groups = result.get("groups")
    summary = result.get("summary")
    if not isinstance(groups, list) or not groups or not isinstance(summary, dict):
        return False
    if not all(
        isinstance(group, dict)
        and group.get("status") == "completed"
        and bool(group.get("provider_task_id"))
        and group.get("result") is not None
        and not has_submission_outcome_unknown(group)
        for group in groups
    ):
        return False
    return summary == {
        "queued": 0,
        "waiting": 0,
        "completed": len(groups),
        "failed": 0,
        "cancelled": 0,
    }


@dataclass(slots=True)
class JobRecord:
    job_id: UUID
    principal_id: UUID | str
    provider: str
    operation: str
    payload: dict[str, Any]
    status: str = "queued"
    idempotency_key: str | None = None
    approval_id: UUID | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    next_attempt_at: datetime | None = None
    attempts: int = 0
    request_hash: str | None = None
    submission_outcome_unknown: bool = False


@dataclass(frozen=True, slots=True)
class JobRequestHashState:
    request_hash: str
    hash_kind: str
    status: str
    submission_outcome_unknown: bool


@dataclass(frozen=True, slots=True)
class JobRequestHashLookup:
    matches: tuple[JobRequestHashState, ...]
    unindexed_batch_job_count: int


@dataclass(slots=True)
class ApprovalRecord:
    approval_id: UUID
    principal_id: UUID | str
    provider: str
    operation: str
    request_hash: str
    estimated_cost: float
    budget_limit: float
    status: str = "prepared"
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=30))
    budget_unlimited: bool = False
    acceptance_type: str | None = None
    acceptance_origin: str | None = None
    standing_policy_id: str | None = None
