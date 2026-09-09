from __future__ import annotations

import pytest

from zai_arsenkin.adapter import PROFILES, active_task_count, build_batch_plan, build_payload


def test_arsenkin_numeric_search_engine_type() -> None:
    with pytest.raises(ValueError, match=r"se\[\]\.type must be numeric"):
        build_payload(
            "positions",
            {"queries": ["mcp"], "url": "example.test", "se": [{"type": "1", "region": 225}], "format": 1},
        )


def test_parser_ads_allows_exactly_one_query() -> None:
    with pytest.raises(ValueError, match="hard maximum 1"):
        build_payload("parser-ads", {"queries": ["one", "two"], "regions": [225]})


def test_arsenkin_payload_uses_canonical_tools_api_envelope() -> None:
    assert build_payload("whois", {"queries": ["example.test"]}) == {
        "tools_name": "whois",
        "data": {"queries": ["example.test"]},
    }


def test_arsenkin_batch_plan_coalesces_compatible_queries_with_lineage() -> None:
    plan = build_batch_plan(
        [
            {
                "client_ref": "direction-a",
                "profile": "commerce",
                "payload": {"queries": ["alpha", "shared"], "se": 1, "region": 213},
            },
            {
                "client_ref": "direction-b",
                "profile": "commerce",
                "payload": {"queries": ["shared", "beta"], "se": 1, "region": 213},
            },
        ],
        4,
    )

    assert plan["input_request_count"] == 2
    assert plan["input_query_count"] == 4
    assert plan["provider_task_count"] == 1
    assert plan["provider_query_count"] == 3
    assert plan["deduplicated_query_count"] == 1
    assert plan["estimated_cost_units"] == 12
    group = plan["groups"][0]
    assert group["payload"]["queries"] == ["alpha", "shared", "beta"]
    assert group["client_refs"] == ["direction-a", "direction-b"]
    shared = next(item for item in group["query_owners"] if item["query"] == "shared")
    assert shared["client_refs"] == ["direction-a", "direction-b"]


def test_arsenkin_batch_plan_keeps_incompatible_parameters_separate() -> None:
    plan = build_batch_plan(
        [
            {
                "client_ref": "moscow",
                "profile": "commerce",
                "payload": {"queries": ["alpha"], "se": 1, "region": 213},
            },
            {
                "client_ref": "russia",
                "profile": "commerce",
                "payload": {"queries": ["beta"], "se": 1, "region": 225},
            },
        ],
        4,
    )

    assert plan["provider_task_count"] == 2
    assert {group["payload"]["region"] for group in plan["groups"]} == {213, 225}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"code": "TASKS_LIST", "result": []}, 0),
        ({"code": "TASKS_LIST", "result": [{"id": 1}, {"id": 2}]}, 2),
        ({"result": {"active_tasks": "3"}}, 3),
        ({"result": [{"status": "running"}, {"status": "done"}]}, 1),
        ({"unexpected": "shape"}, None),
    ],
)
def test_arsenkin_active_task_count_is_strict(payload: dict, expected: int | None) -> None:
    assert active_task_count(payload) == expected


def test_arsenkin_profile_registry_is_complete() -> None:
    assert len(PROFILES) == 19
    assert "parser-ads" in PROFILES
    assert PROFILES["parser-ads"].max_items["queries"] == 1
