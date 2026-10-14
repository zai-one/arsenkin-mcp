"""Arsenkin Projects API v1 (https://help.arsenkin.ru/projects-api).

Reads: documented GET endpoints. Writes (opt-in toolset projects_write): documented
create/update/delete/tag calls and the paid POST /positions/run, each behind a free preview,
confirm=true and a single-use operator approval (zai_arsenkin.project_approvals);
run_positions also needs the operator setting ARSENKIN_PROJECTS_PAID=on.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from zai_arsenkin.coalescing import AsyncSingleFlight
from zai_arsenkin.transport import JsonHttpClient, ProviderError, request_hash

PROJECTS_BASE_URL = "https://arsenkin.ru/api/projects/v1"
MAX_ID = 2_147_483_647
_CHART = frozenset({"project_region_id", "start_date", "end_date"})
# report -> (path template, accepted arguments, required arguments)
REPORTS: dict[str, tuple[str, frozenset[str], frozenset[str]]] = {
    "projects": ("/projects", frozenset({"status_name", "domain", "name", "per_page", "page"}), frozenset()),
    "project": ("/projects/{id}", frozenset(), frozenset()),
    "regions": ("/projects/{id}/regions", frozenset(), frozenset()),
    "competitors": ("/projects/{id}/competitors", frozenset(), frozenset()),
    "keywords": (
        "/projects/{id}/keywords",
        frozenset(
            {"group_id", "search", "per_page", "page", "tag_ids", "tag_mode", "without_tags", "is_deleted"}
        ),
        frozenset(),
    ),
    "group": ("/projects/{id}/groups/{group_id}", frozenset({"group_id"}), frozenset({"group_id"})),
    "tags": ("/projects/{id}/tags", frozenset({"search"}), frozenset()),
    "tag": ("/projects/{id}/tags/{tag_id}", frozenset({"tag_id"}), frozenset({"tag_id"})),
    "groups": (
        "/projects/{id}/groups",
        frozenset({"parent_id", "search", "with_children", "per_page", "page", "tag_ids"}),
        frozenset(),
    ),
    "positions": (
        "/projects/{id}/positions",
        frozenset(
            {
                "project_region_id",
                "project_region_ids",
                "keyword_ids",
                "group_id",
                "group_ids",
                "with_snippets",
                "start_date",
                "end_date",
                "position_from",
                "position_to",
                "sort",
            }
        ),
        frozenset(),
    ),
    "chart_visibility": ("/projects/{id}/charts/visibility", _CHART, frozenset({"project_region_id"})),
    "chart_top_statistics": (
        "/projects/{id}/charts/top-statistics",
        _CHART,
        frozenset({"project_region_id"}),
    ),
    "chart_average_position": (
        "/projects/{id}/charts/average-position",
        _CHART,
        frozenset({"project_region_id"}),
    ),
    "chart_median_position": (
        "/projects/{id}/charts/median-position",
        _CHART | {"group_id"},
        frozenset({"project_region_id"}),
    ),
}
# Documented pagination maxima: keywords 500, groups 200; projects default 15.
_PER_PAGE_MAX = {"projects": 100, "keywords": 500, "groups": 200}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _int(value: object, field: str, low: int = 1, high: int = MAX_ID) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{field} must be an integer between {low} and {high}")
    return value


def _text(value: object, field: str, maximum: int = 255) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    if not 1 <= len(text) <= maximum or _CONTROL.search(text):
        raise ValueError(f"{field} must contain 1-{maximum} printable characters")
    return text


def _day(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError(f"{field} must use YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD") from exc
    return value


def projects_request(
    report: str, project_id: int | None = None, **arguments: Any
) -> tuple[str, dict[str, Any]]:
    """Validate one Projects API read and return (path, query params) without network access."""
    if report not in REPORTS:
        raise ValueError("unsupported Arsenkin projects report")
    template, accepted, required = REPORTS[report]
    supplied = {key: value for key, value in arguments.items() if value is not None and value != []}
    unexpected = sorted(set(supplied) - accepted)
    if unexpected:
        raise ValueError(f"{report} does not accept: " + ", ".join(unexpected))
    missing = sorted(required - set(supplied))
    if missing:
        raise ValueError(f"{report} requires: " + ", ".join(missing))
    for sub in ("group_id", "tag_id"):
        if "{" + sub + "}" in template:
            path = template.format(id=_int(project_id, "project_id"), **{sub: _int(supplied[sub], sub)})
            return path, {}
    if "{id}" in template:
        path = template.format(id=_int(project_id, "project_id"))
    elif project_id is not None:
        raise ValueError("projects lists projects and does not take project_id")
    else:
        path = template
    params: dict[str, Any] = {}
    for field in ("project_region_id", "group_id"):
        if field in supplied:
            params[field] = _int(supplied[field], field)
    if "parent_id" in supplied:
        params["parent_id"] = _int(supplied["parent_id"], "parent_id", 0)
    for field in ("status_name", "domain", "name", "search"):
        if field in supplied:
            params[field] = _text(supplied[field], field)
    if "per_page" in supplied:
        params["per_page"] = _int(supplied["per_page"], "per_page", 1, _PER_PAGE_MAX[report])
    if "page" in supplied:
        params["page"] = _int(supplied["page"], "page", 1, 10_000)
    for field in ("start_date", "end_date"):
        if field in supplied:
            params[field] = _day(supplied[field], field)
    if params.get("start_date", "") > params.get("end_date", "9999-12-31"):
        raise ValueError("start_date must not be after end_date")
    for field in ("position_from", "position_to"):
        if field in supplied:
            params[field] = _int(supplied[field], field, 0, 100)
    if params.get("position_from", 0) > params.get("position_to", 100):
        raise ValueError("position_from must not exceed position_to")
    if "keyword_ids" in supplied:
        ids = supplied["keyword_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500:
            raise ValueError("keyword_ids must be a list of 1-500 IDs")
        params["keyword_ids"] = ",".join(str(_int(item, "keyword_ids")) for item in dict.fromkeys(ids))
    for field in ("project_region_ids", "group_ids", "tag_ids"):
        if field in supplied:
            ids = supplied[field]
            if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
                raise ValueError(f"{field} must be a list of 1-100 IDs")
            params[field] = ",".join(str(_int(item, field)) for item in dict.fromkeys(ids))
    if "tag_mode" in supplied:
        if supplied["tag_mode"] not in {"include", "exclude"}:
            raise ValueError("tag_mode must be include or exclude")
        params["tag_mode"] = supplied["tag_mode"]
    for field in ("without_tags", "is_deleted", "with_snippets", "with_children"):
        if field in supplied:
            if not isinstance(supplied[field], bool):
                raise ValueError(f"{field} must be a boolean")
            params[field] = int(supplied[field])
    if "sort" in supplied:
        if supplied["sort"] not in {"scan_date", "-scan_date"}:
            raise ValueError("sort must be scan_date or -scan_date")
        params["sort"] = supplied["sort"]
    return path, params


# Projects API writes (opt-in toolset projects_write), each behind an operator approval.
# help.arsenkin.ru/projects-api: PUT/DELETE projects, regions, competitors, keywords (+restore),
# groups, tags, tag assignment.
WRITE_ACTIONS = (
    "create_project",
    "add_keywords",
    "create_group",
    "add_region",
    "add_competitor",
    "run_positions",
    "update_project",
    "delete_project",
    "delete_region",
    "delete_competitor",
    "delete_keywords",
    "restore_keywords",
    "update_group",
    "delete_group",
    "create_tag",
    "update_tag",
    "delete_tag",
    "system_tag",
    "tag_groups",
    "untag_groups",
    "tag_keywords",
    "untag_keywords",
)
DESTRUCTIVE_ACTIONS = frozenset(
    {"delete_project", "delete_region", "delete_competitor", "delete_keywords", "delete_group", "delete_tag"}
)
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")


def _ids(value: object, field: str, maximum: int = 10_000) -> list[int]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{field} must be a list of 1-{maximum} IDs")
    return [_int(item, field) for item in dict.fromkeys(value)]


def _extra_write(action: str, base: str, a: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    if action == "update_project":
        return "PUT", base, {"name": _text(a.get("name"), "name")}
    if action == "delete_project":
        return "DELETE", base, {}
    if action in {"delete_region", "delete_competitor", "delete_group", "delete_tag"}:
        kind = {
            "delete_region": "regions",
            "delete_competitor": "competitors",
            "delete_group": "groups",
            "delete_tag": "tags",
        }[action]
        field = "group_id" if kind == "groups" else "target_id"
        return "DELETE", f"{base}/{kind}/{_int(a.get(field), field)}", {}
    if action == "delete_keywords":
        body: dict[str, Any] = {"keyword_ids": _ids(a.get("keyword_ids"), "keyword_ids")}
        if a.get("hard_delete") is True:
            body["hard_delete"] = True
        return "DELETE", f"{base}/keywords", body
    if action == "restore_keywords":
        return "POST", f"{base}/keywords/restore", {"keyword_ids": _ids(a.get("keyword_ids"), "keyword_ids")}
    if action == "update_group":
        body = {}
        if "name" in a:
            body["name"] = _text(a["name"], "name")
        if "active" in a:
            if not isinstance(a["active"], bool):
                raise ValueError("active must be a boolean")
            body["active"] = a["active"]
        if "parent_id" in a:
            body["parent_id"] = None if a["parent_id"] == 0 else _int(a["parent_id"], "parent_id")
        if "url" in a:
            body["url"] = _url(a["url"], "url")
        if not body:
            raise ValueError("update_group needs name, active, parent_id (0 = root) or url")
        return "PUT", f"{base}/groups/{_int(a.get('group_id'), 'group_id')}", body
    if action in {"create_tag", "update_tag"}:
        body = {}
        if "name" in a:
            body["name"] = _text(a["name"], "name", 64)
        if "color" in a:
            if not isinstance(a["color"], str) or not _COLOR.match(a["color"]):
                raise ValueError("color must be #RRGGBB")
            body["color"] = a["color"]
        if action == "create_tag":
            if "name" not in body:
                raise ValueError("create_tag needs name")
            return "POST", f"{base}/tags", body
        if not body:
            raise ValueError("update_tag needs name or color")
        return "PUT", f"{base}/tags/{_int(a.get('target_id'), 'target_id')}", body
    if action == "system_tag":
        return "POST", f"{base}/tags/system", {}
    if action in {"tag_groups", "untag_groups"}:
        body = {
            "group_ids": _ids(a.get("group_ids"), "group_ids"),
            "tag_ids": _ids(a.get("tag_ids"), "tag_ids"),
        }
        return ("POST" if action == "tag_groups" else "DELETE"), f"{base}/groups/tags", body
    if action in {"tag_keywords", "untag_keywords"}:
        body = {"keyword_ids": _ids(a.get("keyword_ids"), "keyword_ids")}
        if action == "tag_keywords" and a.get("system_tag") is True:
            body["system_tag"] = True
            if "tag_ids" in a:
                body["tag_ids"] = _ids(a["tag_ids"], "tag_ids")
        else:
            body["tag_ids"] = _ids(a.get("tag_ids"), "tag_ids")
        return ("POST" if action == "tag_keywords" else "DELETE"), f"{base}/keywords/tags", body
    return None


_URL = re.compile(r"https?://[^\s]{1,2040}\Z")


def _url(value: object, field: str) -> str:
    if not isinstance(value, str) or _URL.fullmatch(value.strip()) is None:
        raise ValueError(f"{field} must be an http(s) URL")
    return value.strip()


def projects_write_request(
    action: str, project_id: int | None = None, **arguments: Any
) -> tuple[str, str, dict[str, Any]]:
    """Validate one Projects API write and return (method, path, JSON body) offline."""
    a = {key: value for key, value in arguments.items() if value is not None and value != []}
    if action == "create_project":
        body: dict[str, Any] = {"domain": _url(a.get("domain"), "domain")}
        if "name" in a:
            body["name"] = _text(a["name"], "name")
        return "POST", "/projects", body
    if action not in WRITE_ACTIONS:
        raise ValueError("action must be one of: " + ", ".join(WRITE_ACTIONS))
    base = f"/projects/{_int(project_id, 'project_id')}"
    extra = _extra_write(action, base, a)
    if extra is not None:
        return extra
    if action == "add_keywords":
        words = a.get("keywords")
        if not isinstance(words, list) or not 1 <= len(words) <= 10_000:
            raise ValueError("keywords must be a list of 1-10000 phrases")
        body = {"keywords": list(dict.fromkeys(_text(word, "keywords", 512) for word in words))}
        if "group_id" in a:
            body["group_id"] = _int(a["group_id"], "group_id")
        if "url" in a:
            body["url"] = _url(a["url"], "url")
        if "tags" in a:
            tags = a["tags"]
            if not isinstance(tags, list) or len(tags) > 50:
                raise ValueError("tags must be at most 50 names")
            body["tags"] = [_text(tag, "tags", 64) for tag in tags]
        return "POST", f"{base}/keywords", body
    if action == "create_group":
        body = {"name": _text(a.get("name"), "name"), "active": True}
        if "parent_id" in a:
            body["parent_id"] = _int(a["parent_id"], "parent_id")
        if "url" in a:
            body["url"] = _url(a["url"], "url")
        return "POST", f"{base}/groups", body
    if action == "add_region":
        device = a.get("device", "desktop")
        if device not in {"desktop", "mobile"}:
            raise ValueError("device must be desktop or mobile")
        body = {
            "region_id": _int(a.get("region_id"), "region_id"),
            "device": device,
            "depth": _int(a.get("depth", 100), "depth", 10, 100),
            "engine_type": _int(a.get("engine_type", 0), "engine_type", 0, 1),
        }
        return "POST", f"{base}/regions", body
    if action == "add_competitor":
        return "POST", f"{base}/competitors", {"url": _url(a.get("url"), "url")}
    body = {}
    for field in ("project_region_ids", "group_ids", "keyword_ids"):
        if field in a:
            ids = a[field]
            if not isinstance(ids, list) or not 1 <= len(ids) <= 10_000:
                raise ValueError(f"{field} must be a list of IDs")
            body[field] = [_int(item, field) for item in dict.fromkeys(ids)]
    if a.get("sagging_positions") is True:
        body["sagging_positions"] = True
    return "POST", f"{base}/positions/run", body


class ArsenkinProjectsAdapter:
    def __init__(
        self,
        token: str,
        http: JsonHttpClient | None = None,
        coalescer: AsyncSingleFlight | None = None,
        base_url: str = PROJECTS_BASE_URL,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http or JsonHttpClient()
        self.coalescer = coalescer or AsyncSingleFlight()

    async def get(self, report: str, project_id: int | None = None, **arguments: Any) -> dict[str, Any]:
        path, params = projects_request(report, project_id, **arguments)
        if not self.token:
            raise ProviderError("Arsenkin token is not configured")

        async def fetch() -> dict[str, Any]:
            return await self.http.request(
                "GET",
                f"{self.base_url}{path}",
                headers={"X-Api-Token": self.token, "Accept": "application/json"},
                params=params,
                idempotent=True,
            )

        key = request_hash({"provider": "arsenkin_projects", "path": path, "params": params})
        return await self.coalescer.run(key, fetch)

    async def write(self, action: str, project_id: int | None = None, **arguments: Any) -> dict[str, Any]:
        method, path, body = projects_write_request(action, project_id, **arguments)
        if not self.token:
            raise ProviderError("Arsenkin token is not configured")
        return await self.http.request(
            method,
            f"{self.base_url}{path}",
            headers={"X-Api-Token": self.token, "Accept": "application/json"},
            payload=body,
            idempotent=False,
        )
