from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from test_runtime import FixtureHttp

from zai_arsenkin.projects import REPORTS, ArsenkinProjectsAdapter, projects_request
from zai_arsenkin.server import create_server


class ProjectsHttp(FixtureHttp):
    seen: list[tuple[str, str, dict, dict]] = []

    async def request(self, method, url, **kwargs):
        if "/api/projects/v1/" not in url:
            return await super().request(method, url, **kwargs)
        await self._admit_attempt(1)
        ProjectsHttp.seen.append((method, url, kwargs.get("params"), kwargs.get("headers")))
        return {"data": [{"id": 1}], "meta": {"current_page": 1}}


@pytest.fixture(autouse=True)
def reset_projects():
    ProjectsHttp.seen = []


def test_projects_requests_are_documented_get_paths():
    assert projects_request("projects", per_page=15, domain="example.com") == (
        "/projects",
        {"domain": "example.com", "per_page": 15},
    )
    path, params = projects_request(
        "positions",
        7,
        project_region_id=5,
        keyword_ids=[3, 1, 3],
        start_date="2026-09-01",
        end_date="2026-09-30",
        position_from=1,
        position_to=10,
        sort="-scan_date",
    )
    assert path == "/projects/7/positions"
    assert params["keyword_ids"] == "3,1" and params["sort"] == "-scan_date"
    assert projects_request("chart_median_position", 7, project_region_id=5, group_id=2)[0] == (
        "/projects/7/charts/median-position"
    )
    assert all(template.startswith("/projects") for template, _, _ in REPORTS.values())
    assert not any("run" in template for template, _, _ in REPORTS.values())


@pytest.mark.parametrize(
    "report,project_id,kwargs",
    [
        ("positions_run", 7, {}),
        ("project", None, {}),
        ("projects", 7, {}),
        ("regions", 7, {"search": "x"}),
        ("chart_visibility", 7, {}),
        ("positions", 7, {"start_date": "2026-10-02", "end_date": "2026-10-01"}),
        ("positions", 7, {"position_from": 20, "position_to": 10}),
        ("positions", 7, {"position_to": 101}),
        ("positions", 7, {"sort": "position"}),
        ("positions", 7, {"keyword_ids": list(range(1, 502))}),
        ("keywords", 7, {"per_page": 501}),
        ("groups", 7, {"per_page": 201}),
        ("project", True, {}),
    ],
)
def test_invalid_projects_requests_fail_locally(report, project_id, kwargs):
    with pytest.raises(ValueError):
        projects_request(report, project_id, **kwargs)


async def test_projects_adapter_uses_documented_token_header():
    class Http:
        async def request(self, method, url, **kwargs):
            Http.args = (method, url, kwargs)
            return {"data": []}

    adapter = ArsenkinProjectsAdapter("synthetic", http=Http())
    await adapter.get("regions", 9)
    method, url, kwargs = Http.args
    assert method == "GET" and url == "https://arsenkin.ru/api/projects/v1/projects/9/regions"
    assert kwargs["headers"]["X-Api-Token"] == "synthetic" and kwargs["idempotent"] is True


async def test_projects_and_limits_tools_are_read_only(config):
    server = create_server(config, transport="stdio", http_factory=ProjectsHttp)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        for name in ("arsenkin_projects", "arsenkin_limits", "arsenkin_status"):
            assert tools[name].annotations.readOnlyHint is True
        assert tools["arsenkin_submit"].annotations.destructiveHint is True
        result = (
            await client.call_tool(
                "arsenkin_projects", {"report": "chart_visibility", "project_id": 7, "project_region_id": 5}
            )
        ).data
        assert result["data"] == [{"id": 1}]
        with pytest.raises(ToolError):
            await client.call_tool("arsenkin_projects", {"report": "chart_visibility", "project_id": 7})
        limits = (await client.call_tool("arsenkin_limits", {})).data
        assert limits["code"] == "TASKS_LIST"
    assert [(m, u.rsplit("/v1", 1)[1], p) for m, u, p, _ in ProjectsHttp.seen] == [
        ("GET", "/projects/7/charts/visibility", {"project_region_id": 5})
    ]
    assert ProjectsHttp.calls == ["info"]


def test_new_project_reads_and_additive_writes():
    from zai_arsenkin.projects import projects_write_request

    assert projects_request("group", 7, group_id=3) == ("/projects/7/groups/3", {})
    path, params = projects_request("positions", 7, project_region_ids=[5, 6], with_snippets=True)
    assert params == {"project_region_ids": "5,6", "with_snippets": 1}
    assert projects_request("keywords", 7, tag_ids=[1], tag_mode="exclude")[1] == {
        "tag_ids": "1",
        "tag_mode": "exclude",
    }
    assert projects_write_request("add_keywords", 7, keywords=["a", "a", "b"], group_id=2) == (
        "POST",
        "/projects/7/keywords",
        {"keywords": ["a", "b"], "group_id": 2},
    )
    assert projects_write_request("run_positions", 7)[1] == "/projects/7/positions/run"
    with pytest.raises(ValueError):
        projects_write_request("create_project", domain="not a url")
    with pytest.raises(ValueError):
        projects_write_request("delete_project", 7)


async def test_projects_write_previews_until_confirmed(config, monkeypatch):
    monkeypatch.setenv("ARSENKIN_TOOLSETS", "all")
    server = create_server(config, transport="stdio", http_factory=ProjectsHttp)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools["arsenkin_projects_write"].annotations.destructiveHint is False
        preview = (
            await client.call_tool("arsenkin_projects_write", {"action": "run_positions", "project_id": 7})
        ).data
        assert preview["needs_confirmation"] is True and ProjectsHttp.seen == []
        await client.call_tool(
            "arsenkin_projects_write", {"action": "run_positions", "project_id": 7, "confirm": True}
        )
    assert [(m, u.rsplit("/v1", 1)[1]) for m, u, _, _ in ProjectsHttp.seen] == [
        ("POST", "/projects/7/positions/run")
    ]
