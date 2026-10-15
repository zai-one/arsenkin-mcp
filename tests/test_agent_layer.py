from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from zai_arsenkin.agent_layer import DESCRIPTION_LIMIT, INSTRUCTIONS_LIMIT
from zai_arsenkin.agent_profile import PROFILE
from zai_arsenkin.config import ServiceConfig
from zai_arsenkin.server import create_server

ROOT = Path(__file__).resolve().parents[1]
TOOLS_LIST_BUDGET = 24_000


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(state_path=tmp_path / "state.sqlite", token="synthetic")


async def test_token_budget_and_agent_surface(config):
    async with Client(create_server(config, transport="stdio")) as client:
        assert len(client.initialize_result.instructions or "") <= INSTRUCTIONS_LIMIT
        tools = await client.list_tools()
        default = {n for ts, group in PROFILE.toolsets.items() if ts not in PROFILE.optin for n in group}
        assert {tool.name for tool in tools} == default
        for tool in tools:
            assert tool.title and len(tool.description or "") <= DESCRIPTION_LIMIT, tool.name
        assert len(json.dumps([tool.model_dump(exclude_none=True) for tool in tools])) <= TOOLS_LIST_BUDGET
        uris = {str(item.uri) for item in await client.list_resources()}
        assert {"arsenkin://playbook", "arsenkin://toolsets", "arsenkin://guide/tools"} <= uris
        assert "clustering" in (await client.read_resource("arsenkin://guide/clustering-params"))[0].text
        assert {p.name for p in await client.list_prompts()} == {
            "cluster_keywords",
            "index_check",
            "text_relevance",
            "semantic_core",
        }


async def test_read_only_mode_drops_paid_and_approval_tools(config, monkeypatch):
    monkeypatch.setenv("ARSENKIN_READ_ONLY", "1")
    async with Client(create_server(config, transport="stdio")) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert not names & {"arsenkin_submit", "arsenkin_batch_submit", "arsenkin_prepare", "arsenkin_cancel"}
    assert {"arsenkin_estimate", "arsenkin_get_result", "arsenkin_projects"} <= names


def test_guide_covers_every_profile():
    from zai_arsenkin.adapter import PROFILES
    from zai_arsenkin.agent_layer import guide_topics

    table = guide_topics(PROFILE)["tools"]
    assert all(f"`{name}`" in table for name in PROFILES)


def test_static_budget_script_passes():
    sys.path.insert(0, str(ROOT / "scripts"))
    import token_budget

    assert token_budget.problems() == []
