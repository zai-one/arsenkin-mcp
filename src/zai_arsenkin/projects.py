"""Read-only Arsenkin Projects API v1 (https://help.arsenkin.ru/projects-api).

Only documented GET endpoints are reachable. Creating, editing or deleting projects,
regions, competitors, keywords, groups or tags, and the paid POST /positions/run, are
deliberately absent.
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
        frozenset({"group_id", "search", "per_page", "page"}),
        frozenset(),
    ),
    "groups": ("/projects/{id}/groups", frozenset({"parent_id", "search", "per_page", "page"}), frozenset()),
    "positions": (
        "/projects/{id}/positions",
        frozenset(
            {
                "project_region_id",
                "keyword_ids",
                "group_id",
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
    if "sort" in supplied:
        if supplied["sort"] not in {"scan_date", "-scan_date"}:
            raise ValueError("sort must be scan_date or -scan_date")
        params["sort"] = supplied["sort"]
    return path, params


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
