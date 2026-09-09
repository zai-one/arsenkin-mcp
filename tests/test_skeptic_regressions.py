from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import UUID

import pytest
from test_runtime import PAYLOAD, FixtureHttp, due, queued

from zai_arsenkin.policy_errors import BudgetDenied
from zai_arsenkin.runtime import Runtime
from zai_arsenkin.worker import Worker


async def test_failed_settlement_does_not_erase_second_group_checkpoint(config, monkeypatch):
    runtime = Runtime(config, "stdio", FixtureHttp)
    requests = [
        {"client_ref": str(depth), "profile": "check-top", "payload": {**PAYLOAD, "depth": depth}}
        for depth in [10, 20]
    ]
    approval = await runtime.jobs.prepare_arsenkin_batch(config.principal_id, requests, 100)
    identifier = UUID(approval["approval_id"])
    assert runtime.store.accept(identifier, principal=config.principal_id, digest=approval["request_hash"])
    job = await runtime.jobs.submit_arsenkin_batch(config.principal_id, requests, identifier, "batch")
    for _ in range(2):
        due(runtime.store)
        assert await Worker(runtime).run_once()
    original = runtime.store.settle_job
    fail = True

    def transient_lock(*args, **kwargs):
        nonlocal fail
        if fail:
            fail = False
            raise sqlite3.OperationalError("synthetic database is locked")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.store, "settle_job", transient_lock)
    due(runtime.store)
    assert await Worker(runtime).run_once()
    current = await runtime.store.get_job(job.job_id, config.principal_id)
    assert [group.get("provider_task_id") for group in current.result["groups"]] == ["task-1", "task-2"]
    due(runtime.store)
    assert await Worker(runtime).run_once()
    assert FixtureHttp.calls.count("set") == 2
    assert (await runtime.store.get_job(job.job_id, config.principal_id)).status == "completed"


async def test_month_is_selected_after_sqlite_write_lock_is_acquired(config, monkeypatch):
    runtime, job = await queued(config)
    claim = runtime.store.claim_job()
    assert claim is not None
    with runtime.store.connect() as db:
        db.execute("UPDATE cost_reservations SET month='2026-09'")
        db.execute(
            "INSERT INTO cost_reservations VALUES(?,?,?,?,?,0)",
            (config.account_id, "october-other", "other", "2026-10", 100_000_000),
        )
    acquired = False

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (
                datetime(2026, 10, 1, tzinfo=UTC)
                if acquired
                else datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)
            )

    connect = runtime.store.connect

    class LockBoundary:
        def __init__(self):
            self.inner = connect()

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def execute(self, sql, *args):
            nonlocal acquired
            result = self.inner.execute(sql, *args)
            if sql == "BEGIN IMMEDIATE":
                acquired = True
            return result

    monkeypatch.setattr("zai_arsenkin.state.datetime", Clock)
    monkeypatch.setattr(runtime.store, "connect", LockBoundary)
    with pytest.raises(BudgetDenied):
        runtime.store.dispatch_intent(job.job_id, claim[1], None)
    with connect() as db:
        assert db.execute("SELECT stage FROM jobs").fetchone()[0] == "queued"


@pytest.mark.parametrize("operation", ["admission", "dispatch"])
async def test_month_change_inside_transaction_rolls_back_budget_decision(config, monkeypatch, operation):
    runtime = Runtime(config, "stdio", FixtureHttp)
    approval = await runtime.jobs.prepare_arsenkin(config.principal_id, "check-top", PAYLOAD, 100)
    identifier = UUID(approval["approval_id"])
    assert runtime.store.accept(identifier, principal=config.principal_id, digest=approval["request_hash"])
    if operation == "dispatch":
        job = await runtime.jobs.submit_arsenkin(config.principal_id, "check-top", PAYLOAD, identifier, "one")
        due(runtime.store)
        claim = runtime.store.claim_job()
        assert claim
    calls = 0

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            nonlocal calls
            calls += 1
            return datetime(2026, 9 if calls == 1 else 10, 1, tzinfo=UTC)

    monkeypatch.setattr("zai_arsenkin.state.datetime", Clock)
    with pytest.raises(BudgetDenied, match="month changed"):
        if operation == "dispatch":
            runtime.store.dispatch_intent(job.job_id, claim[1], None)
        else:
            await runtime.jobs.submit_arsenkin(config.principal_id, "check-top", PAYLOAD, identifier, "one")
    with runtime.store.connect() as db:
        row = db.execute("SELECT status FROM approvals").fetchone()
        assert row[0] == ("consumed" if operation == "dispatch" else "accepted")
        if operation == "dispatch":
            assert db.execute("SELECT stage FROM jobs").fetchone()[0] == "queued"
        else:
            assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM cost_reservations").fetchone()[0] == 0
