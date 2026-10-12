from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from zai_arsenkin.coalescing import AsyncSingleFlight
from zai_arsenkin.transport import (
    JsonHttpClient,
    ProviderError,
    ProviderResponseError,
    request_hash,
)

DEFAULT_RUSSIA_REGION_ID = 225
MAX_BATCH_REQUESTS = 50

# Targets intentionally stay below provider hard ceilings.  They mirror the
# canonical SEO skill recommendations and keep one provider task useful without
# producing an oversized result that is difficult to recover or normalize.
BATCH_QUERY_TARGETS: dict[str, int] = {
    "check-top": 1000,
    "wordstat-frequency": 1000,
    "wordstat-phrases": 100,
    "wordstat-dynamics": 500,
    "commerce": 500,
    "indexation": 500,
    "clustering": 5000,
    "suggest": 500,
    "relevant-url": 500,
    "positions": 500,
    "parser-ads": 1,
    "paa": 500,
    "whois": 500,
    "sp": 500,
    "ai-serp": 500,
    "copyrighters": 500,
}


@dataclass(frozen=True, slots=True)
class ArsenkinProfile:
    tools_name: str
    required: tuple[str, ...]
    defaults: dict[str, Any] = field(default_factory=dict)
    list_fields: tuple[str, ...] = ()
    bool_fields: tuple[str, ...] = ()
    max_items: dict[str, int] = field(default_factory=dict)


PROFILES: dict[str, ArsenkinProfile] = {
    "check-top": ArsenkinProfile(
        "check-top",
        ("queries", "se", "depth"),
        {"is_snippet": True, "noreask": False, "depth": 10},
        ("queries", "se"),
        ("is_snippet", "noreask"),
        {"queries": 10000},
    ),
    "wordstat-frequency": ArsenkinProfile(
        "wordstat",
        ("queries", "regions", "ws"),
        {"type": 1, "device": ""},
        ("queries", "regions", "ws"),
        max_items={"queries": 10000, "regions": 4},
    ),
    "wordstat-phrases": ArsenkinProfile(
        "wordstat",
        ("queries", "region"),
        {
            "type": 2,
            "device": "",
            "minus_words": [],
            "is_clear_minus": True,
            "is_right": True,
            "is_clear": True,
        },
        ("queries", "minus_words"),
        ("is_clear_minus", "is_right", "is_clear"),
        {"queries": 500},
    ),
    "wordstat-dynamics": ArsenkinProfile(
        "wordstat",
        ("queries", "region", "group", "startdate", "enddate"),
        {"type": 3, "device": ""},
        ("queries",),
    ),
    "commerce": ArsenkinProfile("commerce", ("queries", "se", "region"), list_fields=("queries",)),
    "indexation": ArsenkinProfile(
        "indexation",
        ("queries",),
        {"yandex": True, "google": True, "search_all": True, "inurl": True},
        ("queries",),
        ("yandex", "google", "search_all", "inurl"),
    ),
    "clustering": ArsenkinProfile(
        "clustering",
        ("queries", "se", "region"),
        {"group": "hard", "count": 3, "depth": 10, "stoplist": [], "ws": [], "main": False},
        ("queries", "stoplist", "ws"),
        ("main",),
        {"queries": 70000},
    ),
    "suggest": ArsenkinProfile(
        "suggest",
        ("queries", "se", "region"),
        {"check": ["nrm"], "depth": 1, "stoplist": []},
        ("queries", "check", "stoplist"),
    ),
    "relevant-url": ArsenkinProfile(
        "relevant-url", ("queries", "host", "se", "region"), list_fields=("queries",)
    ),
    "positions": ArsenkinProfile(
        "positions",
        ("queries", "url", "se", "format"),
        {"alt_urls": [], "subdomain": False, "format": 1},
        ("queries", "se", "alt_urls"),
        ("subdomain",),
    ),
    "parser-ads": ArsenkinProfile(
        "parser-ads",
        ("queries", "regions"),
        {"device": "desktop", "hosts": []},
        ("queries", "regions", "hosts"),
        max_items={"queries": 1, "regions": 10},
    ),
    "paa": ArsenkinProfile(
        "paa",
        ("queries", "se", "region", "google_domain", "google_from", "google_lang"),
        {"depth": 1, "count": 10},
        ("queries",),
    ),
    "whois": ArsenkinProfile("whois", ("queries",), list_fields=("queries",), max_items={"queries": 500}),
    "iks": ArsenkinProfile("iks", ("mode",), list_fields=("queries", "urls", "domains")),
    "semantics": ArsenkinProfile(
        "semantics",
        ("mode", "region"),
        {
            "stopwords": [],
            "remove_ws": False,
            "remove_ws2": False,
            "remove_small": False,
            "delete_porno": True,
            "delete_toponim": False,
            "only_toponim": False,
            "delete_doubles": True,
        },
        ("queries", "domains", "urls", "competitors", "stopwords"),
        (
            "remove_ws",
            "remove_ws2",
            "remove_small",
            "delete_porno",
            "delete_toponim",
            "only_toponim",
            "delete_doubles",
        ),
        {"stopwords": 10000},
    ),
    "sp": ArsenkinProfile("sp", ("queries", "se", "region", "depth"), list_fields=("queries",)),
    "check-h": ArsenkinProfile("check-h", ("mode",), {"foreign": False}, ("queries", "urls"), ("foreign",)),
    "ai-serp": ArsenkinProfile(
        "ai-serp",
        ("queries", "se", "region", "host"),
        {"subdomain": False, "brands": []},
        ("queries", "brands"),
        ("subdomain",),
        {"brands": 10},
    ),
    "copyrighters": ArsenkinProfile(
        "copyrighters",
        ("queries", "se", "region"),
        {"foreign": False, "remove_main": True, "urls": [], "stoplist": [], "stopwords": []},
        ("queries", "urls", "stoplist", "stopwords"),
        ("foreign", "remove_main"),
    ),
}


def active_task_count(value: Any) -> int | None:
    """Parse the documented account-global active-task count, or fail unknown."""
    if isinstance(value, dict):
        if str(value.get("code", "")).upper() == "TASKS_LIST" and isinstance(value.get("result"), list):
            return len(value["result"])
        for key in ("active", "active_tasks", "tasks_active", "in_progress", "processing"):
            raw = value.get(key)
            if isinstance(raw, int) and not isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.isdigit():
                return int(raw)
        for item in value.values():
            found = active_task_count(item)
            if found is not None:
                return found
    if isinstance(value, list):
        active_markers = {"active", "progress", "process", "processing", "work", "running"}
        count = 0
        matched = False
        for item in value:
            if isinstance(item, dict):
                status_text = str(item.get("status") or item.get("state") or "").lower()
                if any(marker in status_text for marker in active_markers):
                    matched = True
                    count += 1
        if matched:
            return count
    return None


def _as_list(value: Any, field_name: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
    raise ValueError(f"{field_name} must be a list")


def build_payload(profile_name: str, value: dict[str, Any]) -> dict[str, Any]:
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise ValueError("unknown Arsenkin profile")
    data = {**profile.defaults, **value}
    if profile_name == "positions" and "se" in data:
        normalized = []
        for item in _as_list(data["se"], "se"):
            current = dict(item) if isinstance(item, dict) else {"type": int(item)}
            current.setdefault("region", DEFAULT_RUSSIA_REGION_ID)
            normalized.append(current)
        data["se"] = normalized
    for field_name in profile.list_fields:
        if field_name in data:
            data[field_name] = _as_list(data[field_name], field_name)
    for field_name in profile.bool_fields:
        if field_name in data and not isinstance(data[field_name], bool):
            raise ValueError(f"{field_name} must be boolean")
    missing = [field_name for field_name in profile.required if data.get(field_name) in (None, "", [])]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")
    for field_name, maximum in profile.max_items.items():
        if field_name in data and len(_as_list(data[field_name], field_name)) > maximum:
            raise ValueError(f"{field_name} exceeds hard maximum {maximum}")
    if profile_name in {"check-top", "positions"}:
        for item in data.get("se", []):
            if not isinstance(item, dict) or not isinstance(item.get("type"), int):
                raise ValueError("se[].type must be numeric")
            if "region" not in item:
                raise ValueError("se[].region is required")
    # Arsenkin's canonical Tools API contract keeps the profile payload under
    # ``data``.  Keeping this envelope identical to the provider request is
    # also important because prepare/submit approvals hash this value.
    return {"tools_name": profile.tools_name, "data": data}


def estimate_cost(profile_name: str, payload: dict[str, Any], cost_per_query: float) -> dict[str, Any]:
    normalized = build_payload(profile_name, payload)
    count = len(normalized["data"].get("queries", [])) or 1
    estimated = round(count * cost_per_query, 4)
    return {
        "profile": profile_name,
        "request_hash": request_hash(normalized),
        "query_count": count,
        "estimated_cost_units": estimated,
        "live_call": False,
    }


def child_request_hashes(requests: list[dict[str, Any]]) -> tuple[str, ...]:
    """Return stable logical request hashes without caller-only lineage fields."""
    if not 1 <= len(requests) <= MAX_BATCH_REQUESTS:
        raise ValueError(f"requests must contain between 1 and {MAX_BATCH_REQUESTS} items")
    hashes: list[str] = []
    for item in requests:
        if not isinstance(item, dict):
            raise ValueError("each Arsenkin batch item must be an object")
        profile_name = str(item.get("profile") or "").strip()
        payload = item.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("each Arsenkin batch payload must be an object")
        hashes.append(request_hash(build_payload(profile_name, payload)))
    return tuple(dict.fromkeys(hashes))


def _stable_item_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _batch_target(profile_name: str, profile: ArsenkinProfile) -> int:
    hard_max = int(profile.max_items.get("queries", BATCH_QUERY_TARGETS.get(profile_name, 500)))
    return max(1, min(hard_max, BATCH_QUERY_TARGETS.get(profile_name, hard_max)))


def build_batch_plan(requests: list[dict[str, Any]], cost_per_query: float) -> dict[str, Any]:
    """Coalesce compatible Arsenkin requests into bounded provider tasks.

    Compatibility is deliberately strict: profile and every normalized field
    except ``queries`` must match. Query order is stable, duplicates are sent
    once, and every query retains the caller-provided ``client_ref`` lineage.
    """
    if not 1 <= len(requests) <= MAX_BATCH_REQUESTS:
        raise ValueError(f"requests must contain between 1 and {MAX_BATCH_REQUESTS} items")

    client_refs: set[str] = set()
    buckets: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    input_query_count = 0

    for index, item in enumerate(requests):
        if not isinstance(item, dict):
            raise ValueError("each Arsenkin batch item must be an object")
        client_ref = str(item.get("client_ref") or "").strip()
        if not client_ref or len(client_ref) > 128:
            raise ValueError("client_ref is required and must be at most 128 characters")
        if client_ref in client_refs:
            raise ValueError(f"duplicate client_ref: {client_ref}")
        client_refs.add(client_ref)
        profile_name = str(item.get("profile") or "").strip()
        payload = item.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(f"payload must be an object for client_ref={client_ref}")
        normalized = build_payload(profile_name, payload)
        data = dict(normalized["data"])
        queries = data.pop("queries", None)
        if not isinstance(queries, list) or not queries or profile_name == "parser-ads":
            passthrough.append(
                {
                    "profile": profile_name,
                    "payload": dict(normalized["data"]),
                    "client_refs": [client_ref],
                    "query_owners": [],
                    "source_indexes": [index],
                }
            )
            input_query_count += len(queries) if isinstance(queries, list) else 1
            continue

        input_query_count += len(queries)
        compatibility_key = request_hash({"profile": profile_name, "data": data})
        bucket = buckets.setdefault(
            compatibility_key,
            {
                "profile": profile_name,
                "base_data": data,
                "query_items": [],
                "query_index": {},
            },
        )
        for query in queries:
            query_key = _stable_item_key(query)
            query_index = bucket["query_index"]
            if query_key not in query_index:
                query_index[query_key] = len(bucket["query_items"])
                bucket["query_items"].append(
                    {"query": query, "client_refs": [client_ref], "source_indexes": [index]}
                )
                continue
            existing = bucket["query_items"][query_index[query_key]]
            existing["client_refs"].append(client_ref)
            existing["source_indexes"].append(index)

    groups: list[dict[str, Any]] = []
    for bucket in buckets.values():
        profile_name = str(bucket["profile"])
        target = _batch_target(profile_name, PROFILES[profile_name])
        query_items = list(bucket["query_items"])
        for offset in range(0, len(query_items), target):
            chunk = query_items[offset : offset + target]
            payload = {**dict(bucket["base_data"]), "queries": [item["query"] for item in chunk]}
            refs = list(dict.fromkeys(ref for item in chunk for ref in item["client_refs"]))
            source_indexes = sorted({source for item in chunk for source in item["source_indexes"]})
            groups.append(
                {
                    "profile": profile_name,
                    "payload": payload,
                    "client_refs": refs,
                    "query_owners": [
                        {"query": item["query"], "client_refs": item["client_refs"]} for item in chunk
                    ],
                    "source_indexes": source_indexes,
                }
            )
    groups.extend(passthrough)

    total_cost = 0.0
    provider_query_count = 0
    for group in groups:
        estimate = estimate_cost(str(group["profile"]), dict(group["payload"]), cost_per_query)
        total_cost += float(estimate["estimated_cost_units"])
        provider_query_count += int(estimate["query_count"])
        group["query_count"] = int(estimate["query_count"])
        group["estimated_cost_units"] = float(estimate["estimated_cost_units"])
        group["group_id"] = request_hash(
            {
                "profile": group["profile"],
                "payload": group["payload"],
                "client_refs": group["client_refs"],
            }
        )

    plan_body = {
        "requests": requests,
        "groups": groups,
        "input_request_count": len(requests),
        "input_query_count": input_query_count,
        "provider_task_count": len(groups),
        "provider_query_count": provider_query_count,
        "deduplicated_query_count": max(0, input_query_count - provider_query_count),
        "estimated_cost_units": round(total_cost, 4),
    }
    return {
        **plan_body,
        "request_hash": request_hash(plan_body),
        "live_call": False,
    }


class ArsenkinAdapter:
    def __init__(
        self,
        base_url: str,
        token: str,
        http: JsonHttpClient | None = None,
        coalescer: AsyncSingleFlight | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http or JsonHttpClient()
        self.coalescer = coalescer or AsyncSingleFlight()

    async def _post(self, action: str, payload: dict[str, Any], *, coalesce: bool = False) -> dict[str, Any]:
        if not self.token:
            raise ProviderError("Arsenkin token is not configured")

        async def fetch() -> dict[str, Any]:
            return await self.http.request(
                "POST",
                f"{self.base_url}/{action}",
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                payload=payload,
            )

        response = (
            await self.coalescer.run(
                request_hash({"provider": "arsenkin", "action": action, "payload": payload}), fetch
            )
            if coalesce
            else await fetch()
        )
        if str(response.get("status", "")).lower() == "error":
            raise ProviderResponseError("Arsenkin returned an error response")
        return response

    async def status(self) -> dict[str, Any]:
        return await self._post("info", {"query": "status"}, coalesce=True)

    async def limits(self) -> dict[str, Any]:
        # Documented read-only account query: https://help.arsenkin.ru/api ("query": "limits").
        return await self._post("info", {"query": "limits"}, coalesce=True)

    async def submit(self, profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        # The worker owns the paid-submit reconciliation boundary.  Return the
        # raw successful response even when its task reference is missing so
        # the worker can persist ``submission_outcome_unknown`` and retain the
        # reservation instead of treating this as a generic pre-submit error.
        return await self._post("set", build_payload(profile_name, payload))

    async def task_status(self, task_id: str) -> dict[str, Any]:
        return await self._post("check", {"task_id": task_id}, coalesce=True)

    async def result(self, task_id: str) -> dict[str, Any]:
        return await self._post("get", {"task_id": task_id}, coalesce=True)

    async def cancel(self, task_id: str) -> dict[str, Any]:
        return await self._post("tasks", {"action": "delete", "task_id": task_id})
