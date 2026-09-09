from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_arsenkin.config import ServiceConfig
from zai_arsenkin.runtime import AdmittedHttpClient, Runtime
from zai_arsenkin.server import create_server
from zai_arsenkin.state import StateStore
from zai_arsenkin.transport import ProviderRateLimited, ProviderTimeoutError
from zai_arsenkin.worker import Worker

PAYLOAD = {"queries": ["alpha"], "se": [{"type": 0, "region": 225}], "depth": 10}


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(
        state_path=tmp_path / "state.sqlite",
        token="synthetic",
        principal_monthly_cost_limit=100,
        account_monthly_cost_limit=100,
    )


class FixtureHttp(AdmittedHttpClient):
    calls: list[str] = []
    failure: Exception | None = None

    async def request(self, method, url, **kwargs):
        await self._admit_attempt(1)
        action = url.rsplit("/", 1)[-1]
        self.calls.append(action)
        if action == "info":
            return {"code": "TASKS_LIST", "result": []}
        if action == "set":
            if self.failure:
                raise self.failure
            return {"task_id": f"task-{self.calls.count('set')}"}
        if action == "check":
            return {"status": "done"}
        if action == "get":
            return {"rows": [{"query": "alpha"}]}
        if action == "tasks":
            return {"status": "cancelled"}
        raise AssertionError(action)


@pytest.fixture(autouse=True)
def reset_http():
    FixtureHttp.calls = []
    FixtureHttp.failure = None


def due(store):
    with store.connect() as db:
        db.execute("UPDATE jobs SET next_attempt=0 WHERE account=?", (store.account,))


async def queued(config, *, batch=False):
    runtime = Runtime(config, "stdio", FixtureHttp)
    actor = config.principal_id
    if batch:
        requests = [
            {"profile": "check-top", "payload": PAYLOAD, "client_ref": "row-a"},
            {"profile": "check-top", "payload": PAYLOAD, "client_ref": "row-b"},
        ]
        approval = await runtime.jobs.prepare_arsenkin_batch(actor, requests, 100)
    else:
        approval = await runtime.jobs.prepare_arsenkin(actor, "check-top", PAYLOAD, 100)
    assert runtime.store.accept(
        UUID(approval["approval_id"]), principal=actor, digest=approval["request_hash"]
    )
    if batch:
        job = await runtime.jobs.submit_arsenkin_batch(
            actor, requests, UUID(approval["approval_id"]), "key-1"
        )
    else:
        job = await runtime.jobs.submit_arsenkin(
            actor, "check-top", PAYLOAD, UUID(approval["approval_id"]), "key-1"
        )
    due(runtime.store)
    return runtime, job


async def test_all_original_schemas_descriptions_and_no_public_acceptance(config):
    expected = json.loads((Path(__file__).parents[1] / "contracts/arsenkin.json").read_text())["tools"]
    async with Client(create_server(config, transport="stdio", http_factory=FixtureHttp)) as client:
        actual = {
            tool.name: {
                "inputSchema": tool.inputSchema,
                "outputSchema": tool.outputSchema,
                "description": tool.description,
            }
            for tool in await client.list_tools()
        }
        assert {name: actual[name] for name in expected} == expected
        assert set(actual) - set(expected) == {
            "arsenkin_export_result",
            "arsenkin_profile_catalog",
            "arsenkin_local_diagnostics",
            "arsenkin_result_table",
        }
        prepared = (
            await client.call_tool("arsenkin_prepare", {"profile": "check-top", "payload": PAYLOAD})
        ).data
        with pytest.raises(ToolError):
            await client.call_tool(
                "arsenkin_submit",
                {
                    "profile": "check-top",
                    "payload": PAYLOAD,
                    "approval_id": prepared["approval_id"],
                    "idempotency_key": "client-key",
                },
            )
    assert FixtureHttp.calls == []


@pytest.mark.parametrize("batch", [False, True])
async def test_worker_checkpoint_restart_polls_once_and_preserves_batch_lineage(config, batch):
    runtime, job = await queued(config, batch=batch)
    assert await Worker(runtime).run_once()
    current = await runtime.store.get_job(job.job_id, config.principal_id)
    assert current.status == "waiting"
    due(runtime.store)
    restarted = Runtime(config, "stdio", FixtureHttp)
    assert await Worker(restarted).run_once()
    current = await restarted.store.get_job(job.job_id, config.principal_id)
    assert current.status == "completed"
    assert FixtureHttp.calls == ["info", "set", "check", "get"]
    if batch:
        assert current.result["groups"][0]["client_refs"] == ["row-a", "row-b"]


@pytest.mark.parametrize("batch", [False, True])
async def test_unknown_submission_is_not_retried_or_refunded(config, batch):
    runtime, job = await queued(config, batch=batch)
    FixtureHttp.failure = ProviderTimeoutError("synthetic lost response")
    assert await Worker(runtime).run_once()
    current = await runtime.store.get_job(job.job_id, config.principal_id)
    assert current.status == "failed"
    assert current.error_code == "submission_outcome_unknown"
    due(runtime.store)
    assert not await Worker(Runtime(config, "stdio", FixtureHttp)).run_once()
    assert FixtureHttp.calls.count("set") == 1
    with runtime.store.connect() as db:
        assert db.execute("SELECT released FROM cost_reservations").fetchone()[0] == 0


@pytest.mark.parametrize("batch", [False, True])
async def test_explicit_429_can_retry_same_job_without_second_approval(config, batch):
    runtime, job = await queued(config, batch=batch)
    FixtureHttp.failure = ProviderRateLimited("synthetic 429", retry_after_seconds=120)
    assert await Worker(runtime).run_once()
    current = await runtime.store.get_job(job.job_id, config.principal_id)
    assert current.status == "waiting"
    assert current.error_code == "provider_rate_limited"
    FixtureHttp.failure = None
    due(runtime.store)
    with runtime.store.connect() as db:
        db.execute("UPDATE cooldowns SET until_time=0")
    assert await Worker(runtime).run_once()
    assert FixtureHttp.calls.count("set") == 2
    with runtime.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM cost_reservations").fetchone()[0] == 1


async def test_account_actor_ownership_and_hash_lookup(config):
    runtime, job = await queued(config)
    actor = config.principal_id
    again = await runtime.jobs.submit_arsenkin(actor, "check-top", PAYLOAD, job.approval_id, "key-1")
    assert again.job_id == job.job_id
    with pytest.raises(PermissionError):
        await runtime.jobs.submit_arsenkin(
            actor, "check-top", {**PAYLOAD, "depth": 20}, job.approval_id, "key-1"
        )
    assert await runtime.store.get_job(job.job_id, "other") is None
    assert not await runtime.store.cancel_job(job.job_id, "other")
    assert await StateStore(replace(config, account_id="other-account")).get_job(job.job_id, actor) is None
    assert not (
        await runtime.store.lookup_job_request_hashes("other", "arsenkin", (job.request_hash,))
    ).matches
    lookup = await runtime.store.lookup_job_request_hashes(actor, "arsenkin", (job.request_hash,))
    assert {item.hash_kind for item in lookup.matches} == {"job", "child"}


async def test_cancel_before_dispatch_never_calls_provider_and_releases_once(config):
    runtime, job = await queued(config)
    assert await runtime.store.cancel_job(job.job_id, config.principal_id)
    assert await Worker(runtime).run_once()
    assert (await runtime.store.get_job(job.job_id, config.principal_id)).status == "cancelled"
    assert FixtureHttp.calls == []
    assert not await Worker(runtime).run_once()
    with runtime.store.connect() as db:
        assert db.execute("SELECT released FROM cost_reservations").fetchone()[0] == 1


async def test_reduced_current_budget_blocks_queued_dispatch(config):
    runtime, job = await queued(config)
    disabled = Runtime(replace(config, account_monthly_cost_limit=0), "stdio", FixtureHttp)
    assert await Worker(disabled).run_once()
    current = await runtime.store.get_job(job.job_id, config.principal_id)
    assert (current.status, current.error_code) == ("waiting", "budget_denied")
    assert FixtureHttp.calls == ["info"]


@pytest.mark.parametrize("after_checkpoint", [False, True])
async def test_crash_between_post_and_settlement_keeps_durable_outcome(config, monkeypatch, after_checkpoint):
    runtime, job = await queued(config)
    original = runtime.store.checkpoint

    def crash(*args, **kwargs):
        if after_checkpoint:
            original(*args, **kwargs)
        raise asyncio.CancelledError()

    monkeypatch.setattr(runtime.store, "checkpoint", crash)
    with pytest.raises(asyncio.CancelledError):
        await Worker(runtime).run_once()
    with runtime.store.connect() as db:
        db.execute("UPDATE jobs SET lease_until=0,next_attempt=0")
    restarted = Runtime(config, "stdio", FixtureHttp)
    await Worker(restarted).run_once()
    current = await restarted.store.get_job(job.job_id, config.principal_id)
    assert current.status == ("completed" if after_checkpoint else "failed")
    if not after_checkpoint:
        assert current.error_code == "submission_outcome_unknown"
    assert FixtureHttp.calls.count("set") == 1
