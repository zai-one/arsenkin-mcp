"""Account-bound SQLite approvals, paid-job admission and durable dispatch ownership."""

from __future__ import annotations

import copy
import json
import sqlite3
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from zai_arsenkin.adapter import build_batch_plan, build_payload
from zai_arsenkin.adapter import child_request_hashes as payload_child_hashes
from zai_arsenkin.config import ServiceConfig
from zai_arsenkin.models import (
    ApprovalRecord,
    JobRecord,
    JobRequestHashLookup,
    JobRequestHashState,
    has_provider_reference,
    has_submission_outcome_unknown,
    validate_request_hashes,
)
from zai_arsenkin.policy_errors import BudgetDenied
from zai_arsenkin.transport import request_hash

TERMINAL = {"completed", "failed", "cancelled"}


def units(value: float) -> int:
    amount = Decimal(str(value)) * 1_000_000
    if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
        raise ValueError("finite nonnegative cost with at most six decimals required")
    return int(amount)


def _provider_references(result: dict[str, Any] | None) -> set[tuple[str, str]]:
    if not isinstance(result, dict):
        return set()
    references = set()
    if result.get("provider_task_id"):
        references.add(("single", str(result["provider_task_id"])))
    for group in result.get("groups", []):
        if isinstance(group, dict) and group.get("provider_task_id"):
            references.add((str(group["group_id"]), str(group["provider_task_id"])))
    return references


def _same_month(month: str) -> None:
    if datetime.now(UTC).strftime("%Y-%m") != month:
        raise BudgetDenied("month changed during budget transaction; retry required")


class StateStore:
    def __init__(self, config: ServiceConfig):
        self.config, self.account, self.path = config, config.account_id, config.state_path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS approvals(
                    account TEXT NOT NULL, id TEXT NOT NULL, actor TEXT NOT NULL,
                    operation TEXT NOT NULL, digest TEXT NOT NULL, cost INTEGER NOT NULL,
                    budget INTEGER NOT NULL, unlimited INTEGER NOT NULL, expires REAL NOT NULL,
                    status TEXT NOT NULL, acceptance_type TEXT, acceptance_origin TEXT, policy TEXT,
                    PRIMARY KEY(account,id));
                CREATE TABLE IF NOT EXISTS state_events(
                    account TEXT NOT NULL, object_id TEXT NOT NULL, event TEXT NOT NULL,
                    origin TEXT NOT NULL, at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cost_reservations(
                    account TEXT NOT NULL, id TEXT NOT NULL, actor TEXT NOT NULL,
                    month TEXT NOT NULL, amount INTEGER NOT NULL, released INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(account,id));
                CREATE TABLE IF NOT EXISTS jobs(
                    account TEXT NOT NULL, id TEXT NOT NULL, actor TEXT NOT NULL,
                    operation TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, approval_id TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, error_code TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL, next_attempt REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT 'queued',
                    owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(account,id), UNIQUE(account,actor,operation,idempotency_key),
                    UNIQUE(account,approval_id));
                CREATE TABLE IF NOT EXISTS job_hashes(
                    account TEXT NOT NULL, job_id TEXT NOT NULL, digest TEXT NOT NULL, kind TEXT NOT NULL,
                    PRIMARY KEY(account,job_id,digest,kind));
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def _event(self, db: sqlite3.Connection, identifier: str, event: str, origin: str) -> None:
        db.execute(
            "INSERT INTO state_events VALUES(?,?,?,?,?)",
            (self.account, identifier, event, origin, time.time()),
        )

    @staticmethod
    def _approval(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            UUID(row["id"]),
            row["actor"],
            "arsenkin",
            row["operation"],
            row["digest"],
            row["cost"] / 1_000_000,
            row["budget"] / 1_000_000,
            row["status"],
            datetime.fromtimestamp(row["expires"], UTC),
            bool(row["unlimited"]),
            row["acceptance_type"],
            row["acceptance_origin"],
            row["policy"],
        )

    async def create_approval(self, approval: ApprovalRecord) -> None:
        if approval.provider != "arsenkin" or approval.status != "prepared":
            raise PermissionError("only unaccepted Arsenkin drafts may be created")
        if approval.operation not in {"submit", "submit_batch"}:
            raise ValueError("unsupported paid operation")
        validate_request_hashes((approval.request_hash,))
        with self.connect() as db:
            db.execute(
                "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,'prepared',NULL,NULL,NULL)",
                (
                    self.account,
                    str(approval.approval_id),
                    str(approval.principal_id),
                    approval.operation,
                    approval.request_hash,
                    units(approval.estimated_cost),
                    units(approval.budget_limit),
                    int(approval.budget_unlimited),
                    approval.expires_at.timestamp(),
                ),
            )
            self._event(db, str(approval.approval_id), "prepared", "mcp-prepare")

    async def get_approval(self, approval_id: UUID) -> ApprovalRecord | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM approvals WHERE account=? AND id=?", (self.account, str(approval_id))
            ).fetchone()
        return self._approval(row) if row else None

    def _paid_gate(self, row: sqlite3.Row, *, automatic_draft: bool = False) -> bool:
        cap = units(self.config.arsenkin_max_cost_per_approval)
        if row["cost"] <= 0 or (cap and row["cost"] > cap):
            return False
        if row["unlimited"]:
            return self.config.arsenkin_auto_paid_policy == "unlimited" and (
                automatic_draft
                or (
                    row["acceptance_type"] == "standing_policy_child"
                    and row["acceptance_origin"] == "server"
                    and row["policy"] == self.config.arsenkin_standing_policy_id
                )
            )
        principal, account = (
            units(self.config.principal_monthly_cost_limit),
            units(self.config.account_monthly_cost_limit),
        )
        return bool(principal and account and row["cost"] <= min(principal, account, row["budget"]))

    def accept(self, approval_id: UUID, *, principal: str, digest: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM approvals WHERE account=? AND id=?", (self.account, str(approval_id))
            ).fetchone()
            if (
                not row
                or row["actor"] != principal
                or row["digest"] != digest
                or row["unlimited"]
                or row["status"] != "prepared"
                or row["expires"] <= time.time()
                or not self._paid_gate(row)
            ):
                return False
            db.execute(
                "UPDATE approvals SET status='accepted',acceptance_type='human',"
                "acceptance_origin='operator-cli' WHERE account=? AND id=?",
                (self.account, str(approval_id)),
            )
            self._event(db, str(approval_id), "accepted", "operator-cli")
            return True

    async def accept_approval(
        self, approval_id: UUID, *, acceptance_type: str, acceptance_origin: str, standing_policy_id: str
    ) -> bool:
        # Only the package JobService calls this trusted server-policy route.
        if (
            acceptance_type != "standing_policy_child"
            or acceptance_origin != "server"
            or standing_policy_id != self.config.arsenkin_standing_policy_id
        ):
            return False
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM approvals WHERE account=? AND id=?", (self.account, str(approval_id))
            ).fetchone()
            if (
                not row
                or not row["unlimited"]
                or row["status"] != "prepared"
                or row["expires"] <= time.time()
                or not self._paid_gate(row, automatic_draft=True)
            ):
                return False
            db.execute(
                "UPDATE approvals SET status='accepted',acceptance_type=?,acceptance_origin=?,policy=? "
                "WHERE account=? AND id=?",
                (acceptance_type, acceptance_origin, standing_policy_id, self.account, str(approval_id)),
            )
            self._event(db, str(approval_id), "accepted", "standing-policy:" + standing_policy_id)
            return True

    @staticmethod
    def _job(row: sqlite3.Row) -> JobRecord:
        result = json.loads(row["result"]) if row["result"] else None
        return JobRecord(
            UUID(row["id"]),
            row["actor"],
            "arsenkin",
            row["operation"],
            json.loads(row["payload"]),
            row["status"],
            row["idempotency_key"],
            UUID(row["approval_id"]),
            result,
            row["error_code"],
            datetime.fromtimestamp(row["created"], UTC),
            datetime.fromtimestamp(row["updated"], UTC),
            datetime.fromtimestamp(row["next_attempt"], UTC),
            row["attempts"],
            row["digest"],
            has_submission_outcome_unknown(result, row["error_code"]),
        )

    async def get_job(self, job_id: UUID, principal: Any) -> JobRecord | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE account=? AND id=? AND actor=?",
                (self.account, str(job_id), str(principal)),
            ).fetchone()
        return self._job(row) if row else None

    def _budget(self, db: sqlite3.Connection, row: sqlite3.Row, *, extra: int, month: str) -> None:
        if not self._paid_gate(row):
            raise BudgetDenied("current server policy denies paid dispatch")
        if row["unlimited"]:
            return
        total, own = db.execute(
            "SELECT COALESCE(SUM(amount),0),COALESCE(SUM(CASE WHEN actor=? "
            "THEN amount ELSE 0 END),0) FROM cost_reservations "
            "WHERE account=? AND month=? AND released=0",
            (row["actor"], self.account, month),
        ).fetchone()
        if total + extra > units(self.config.account_monthly_cost_limit) or own + extra > units(
            self.config.principal_monthly_cost_limit
        ):
            raise BudgetDenied("monthly paid budget exhausted")

    async def admit_paid_job(
        self,
        job: JobRecord,
        *,
        approval_operation: str,
        request_hash: str,
        child_request_hashes: tuple[str, ...] = (),
        backlog_limit: int = 50,
    ) -> JobRecord:
        if job.provider != "arsenkin" or not job.approval_id or not job.idempotency_key:
            raise ValueError("Arsenkin approval and idempotency key required")
        if len(job.idempotency_key) > 128:
            raise ValueError("idempotency key too long")
        if job.operation == "submit_batch":
            plan = build_batch_plan(
                job.payload["batch_plan"]["requests"], self.config.arsenkin_cost_per_query
            )
            if plan != job.payload["batch_plan"]:
                raise ValueError("batch plan changed")
            digest, expected_operation = plan["request_hash"], "submit_batch"
            expected_children = payload_child_hashes(plan["requests"])
        elif job.operation == "submit:" + str(job.payload.get("profile")):
            digest = hash_payload(build_payload(job.payload["profile"], job.payload["payload"]))
            expected_operation = "submit"
            expected_children = (digest,)
        else:
            raise ValueError("unsupported job operation")
        if digest != request_hash or approval_operation != expected_operation:
            raise PermissionError("approval operation or request hash changed")
        children = validate_request_hashes(child_request_hashes) if child_request_hashes else ()
        if set(children) != set(expected_children):
            raise PermissionError("child request hashes changed")
        actor, identifier = str(job.principal_id), str(job.job_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            month = datetime.now(UTC).strftime("%Y-%m")
            consumed = db.execute(
                "SELECT * FROM jobs WHERE account=? AND actor=? AND approval_id=?",
                (self.account, actor, str(job.approval_id)),
            ).fetchone()
            if consumed:
                if (
                    consumed["idempotency_key"] != job.idempotency_key
                    or consumed["operation"] != job.operation
                    or consumed["digest"] != digest
                ):
                    raise PermissionError("approval already consumed by another request or key")
                return self._job(consumed)
            existing = db.execute(
                "SELECT * FROM jobs WHERE account=? AND actor=? AND operation=? AND idempotency_key=?",
                (self.account, actor, job.operation, job.idempotency_key),
            ).fetchone()
            if existing:
                if existing["approval_id"] != str(job.approval_id):
                    raise PermissionError("idempotency key belongs to another approval")
                if existing["digest"] != digest or existing["operation"] != job.operation:
                    raise PermissionError("idempotency key belongs to another request")
                return self._job(existing)
            row = db.execute(
                "SELECT * FROM approvals WHERE account=? AND id=?", (self.account, str(job.approval_id))
            ).fetchone()
            if (
                not row
                or row["actor"] != actor
                or row["operation"] != approval_operation
                or row["digest"] != digest
                or row["status"] != "accepted"
                or row["expires"] <= now
            ):
                raise PermissionError("accepted matching unexpired approval required")
            incomplete = db.execute(
                "SELECT 1 FROM jobs j WHERE j.account=? AND j.operation='submit_batch' "
                "AND NOT EXISTS (SELECT 1 FROM job_hashes h WHERE h.account=j.account "
                "AND h.job_id=j.id AND h.kind='child') LIMIT 1",
                (self.account,),
            ).fetchone()
            if incomplete:
                raise PermissionError("request hash coverage is incomplete for batch jobs")
            paid_hashes = (digest, *children)
            placeholders = ",".join("?" for _ in paid_hashes)
            duplicate = db.execute(
                f"SELECT 1 FROM job_hashes WHERE account=? AND digest IN ({placeholders}) LIMIT 1",
                (self.account, *paid_hashes),
            ).fetchone()
            if duplicate:
                raise PermissionError("request hash guard denied a duplicate Arsenkin paid job")
            count = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE account=? AND status NOT IN "
                "('completed','failed','cancelled')",
                (self.account,),
            ).fetchone()[0]
            if count >= min(backlog_limit, self.config.arsenkin_server_queue_target):
                raise PermissionError("durable backlog full")
            self._budget(db, row, extra=row["cost"], month=month)
            db.execute(
                "INSERT INTO cost_reservations VALUES(?,?,?,?,?,0)",
                (self.account, identifier, actor, month, row["cost"]),
            )
            db.execute(
                "UPDATE approvals SET status='consumed' WHERE account=? AND id=?",
                (self.account, str(job.approval_id)),
            )
            due = job.next_attempt_at.timestamp() if job.next_attempt_at else now
            db.execute(
                "INSERT INTO jobs(account,id,actor,operation,payload,digest,idempotency_key,approval_id,"
                "status,created,updated,next_attempt) VALUES(?,?,?,?,?,?,?,?,'queued',?,?,?)",
                (
                    self.account,
                    identifier,
                    actor,
                    job.operation,
                    json.dumps(job.payload),
                    digest,
                    job.idempotency_key,
                    str(job.approval_id),
                    now,
                    now,
                    due,
                ),
            )
            for value, kind in [(digest, "job"), *((value, "child") for value in children)]:
                db.execute(
                    "INSERT OR IGNORE INTO job_hashes VALUES(?,?,?,?)",
                    (self.account, identifier, value, kind),
                )
            self._event(db, str(job.approval_id), "consumed", "job-admission")
            self._event(db, identifier, "queued", "job-admission")
            _same_month(month)
            return self._job(
                db.execute(
                    "SELECT * FROM jobs WHERE account=? AND id=?", (self.account, identifier)
                ).fetchone()
            )

    async def lookup_job_request_hashes(
        self, principal: Any, provider: str, hashes: tuple[str, ...]
    ) -> JobRequestHashLookup:
        if provider != "arsenkin":
            raise ValueError("unsupported provider")
        checked = set(validate_request_hashes(hashes))
        with self.connect() as db:
            rows = db.execute(
                "SELECT h.digest,h.kind,j.status,j.result,j.error_code FROM job_hashes h "
                "JOIN jobs j ON j.account=h.account AND j.id=h.job_id "
                "WHERE j.account=? AND j.actor=?",
                (self.account, str(principal)),
            ).fetchall()
            incomplete = db.execute(
                "SELECT COUNT(*) FROM jobs j WHERE j.account=? AND j.actor=? AND j.operation='submit_batch' "
                "AND NOT EXISTS (SELECT 1 FROM job_hashes h WHERE h.account=j.account "
                "AND h.job_id=j.id AND h.kind='child')",
                (self.account, str(principal)),
            ).fetchone()[0]
        return JobRequestHashLookup(
            tuple(
                JobRequestHashState(
                    row["digest"],
                    row["kind"],
                    row["status"],
                    has_submission_outcome_unknown(
                        json.loads(row["result"]) if row["result"] else None, row["error_code"]
                    ),
                )
                for row in rows
                if row["digest"] in checked
            ),
            incomplete,
        )

    async def cancel_job(self, job_id: UUID, principal: Any) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "UPDATE jobs SET cancel_requested=1,next_attempt=?,updated=?,"
                "status=CASE WHEN lease_until>? THEN status ELSE 'cancel_requested' END "
                "WHERE account=? AND id=? AND actor=? AND status NOT IN "
                "('completed','failed','cancelled')",
                (time.time(), time.time(), time.time(), self.account, str(job_id), str(principal)),
            )
            if result.rowcount:
                self._event(db, str(job_id), "cancel_requested", "mcp-cancel")
            return bool(result.rowcount)

    def claim_job(self) -> tuple[JobRecord, str] | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            expired = db.execute(
                "SELECT * FROM jobs WHERE account=? AND owner IS NOT NULL AND lease_until<=?",
                (self.account, now),
            ).fetchall()
            for row in expired:
                unknown = row["stage"] == "dispatching"
                result = json.loads(row["result"]) if row["result"] else {}
                if unknown:
                    result["submission_outcome_unknown"] = True
                status = "failed" if unknown else "cancel_requested" if row["cancel_requested"] else "waiting"
                db.execute(
                    "UPDATE jobs SET status=?,result=?,error_code=?,owner=NULL,lease_until=0,"
                    "updated=?,next_attempt=? WHERE account=? AND id=?",
                    (
                        status,
                        json.dumps(result) if result else None,
                        "submission_outcome_unknown" if unknown else row["error_code"],
                        now,
                        now,
                        self.account,
                        row["id"],
                    ),
                )
                self._event(db, row["id"], "recovered_unknown" if unknown else "recovered", "worker")
            # Serialize worker cycles per account, including status -> paid submit.
            # Other MCP reads retain their own bounded admission.
            if db.execute(
                "SELECT 1 FROM jobs WHERE account=? AND lease_until>?", (self.account, now)
            ).fetchone():
                return None
            row = db.execute(
                "SELECT * FROM jobs WHERE account=? AND status IN "
                "('queued','waiting','cancel_requested') AND next_attempt<=? "
                "ORDER BY cancel_requested DESC,created LIMIT 1",
                (self.account, now),
            ).fetchone()
            if not row:
                return None
            owner = uuid4().hex
            db.execute(
                "UPDATE jobs SET owner=?,lease_until=?,attempts=attempts+1,status='running',updated=? "
                "WHERE account=? AND id=?",
                (owner, now + 90, now, self.account, row["id"]),
            )
            job = self._job(row)
            job.attempts += 1
            if row["cancel_requested"]:
                job.status = "cancel_requested"
            self._event(db, row["id"], "claimed", "worker")
            return job, owner

    def _owned(self, db: sqlite3.Connection, job_id: UUID, owner: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM jobs WHERE account=? AND id=? AND owner=? AND lease_until>?",
            (self.account, str(job_id), owner, time.time()),
        ).fetchone()
        if not row:
            raise PermissionError("job claim expired")
        return row

    def dispatch_intent(self, job_id: UUID, owner: str, before: dict[str, Any] | None) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            month = datetime.now(UTC).strftime("%Y-%m")
            job = self._owned(db, job_id, owner)
            if job["cancel_requested"]:
                raise PermissionError("cancellation requested before paid dispatch")
            row = db.execute(
                "SELECT * FROM approvals WHERE account=? AND id=? AND status='consumed'",
                (self.account, job["approval_id"]),
            ).fetchone()
            reserve = db.execute(
                "SELECT * FROM cost_reservations WHERE account=? AND id=? AND released=0",
                (self.account, str(job_id)),
            ).fetchone()
            if not row or not reserve or row["actor"] != job["actor"]:
                raise BudgetDenied("consumed approval and reservation required")
            self._budget(db, row, extra=reserve["amount"] if reserve["month"] != month else 0, month=month)
            db.execute(
                "UPDATE cost_reservations SET month=? WHERE account=? AND id=?",
                (month, self.account, str(job_id)),
            )
            db.execute(
                "UPDATE jobs SET stage='dispatching',result=?,updated=? WHERE account=? AND id=?",
                (json.dumps(before) if before else None, time.time(), self.account, str(job_id)),
            )
            self._event(db, str(job_id), "dispatch_intent", "worker")
            _same_month(month)

    def checkpoint(self, job_id: UUID, owner: str, result: dict[str, Any] | None, stage: str) -> None:
        if stage not in {"queued", "polling"}:
            raise ValueError("invalid checkpoint stage")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._owned(db, job_id, owner)
            db.execute(
                "UPDATE jobs SET result=?,stage=?,updated=? WHERE account=? AND id=?",
                (json.dumps(result) if result else None, stage, time.time(), self.account, str(job_id)),
            )
            self._event(db, str(job_id), "checkpoint:" + stage, "worker")

    def settle_job(
        self,
        job_id: UUID,
        owner: str,
        *,
        status: str,
        result: dict[str, Any] | None,
        error: str | None = None,
        delay: int = 60,
        preserve_checkpoint: bool = False,
    ) -> None:
        if status not in TERMINAL | {"waiting", "cancel_requested"}:
            raise ValueError("invalid job outcome")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned(db, job_id, owner)
            previous = json.loads(row["result"]) if row["result"] else None
            if preserve_checkpoint:
                # The fallback must read the latest result under the same write
                # lock; a worker's claimed copy predates paid-submit checkpoints.
                result = previous
            elif status != "completed" and _provider_references(previous) - _provider_references(result):
                result = previous
            if row["stage"] == "dispatching":
                result = copy.deepcopy(result or previous or {})
                result["submission_outcome_unknown"] = True
                status, error = "failed", "submission_outcome_unknown"
            if row["cancel_requested"] and status not in TERMINAL:
                status, delay = "cancel_requested", 0
            if (
                status in {"failed", "cancelled"}
                and not has_provider_reference(result)
                and not has_provider_reference(previous)
                and not has_submission_outcome_unknown(result, error)
            ):
                db.execute(
                    "UPDATE cost_reservations SET released=1 WHERE account=? AND id=?",
                    (self.account, str(job_id)),
                )
            now = time.time()
            db.execute(
                "UPDATE jobs SET status=?,result=?,error_code=?,owner=NULL,lease_until=0,"
                "updated=?,next_attempt=? WHERE account=? AND id=?",
                (
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    now,
                    now + max(0, delay),
                    self.account,
                    str(job_id),
                ),
            )
            self._event(db, str(job_id), "settled:" + status, "worker")


hash_payload = request_hash
