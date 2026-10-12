import json
import time

import httpx
import pytest
from fastmcp import Client
from test_runtime import FixtureHttp

from zai_arsenkin import __version__, update_check
from zai_arsenkin.server import create_server

TOOL = "arsenkin_check_update"
RELEASE = {
    "tag_name": "v99.0.0",
    "html_url": "https://github.com/zai-one/arsenkin-mcp/releases/tag/v99.0.0",
    "draft": False,
    "prerelease": False,
}


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.delenv(update_check.ENV_DISABLE, raising=False)


def no_network():
    raise AssertionError("network must not be used")


def test_newer_release_suggests_matching_install_command(tmp_path):
    cache = tmp_path / "update-check.json"
    result = update_check.check_update(cache, fetch=lambda: RELEASE, now=1000.0)
    assert result["update_available"] is True
    assert result["current_version"] == __version__
    assert result["latest_version"] == "99.0.0"
    assert result["release_url"] == RELEASE["html_url"]
    assert result["update_command"] == (
        'uv tool install --force "zai-arsenkin-mcp[standalone] @ https://github.com/zai-one/arsenkin-mcp/releases/'
        'download/v99.0.0/zai_arsenkin_mcp-99.0.0-py3-none-any.whl"'
    )
    assert result["auto_update"] is False
    assert json.loads(cache.read_text())["latest_version"] == "99.0.0"


def test_cache_is_reused_for_a_day_and_refreshed_after(tmp_path):
    cache = tmp_path / "update-check.json"
    update_check.check_update(cache, fetch=lambda: RELEASE, now=1000.0)
    cached = update_check.check_update(cache, fetch=no_network, now=1000.0 + 3600)
    assert cached["source"] == "cache" and cached["update_available"] is True
    forced_too_soon = update_check.check_update(cache, force=True, fetch=no_network, now=1100.0)
    assert forced_too_soon["source"] == "cache"
    current = {**RELEASE, "tag_name": f"v{__version__}"}
    refreshed = update_check.check_update(cache, fetch=lambda: current, now=1000.0 + 25 * 3600)
    assert refreshed["source"] == "github"
    assert refreshed["update_available"] is False and refreshed["update_command"] is None


def test_offline_or_rate_limited_fails_gracefully(tmp_path):
    def offline():
        raise httpx.ConnectError("offline")

    cache = tmp_path / "update-check.json"
    result = update_check.check_update(cache, fetch=offline, now=1000.0)
    assert result["status"] == "unavailable" and result["update_available"] is None
    assert result["error"] == "ConnectError"
    update_check.check_update(cache, fetch=lambda: RELEASE, now=1000.0)
    stale = update_check.check_update(cache, fetch=offline, now=1000.0 + 30 * 3600)
    assert stale["last_known"]["latest_version"] == "99.0.0"


@pytest.mark.parametrize(
    "payload",
    [
        {"tag_name": "nightly"},
        {**RELEASE, "prerelease": True},
        {**RELEASE, "tag_name": "v1.0.0; rm -rf /"},
    ],
)
def test_untrusted_release_metadata_is_rejected(tmp_path, payload):
    result = update_check.check_update(tmp_path / "c.json", fetch=lambda: payload, now=1.0)
    assert result["status"] == "unavailable"
    assert not (tmp_path / "c.json").exists()


def test_foreign_release_url_falls_back_to_repository(tmp_path):
    payload = {**RELEASE, "html_url": "https://evil.example/release"}
    result = update_check.check_update(tmp_path / "c.json", fetch=lambda: payload, now=1.0)
    assert result["release_url"] == "https://github.com/zai-one/arsenkin-mcp/releases"


def test_env_opt_out_disables_network_and_hint(tmp_path, monkeypatch):
    cache = tmp_path / "update-check.json"
    update_check.check_update(cache, fetch=lambda: RELEASE, now=time.time())
    monkeypatch.setenv(update_check.ENV_DISABLE, "1")
    result = update_check.check_update(cache, force=True, fetch=no_network)
    assert result["enabled"] is False and result["status"] == "disabled"
    assert update_check.cached_hint(cache) is None
    assert update_check.refresh_in_background(cache) is None


def test_version_parsing():
    assert update_check.parse_version("v0.10.0") > update_check.parse_version("0.9.9")
    assert update_check.parse_version("1.2") == update_check.parse_version("v1.2.0")
    assert update_check.parse_version("1.2.3-rc1") is None


def test_fetch_uses_public_api_with_timeout(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(200, json=RELEASE, request=httpx.Request("GET", url))

    monkeypatch.setattr(update_check.httpx, "get", fake_get)
    assert update_check.fetch_latest_release()["tag_name"] == "v99.0.0"
    assert seen["url"] == "https://api.github.com/repos/zai-one/arsenkin-mcp/releases/latest"
    assert seen["timeout"] <= 5 and "Authorization" not in seen["headers"]


async def test_tool_hint_and_diagnostics_use_cache_without_blocking(config, monkeypatch):
    cache = config.state_path.parent / update_check.CACHE_FILE
    update_check.check_update(cache, fetch=lambda: RELEASE, now=time.time())
    monkeypatch.setattr(update_check, "fetch_latest_release", no_network)
    server = create_server(config, transport="stdio", http_factory=FixtureHttp)
    assert "99.0.0 is available" in server.instructions
    assert TOOL in server.instructions
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools[TOOL].annotations.readOnlyHint is True
        result = (await client.call_tool(TOOL, {})).data
        diagnostics = (await client.call_tool("arsenkin_local_diagnostics", {})).data
    assert result["update_available"] is True and result["source"] == "cache"
    assert "99.0.0 is available" in diagnostics["update_available"]
    assert FixtureHttp.calls == []


def test_no_hint_without_cache(config):
    assert create_server(config, transport="stdio").instructions is None
