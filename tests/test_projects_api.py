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
    # apidoc groups-list documents tag_ids (comma-separated) next to parent_id/search.
    assert projects_request("groups", 7, tag_ids=[3, 4])[1]["tag_ids"] == "3,4"
    with pytest.raises(ValueError):
        projects_write_request("create_project", domain="not a url")
    assert projects_write_request("delete_project", 7) == ("DELETE", "/projects/7", {})


def test_projects_api_edits_deletes_and_tags_follow_docs():
    from zai_arsenkin.projects import projects_write_request as w

    assert w("update_project", 7, name="N") == ("PUT", "/projects/7", {"name": "N"})
    assert w("delete_region", 7, target_id=3) == ("DELETE", "/projects/7/regions/3", {})
    assert w("delete_competitor", 7, target_id=4)[1] == "/projects/7/competitors/4"
    assert w("delete_keywords", 7, keyword_ids=[1, 1, 2]) == (
        "DELETE",
        "/projects/7/keywords",
        {"keyword_ids": [1, 2]},
    )
    assert w("delete_keywords", 7, keyword_ids=[1], hard_delete=True)[2]["hard_delete"] is True
    assert w("restore_keywords", 7, keyword_ids=[5]) == (
        "POST",
        "/projects/7/keywords/restore",
        {"keyword_ids": [5]},
    )
    assert w("update_group", 7, group_id=2, parent_id=0, active=False) == (
        "PUT",
        "/projects/7/groups/2",
        {"active": False, "parent_id": None},
    )
    assert w("delete_group", 7, group_id=2)[:2] == ("DELETE", "/projects/7/groups/2")
    assert w("create_tag", 7, name="P", color="#EF4444") == (
        "POST",
        "/projects/7/tags",
        {"name": "P", "color": "#EF4444"},
    )
    assert w("update_tag", 7, target_id=12, color="#22C55E")[:2] == ("PUT", "/projects/7/tags/12")
    assert w("delete_tag", 7, target_id=12)[:2] == ("DELETE", "/projects/7/tags/12")
    assert w("system_tag", 7) == ("POST", "/projects/7/tags/system", {})
    assert w("untag_groups", 7, group_ids=[1], tag_ids=[12]) == (
        "DELETE",
        "/projects/7/groups/tags",
        {"group_ids": [1], "tag_ids": [12]},
    )
    assert w("tag_keywords", 7, keyword_ids=[1], system_tag=True)[2] == {
        "keyword_ids": [1],
        "system_tag": True,
    }
    for bad in (
        ("update_group", {"group_id": 2}),
        ("create_tag", {"color": "#fff"}),
        ("tag_groups", {"group_ids": [1]}),
        ("delete_region", {}),
    ):
        with pytest.raises(ValueError):
            w(bad[0], 7, **bad[1])
    assert projects_request("groups", 7, with_children=False)[1] == {"with_children": 0}
    assert projects_request("tag", 7, tag_id=12) == ("/projects/7/tags/12", {})


async def test_projects_write_previews_until_confirmed(config, monkeypatch):
    monkeypatch.setenv("ARSENKIN_TOOLSETS", "all")
    monkeypatch.delenv("ARSENKIN_PROJECTS_PAID", raising=False)
    server = create_server(config, transport="stdio", http_factory=ProjectsHttp)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools["arsenkin_projects_write"].annotations.destructiveHint is True
        # Paid run_positions bypasses the approval ledger: the agent's confirm=true is not enough.
        blocked = (
            await client.call_tool(
                "arsenkin_projects_write", {"action": "run_positions", "project_id": 7, "confirm": True}
            )
        ).data
        assert blocked["code"] == "paid_disabled" and ProjectsHttp.seen == []
        monkeypatch.setenv("ARSENKIN_PROJECTS_PAID", "on")
        preview = (
            await client.call_tool("arsenkin_projects_write", {"action": "run_positions", "project_id": 7})
        ).data
        assert preview["needs_confirmation"] is True and ProjectsHttp.seen == []
        args = {"action": "run_positions", "project_id": 7, "confirm": True}
        ticket = (await client.call_tool("arsenkin_projects_write", args)).data
        assert ticket["needs_operator_approval"] is True and ProjectsHttp.seen == []
        refused = (
            await client.call_tool("arsenkin_projects_write", {**args, "approval_id": ticket["approval_id"]})
        ).data
        assert refused["code"] == "approval_invalid" and ProjectsHttp.seen == []
        from zai_arsenkin.project_approvals import ApprovalStore

        store = ApprovalStore(config.state_path.with_name("arsenkin-projects-approvals.json"))
        store.accept(ticket["approval_id"], ticket["digest"])
        other = {
            "action": "run_positions",
            "project_id": 8,
            "confirm": True,
            "approval_id": ticket["approval_id"],
        }
        assert (await client.call_tool("arsenkin_projects_write", other)).data["code"] == "approval_invalid"
        await client.call_tool("arsenkin_projects_write", {**args, "approval_id": ticket["approval_id"]})
        again = (
            await client.call_tool("arsenkin_projects_write", {**args, "approval_id": ticket["approval_id"]})
        ).data
        assert again["code"] == "approval_invalid"  # single use
    assert [(m, u.rsplit("/v1", 1)[1]) for m, u, _, _ in ProjectsHttp.seen] == [
        ("POST", "/projects/7/positions/run")
    ]


def test_concurrent_accept_cannot_resurrect_a_consumed_approval(tmp_path, monkeypatch):
    """Server and operator CLI share one file: a stale operator write must not re-arm a spent approval."""
    import threading
    import time

    from zai_arsenkin.project_approvals import ApprovalStore

    store = ApprovalStore(tmp_path / "arsenkin-projects-approvals.json")
    spent = {"project_id": 7, "method": "POST", "path": "/projects/7/positions/run", "body": {}}
    x = store.prepare("arsenkin_projects_write", spent, "POST run", "alice")
    y = store.prepare("arsenkin_projects_write", {"project_id": 8}, "DELETE", "alice")
    store.accept(x["approval_id"], x["digest"])
    loaded, original = threading.Event(), ApprovalStore._load

    def slow_load(self):
        data = original(self)
        if threading.current_thread().name == "operator":
            loaded.set()
            time.sleep(0.3)  # the operator CLI read is in flight while the server consumes
        return data

    monkeypatch.setattr(ApprovalStore, "_load", slow_load)
    operator = threading.Thread(target=store.accept, args=(y["approval_id"], y["digest"]), name="operator")
    operator.start()
    assert loaded.wait(5)
    store.consume(x["approval_id"], "arsenkin_projects_write", spent, "alice")
    operator.join()
    with pytest.raises(PermissionError):  # still single use: no second paid run
        store.consume(x["approval_id"], "arsenkin_projects_write", spent, "alice")
    assert store.pending() == []  # y was accepted, not lost


def test_projects_approval_is_bound_to_the_preparing_principal(tmp_path):
    from zai_arsenkin.project_approvals import ApprovalStore

    store = ApprovalStore(tmp_path / "a.json")
    request = {"project_id": 7, "method": "DELETE", "path": "/projects/7", "body": {}}
    ticket = store.prepare("arsenkin_projects_write", request, "DELETE /projects/7", "alice")
    store.accept(ticket["approval_id"], ticket["digest"])
    with pytest.raises(PermissionError):
        store.consume(ticket["approval_id"], "arsenkin_projects_write", request, "bob")
    store.consume(ticket["approval_id"], "arsenkin_projects_write", request, "alice")


async def test_projects_operator_command_works_from_any_directory(config, monkeypatch, tmp_path, capsys):
    import shlex
    from pathlib import Path

    from zai_arsenkin import project_approvals

    monkeypatch.setenv("ARSENKIN_TOOLSETS", "all")
    server = create_server(config, transport="stdio", http_factory=ProjectsHttp)
    async with Client(server) as client:
        args = {"action": "create_group", "project_id": 7, "name": "Диваны", "confirm": True}
        ticket = (await client.call_tool("arsenkin_projects_write", args)).data
        command = shlex.split(ticket["operator_command"])
        assert command[0] == "arsenkin-projects-approval"
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)  # operator shell: other cwd, no ARSENKIN_STATE_PATH
        default = Path("state/arsenkin-projects-approvals.json")
        path = command[command.index("--path") + 1]
        assert project_approvals.main(["--path", path, "--list"], default_path=default) == 0
        assert "Диваны" in capsys.readouterr().out  # the operator sees the exact change
        assert project_approvals.main(command[1:], default_path=default) == 0
        await client.call_tool("arsenkin_projects_write", {**args, "approval_id": ticket["approval_id"]})
    assert [(m, u.rsplit("/v1", 1)[1]) for m, u, _, _ in ProjectsHttp.seen] == [
        ("POST", "/projects/7/groups")
    ]
    assert not (elsewhere / "state").exists()
