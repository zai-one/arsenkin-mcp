from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from test_runtime import PAYLOAD, FixtureHttp

from zai_arsenkin.config import SCOPES, ServiceConfig
from zai_arsenkin.runtime import Runtime
from zai_arsenkin.server import create_server
from zai_arsenkin.state import StateStore


@pytest.fixture(scope="session")
def pair():
    return RSAKeyPair.generate()


def token(pair, *, scopes=SCOPES, actor="alice", account="default", **claims):
    return pair.create_token(
        subject=actor,
        issuer="arsenkin-operator",
        audience="arsenkin-mcp",
        scopes=list(scopes),
        expires_in_seconds=60,
        additional_claims={"account_id": account, **claims},
    )


@asynccontextmanager
async def connection(server, bearer):
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    async with (
        app.router.lifespan_context(app),
        Client(
            StreamableHttpTransport("http://fixture/mcp", auth=bearer, httpx_client_factory=factory)
        ) as client,
    ):
        yield client


async def test_http_auth_scopes_account_and_untrusted_financial_claims(config, pair):
    settings = replace(
        config, public_key=pair.public_key, principal_monthly_cost_limit=0, account_monthly_cost_limit=0
    )
    server = create_server(settings, http_factory=FixtureHttp)
    async with connection(server, token(pair, scopes=["arsenkin:read"])) as client:
        listed = {tool.name for tool in await client.list_tools()}
        assert "arsenkin_prepare" not in listed
        assert "arsenkin_status" in listed
        with pytest.raises(ToolError):
            await client.call_tool("arsenkin_prepare", {"profile": "check-top", "payload": PAYLOAD})
    async with connection(server, token(pair, account="other")) as client:
        with pytest.raises(ToolError):
            await client.call_tool("arsenkin_status", {})
    async with connection(
        server,
        token(
            pair, monthly_cost_limit=99999, monthly_cost_unlimited=True, arsenkin_auto_paid_policy="unlimited"
        ),
    ) as client:
        prepared = (
            await client.call_tool("arsenkin_prepare", {"profile": "check-top", "payload": PAYLOAD})
        ).data
        assert not prepared["can_accept"] and not prepared["standing_unlimited"]
    assert FixtureHttp.calls == []
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code in {401, 403}


async def test_http_approval_and_job_remain_bound_to_original_actor(config, pair):
    settings = replace(config, public_key=pair.public_key)
    server = create_server(settings, http_factory=FixtureHttp)
    async with connection(server, token(pair)) as client:
        draft = (
            await client.call_tool("arsenkin_prepare", {"profile": "check-top", "payload": PAYLOAD})
        ).data
    assert StateStore(settings).accept(
        UUID(draft["approval_id"]), principal="alice", digest=draft["request_hash"]
    )
    arguments = {
        "profile": "check-top",
        "payload": PAYLOAD,
        "approval_id": draft["approval_id"],
        "idempotency_key": "owned",
    }
    async with connection(server, token(pair, actor="bob")) as client:
        with pytest.raises(ToolError):
            await client.call_tool("arsenkin_submit", arguments)
    async with connection(server, token(pair)) as client:
        submitted = (await client.call_tool("arsenkin_submit", arguments)).data
    async with connection(server, token(pair, actor="bob")) as client:
        with pytest.raises(ToolError):
            await client.call_tool("arsenkin_get_result", {"job_id": submitted["job_id"]})
        assert (await client.call_tool("arsenkin_cancel", {"job_id": submitted["job_id"]})).data[
            "cancel_requested"
        ] is False
    assert FixtureHttp.calls == []


async def test_rate_limit_and_audit_survive_new_runtime(config):
    settings = replace(config, request_rate_limit=1, principal_rate_limit=1)
    for iteration in range(2):
        async with Client(create_server(settings, transport="stdio", http_factory=FixtureHttp)) as client:
            if iteration:
                with pytest.raises(ToolError, match="provider_rate_limited"):
                    await client.call_tool("arsenkin_status", {})
            else:
                await client.call_tool("arsenkin_status", {})
    assert FixtureHttp.calls == ["info"]
    with StateStore(settings).connect() as db:
        assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
        assert [row[0] for row in db.execute("SELECT outcome FROM calls ORDER BY started")] == [
            "success",
            "error",
        ]


@pytest.mark.parametrize("deadline", [False, True])
async def test_no_detached_http_after_cancellation_or_deadline(config, monkeypatch, deadline):
    entered, released = asyncio.Event(), asyncio.Event()

    class SlowHttp(FixtureHttp):
        async def request(self, *args, **kwargs):
            await self._admit_attempt(1)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()

    runtime = Runtime(config, "stdio", SlowHttp)
    if deadline:
        monkeypatch.setattr("zai_arsenkin.runtime.OPERATION_TIMEOUT_SECONDS", 0.04)
    task = asyncio.create_task(
        runtime.execute("arsenkin_status", {}, lambda: runtime.registry.arsenkin().status())
    )
    await entered.wait()
    if not deadline:
        task.cancel()
    with pytest.raises(ToolError if deadline else asyncio.CancelledError):
        await task
    assert released.is_set()
    with runtime.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0


async def test_real_stdio_process_discovers_and_estimates_without_credentials(tmp_path):
    env = {
        "ARSENKIN_STATE_PATH": str(tmp_path / "stdio.sqlite"),
        "ARSENKIN_TOKEN_FILE": "",
        "ARSENKIN_AUTO_PAID_POLICY": "manual",
        "ARSENKIN_PRINCIPAL_MONTHLY_COST_LIMIT": "0",
        "ARSENKIN_ACCOUNT_MONTHLY_COST_LIMIT": "0",
        "ARSENKIN_DISABLE_UPDATE_CHECK": "1",
    }
    async with Client(StdioTransport(sys.executable, ["-m", "zai_arsenkin"], env=env)) as client:
        assert len(await client.list_tools()) == 18
        prepared = (
            await client.call_tool("arsenkin_prepare", {"profile": "check-top", "payload": PAYLOAD})
        ).data
        assert not prepared["can_accept"] and prepared["live_call"] is False


def test_http_requires_key_and_invalid_costs_are_rejected(tmp_path, config):
    with pytest.raises(ValueError):
        create_server(ServiceConfig(state_path=tmp_path / "no-key.sqlite"))
    for value in [float("nan"), float("inf"), -1, True]:
        with pytest.raises(ValueError):
            replace(config, account_monthly_cost_limit=value)
