import csv
import io
import json
from dataclasses import replace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from test_runtime import FixtureHttp, due, queued

from zai_arsenkin.config import ServiceConfig
from zai_arsenkin.export import MAX_BYTES, export_result
from zai_arsenkin.server import create_server
from zai_arsenkin.worker import Worker

JOB = {
    "job_id": "fixture-id",
    "status": "completed",
    "result": {
        "client_ref": "row-a",
        "items": [{"query": "кофе", "region": 213, "url": "https://example.com"}],
        "a/b~c": "=1+1",
    },
}


def test_export_retains_unicode_client_ref_and_unambiguous_json_pointers():
    result = export_result(JOB)
    rows = [json.loads(line) for line in result["content"].splitlines()]
    values = {row["pointer"]: json.loads(row["value_json"]) for row in rows}
    assert values["/client_ref"] == "row-a"
    assert values["/items/0/query"] == "кофе"
    assert values["/a~1b~0c"] == "=1+1"
    assert all(row["job_id"] == JOB["job_id"] for row in rows)
    csv_rows = list(csv.DictReader(io.StringIO(export_result(JOB, "csv")["content"])))
    assert {row["pointer"]: json.loads(row["value_json"]) for row in csv_rows} == values
    assert not any(cell.startswith(("=", "+", "-", "@")) for row in csv_rows for cell in row.values())


def test_result_and_byte_limits_are_explicit_and_pending_is_unavailable():
    result = export_result(JOB, max_rows=1)
    assert result["truncated"] and result["row_count"] == 1
    large = export_result({**JOB, "result": {"field": "x" * MAX_BYTES}})
    assert large["truncated"] and len(large["content"].encode()) < MAX_BYTES
    assert export_result({"job_id": "pending", "status": "running"})["available"] is False


@pytest.mark.parametrize("format,rows", [("html", 2), ("csv", 0), ("jsonl", 1001), ("jsonl", True)])
def test_export_rejects_unbounded_and_unsupported_arguments(format, rows):
    with pytest.raises(ValueError):
        export_result(JOB, format, rows)


async def test_saved_batch_export_never_resubmits_and_is_owned(tmp_path):
    config = ServiceConfig(
        state_path=tmp_path / "jobs.sqlite",
        token="synthetic",
        principal_monthly_cost_limit=100,
        account_monthly_cost_limit=100,
    )
    FixtureHttp.calls, FixtureHttp.failure = [], None
    runtime, job = await queued(config, batch=True)
    await Worker(runtime).run_once()
    due(runtime.store)
    await Worker(runtime).run_once()
    calls = list(FixtureHttp.calls)
    async with Client(create_server(config, transport="stdio", http_factory=FixtureHttp)) as client:
        first = (await client.call_tool("arsenkin_export_result", {"job_id": str(job.job_id)})).data
        second = (await client.call_tool("arsenkin_export_result", {"job_id": str(job.job_id)})).data
    assert first == second and first["available"]
    assert "row-a" in first["content"] and "row-b" in first["content"]
    assert FixtureHttp.calls == calls
    async with Client(
        create_server(replace(config, principal_id="other"), transport="stdio", http_factory=FixtureHttp)
    ) as client:
        with pytest.raises(ToolError):
            await client.call_tool("arsenkin_export_result", {"job_id": str(job.job_id)})
    assert FixtureHttp.calls == calls
