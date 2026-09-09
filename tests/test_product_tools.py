from __future__ import annotations

import csv
import io
import json
from dataclasses import replace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from test_runtime import FixtureHttp, queued

from zai_arsenkin.adapter import PROFILES, build_payload
from zai_arsenkin.catalog import EXAMPLES, profile_catalog
from zai_arsenkin.config import ServiceConfig
from zai_arsenkin.result_tables import at_pointer, result_table
from zai_arsenkin.runtime import Runtime
from zai_arsenkin.server import create_server
from zai_arsenkin.state import StateStore
from zai_arsenkin.worker import Worker

JOB = {
    "job_id": "synthetic-job",
    "status": "completed",
    "updated_at": "2026-09-09T00:00:00Z",
    "result": {
        "result": {"result": {"repeatUrl": {"https://example.test/a": 2, "https://example.test/b": 0}}}
    },
}
POINTER = "/result/result/repeatUrl"


def test_catalog_is_complete_examples_validate_and_returns_copies():
    first = profile_catalog()
    assert {row["profile"] for row in first["profiles"]} == set(PROFILES)
    assert first["provider_calls"] == 0
    for name, payload in EXAMPLES.items():
        assert build_payload(name, payload)["data"]["queries"]
    first["profiles"][0]["defaults"]["depth"] = 999
    assert profile_catalog()["profiles"][0]["defaults"]["depth"] == 10


def test_serp_table_preserves_provider_counts_and_json_pointer_provenance():
    result = result_table(JOB, "serp_urls", POINTER)
    assert result["selected_view_complete"] and result["row_count"] == 2
    assert result["provider_completeness"] == "not_asserted"
    assert result["rows"][1]["occurrences"] == 0
    for row in result["rows"]:
        assert at_pointer(JOB["result"], row["pointer"]) == row["occurrences"]
    csv_rows = list(csv.DictReader(io.StringIO(result_table(JOB, "serp_urls", POINTER, "csv")["content"])))
    assert csv_rows[0]["value"] == "https://example.test/a"
    assert csv_rows[1]["occurrences"] == "0"


def test_cluster_records_missing_fields_unicode_and_formula_strings():
    job = {**JOB, "result": [{"clustered": "=SUM(1,2)", "words": ["кофе", "чай"], "wssumm": 0}]}
    result = result_table(job, "clusters", format="csv")
    row = next(csv.DictReader(io.StringIO(result["content"])))
    assert row["clustered"] == "'=SUM(1,2)"
    assert json.loads(row["words"]) == ["кофе", "чай"]
    assert row["wssumm"] == "0" and row["ws"] == "" and row["topurl"] == ""
    assert result["selected_view_complete"]


@pytest.mark.parametrize("pointer", ["/missing", "/a~2b", "missing", "/-1", "/00", "/999", "/" * 26])
def test_pointer_rejects_ambiguous_paths(pointer):
    with pytest.raises(ValueError):
        at_pointer([1], pointer)


def test_pointer_escapes_and_batch_group_path():
    data = {"groups": [{"result": {"a/b~c": [1]}}]}
    assert at_pointer(data, "/groups/0/result/a~1b~0c/0") == 1


@pytest.mark.parametrize(
    "value,view",
    [
        (["bad"], "clusters"),
        ([{"clustered": "x", "words": "bad"}], "clusters"),
        ({"https://example.test": -1}, "serp_urls"),
        ({"https://example.test": True}, "serp_urls"),
        ({"https://example.test": None}, "serp_urls"),
    ],
)
def test_unknown_result_shape_does_not_silently_create_empty_success(value, view):
    with pytest.raises(ValueError):
        result_table({**JOB, "result": value}, view)


def test_limits_pending_and_unselected_data_are_explicit():
    limited = result_table(JOB, "serp_urls", POINTER, max_rows=1)
    assert not limited["selected_view_complete"] and limited["stop_reason"] == "row_limit"
    large = result_table({**JOB, "result": {"x" * 200000: 1}}, "serp_urls")
    assert large["stop_reason"] == "byte_limit" and not large["selected_view_complete"]
    assert result_table({"job_id": "pending", "status": "waiting"}, "serp_urls")["available"] is False
    with pytest.raises(ValueError):
        result_table(JOB, "serp_urls", POINTER, max_rows=1001)


async def test_table_from_owned_durable_result_never_dispatches_and_redacts_before_csv(tmp_path):
    secret = 'synthetic"credential'
    config = ServiceConfig(
        state_path=tmp_path / "state.sqlite",
        token=secret,
        principal_monthly_cost_limit=100,
        account_monthly_cost_limit=100,
    )
    runtime, job = await queued(config)
    with runtime.store.connect() as db:
        db.execute(
            "UPDATE jobs SET status=?,result=? WHERE account=? AND id=?",
            (
                "completed",
                json.dumps({"clusters": [{"clustered": secret, "words": [secret]}]}),
                config.account_id,
                str(job.job_id),
            ),
        )
    FixtureHttp.calls = []
    args = dict(job_id=str(job.job_id), view="clusters", result_pointer="/clusters", format="csv")
    first = None
    for _ in range(2):
        async with Client(create_server(config, transport="stdio", http_factory=FixtureHttp)) as client:
            result = (await client.call_tool("arsenkin_result_table", args)).data
        assert first is None or result == first
        first = result
        row = next(csv.DictReader(io.StringIO(result["content"])))
        assert row["clustered"] == "***redacted***"
        assert json.loads(row["words"]) == ["***redacted***"]
    for changed in [replace(config, principal_id="other"), replace(config, account_id="other")]:
        async with Client(create_server(changed, transport="stdio", http_factory=FixtureHttp)) as client:
            with pytest.raises(ToolError):
                await client.call_tool("arsenkin_result_table", args)
    assert FixtureHttp.calls == []


async def test_diagnostics_worker_heartbeat_restart_budget_and_actor_boundary(tmp_path):
    config = ServiceConfig(
        state_path=tmp_path / "state.sqlite",
        token="synthetic",
        principal_monthly_cost_limit=100,
        account_monthly_cost_limit=100,
    )
    runtime, job = await queued(config)
    before = runtime.store.diagnostics(config.principal_id)
    assert before["worker"]["state"] == "not_seen"
    assert before["own_jobs_by_status"] == {"queued": 1}
    assert runtime.store.diagnostics("other")["own_jobs_by_status"] == {}
    runtime.store.heartbeat()
    store = StateStore(config)
    assert store.diagnostics(config.principal_id)["worker"]["state"] == "recent"
    with store.connect() as db:
        db.execute("UPDATE worker_health SET last_cycle=last_cycle-121")
    assert store.diagnostics(config.principal_id)["worker"]["state"] == "stale"
    blank = replace(config, state_path=tmp_path / "empty.sqlite")
    empty_runtime = Runtime(blank, "stdio", FixtureHttp)
    FixtureHttp.calls = []
    assert await Worker(empty_runtime).run_once() is False
    assert empty_runtime.store.diagnostics(config.principal_id)["worker"]["state"] == "recent"
    assert not FixtureHttp.calls


async def test_catalog_and_diagnostics_use_read_scope_and_make_no_provider_call(tmp_path):
    config = ServiceConfig(state_path=tmp_path / "state.sqlite", token="synthetic")
    FixtureHttp.calls = []
    async with Client(create_server(config, transport="stdio", http_factory=FixtureHttp)) as client:
        assert (await client.call_tool("arsenkin_profile_catalog", {})).data["profiles"]
        diagnostic = (await client.call_tool("arsenkin_local_diagnostics", {})).data
        assert diagnostic["provider_connectivity"] == "not_checked"
        assert "configure_paid_limits_before_prepare" in diagnostic["next_steps"]
    assert not FixtureHttp.calls
    denied = replace(config, local_scopes=frozenset({"arsenkin:execute"}))
    async with Client(create_server(denied, transport="stdio", http_factory=FixtureHttp)) as client:
        with pytest.raises(ToolError):
            await client.call_tool("arsenkin_local_diagnostics", {})


def test_concurrent_heartbeat_is_not_misreported_as_clock_skew(tmp_path, monkeypatch):
    from contextlib import contextmanager

    import zai_arsenkin.state as state

    store = StateStore(ServiceConfig(state_path=tmp_path / "race.sqlite"))
    original = store.connect
    clock = [100.0]
    monkeypatch.setattr(state.time, "time", lambda: clock[0])

    @contextmanager
    def concurrent_connection():
        with original() as db:
            clock[0] += 0.001
            db.execute("INSERT INTO worker_health VALUES(?,?)", (store.account, clock[0]))
            yield db

    monkeypatch.setattr(store, "connect", concurrent_connection)
    result = store.diagnostics("local-operator")
    assert result["worker"]["state"] == "recent"
    assert "start_worker_with_the_same_config" not in result["next_steps"]
