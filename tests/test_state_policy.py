from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from test_runtime import PAYLOAD, FixtureHttp, due, queued

from zai_arsenkin.config import ServiceConfig
from zai_arsenkin.jobs import JobService
from zai_arsenkin.runtime import Runtime
from zai_arsenkin.state import StateStore
from zai_arsenkin.worker import Worker


async def test_account_budget_race_preserves_losing_approval(config):
    limited = replace(config, account_monthly_cost_limit=4)
    store = StateStore(limited)
    jobs = JobService(store, 4)
    approvals = []
    for actor in ["one", "two"]:
        value = await jobs.prepare_arsenkin(actor, "check-top", {**PAYLOAD, "queries": [actor]}, 100)
        identifier = UUID(value["approval_id"])
        assert store.accept(identifier, principal=actor, digest=value["request_hash"])
        approvals.append(identifier)
    barrier = threading.Barrier(2)

    def submit(actor, approval):
        isolated = JobService(StateStore(limited), 4)
        barrier.wait(timeout=10)
        try:
            return asyncio.run(
                isolated.submit_arsenkin(actor, "check-top", {**PAYLOAD, "queries": [actor]}, approval, actor)
            )
        except PermissionError:
            return None

    outcomes = await asyncio.gather(
        *[
            asyncio.to_thread(submit, actor, approval)
            for actor, approval in zip(["one", "two"], approvals, strict=True)
        ]
    )
    assert sum(value is not None for value in outcomes) == 1
    with store.connect() as db:
        assert [row[0] for row in db.execute("SELECT status FROM approvals ORDER BY status")] == [
            "accepted",
            "consumed",
        ]
        assert db.execute("SELECT SUM(amount) FROM cost_reservations").fetchone()[0] == 4_000_000


async def test_backlog_rejection_does_not_consume_or_reserve(config):
    limited = replace(config, arsenkin_server_queue_target=1)
    runtime, first = await queued(limited)
    payload = {**PAYLOAD, "queries": ["second"]}
    approval = await runtime.jobs.prepare_arsenkin(config.principal_id, "check-top", payload, 100)
    identifier = UUID(approval["approval_id"])
    assert runtime.store.accept(identifier, principal=config.principal_id, digest=approval["request_hash"])
    with pytest.raises(PermissionError, match="backlog"):
        await runtime.jobs.submit_arsenkin(config.principal_id, "check-top", payload, identifier, "second")
    assert (await runtime.store.get_approval(identifier)).status == "accepted"
    assert (await runtime.store.get_job(first.job_id, config.principal_id)).status == "queued"


@pytest.mark.parametrize("exhausted", [False, True])
async def test_month_rollover_rebooks_existing_reservation_before_post(config, exhausted):
    runtime, job = await queued(config)
    month = datetime.now(UTC).strftime("%Y-%m")
    with runtime.store.connect() as db:
        db.execute("UPDATE cost_reservations SET month='2000-01'")
        if exhausted:
            db.execute(
                "INSERT INTO cost_reservations VALUES(?,?,?,?,?,0)",
                (config.account_id, "other-reservation", "other", month, 100_000_000),
            )
    assert await Worker(runtime).run_once()
    current = await runtime.store.get_job(job.job_id, config.principal_id)
    assert current.error_code == ("budget_denied" if exhausted else None)
    assert FixtureHttp.calls.count("set") == (0 if exhausted else 1)
    with runtime.store.connect() as db:
        row = db.execute(
            "SELECT month,amount FROM cost_reservations WHERE id=?", (str(job.job_id),)
        ).fetchone()
        assert tuple(row) == ("2000-01" if exhausted else month, 4_000_000)


async def test_explicit_standing_policy_is_server_owned_and_rechecked(config):
    unlimited = replace(
        config,
        arsenkin_auto_paid_policy="unlimited",
        principal_monthly_cost_limit=0,
        account_monthly_cost_limit=0,
    )
    runtime = Runtime(unlimited, "stdio", FixtureHttp)
    approval = await runtime.jobs.prepare_arsenkin(
        config.principal_id,
        "check-top",
        PAYLOAD,
        0,
        auto_accept=True,
        standing_policy_id=unlimited.arsenkin_standing_policy_id,
    )
    assert approval["approval_status"] == "accepted"
    job = await runtime.jobs.submit_arsenkin(
        config.principal_id, "check-top", PAYLOAD, UUID(approval["approval_id"]), "auto"
    )
    due(runtime.store)
    revoked = Runtime(replace(unlimited, arsenkin_auto_paid_policy="manual"), "stdio", FixtureHttp)
    await Worker(revoked).run_once()
    assert (await runtime.store.get_job(job.job_id, config.principal_id)).error_code == "budget_denied"
    assert FixtureHttp.calls.count("set") == 0


async def test_worker_claims_are_serialized_across_instances(config):
    runtime, job = await queued(config)
    other = StateStore(config)
    first = runtime.store.claim_job()
    assert first is not None and first[0].job_id == job.job_id
    assert other.claim_job() is None
    with runtime.store.connect() as db:
        db.execute("UPDATE jobs SET lease_until=0")
    replacement = other.claim_job()
    assert replacement is not None and replacement[1] != first[1]
    with pytest.raises(PermissionError, match="expired"):
        runtime.store.dispatch_intent(job.job_id, first[1], None)


async def test_batch_multiple_groups_preserves_earlier_paid_reference(config):
    runtime = Runtime(config, "stdio", FixtureHttp)
    requests = [
        {"profile": "check-top", "payload": {**PAYLOAD, "depth": depth}, "client_ref": str(depth)}
        for depth in [10, 20]
    ]
    approval = await runtime.jobs.prepare_arsenkin_batch(config.principal_id, requests, 100)
    identifier = UUID(approval["approval_id"])
    assert runtime.store.accept(identifier, principal=config.principal_id, digest=approval["request_hash"])
    job = await runtime.jobs.submit_arsenkin_batch(config.principal_id, requests, identifier, "batch")
    for _ in range(4):
        due(runtime.store)
        assert await Worker(Runtime(config, "stdio", FixtureHttp)).run_once()
    current = await runtime.store.get_job(job.job_id, config.principal_id)
    assert current.status == "completed"
    assert [group["provider_task_id"] for group in current.result["groups"]] == ["task-1", "task-2"]
    assert [group["client_refs"] for group in current.result["groups"]] == [["10"], ["20"]]
    assert FixtureHttp.calls.count("set") == 2


def test_budget_environment_is_service_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("ARSENKIN_STATE_PATH", str(tmp_path / "state.sqlite"))
    monkeypatch.setenv("ARSENKIN_PRINCIPAL_MONTHLY_COST_LIMIT", "8")
    monkeypatch.setenv("ARSENKIN_ACCOUNT_MONTHLY_COST_LIMIT", "12")
    monkeypatch.setenv("ACCOUNT_MONTHLY_COST_LIMIT", "999")
    parsed = ServiceConfig.from_env()
    assert (parsed.principal_monthly_cost_limit, parsed.account_monthly_cost_limit) == (8, 12)
