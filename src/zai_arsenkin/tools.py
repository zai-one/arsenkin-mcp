from __future__ import annotations

import asyncio
from typing import Any, Literal
from uuid import UUID

from zai_arsenkin.adapter import build_batch_plan, estimate_cost
from zai_arsenkin.models import validate_request_hashes
from zai_arsenkin.projects import projects_request
from zai_arsenkin.update_check import CACHE_FILE, cached_hint, check_update

# MCP tool annotations; enforcement (scopes, approvals, budgets) stays server-side.
LOCAL_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
PROVIDER_READ = {**LOCAL_READ, "openWorldHint": True}
# Creates a local approval record only; no provider call and no spend.
LOCAL_PREPARE = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
# Queues an approved paid provider task; idempotency_key replays return the same job.
PAID_SUBMIT = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True}
CANCEL = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True}


def register_tools(server: Any, runtime: Any) -> None:
    registry, settings, store, jobs = runtime.registry, runtime.settings, runtime.store, runtime.jobs
    current_access, require_scopes = runtime.current_access, runtime.require_scopes
    _provider_read = runtime.read
    _job_view, _owned_job = runtime.job_view, runtime.owned_job
    update_cache = settings.state_path.parent / CACHE_FILE

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=LOCAL_READ)
    async def arsenkin_profile_catalog() -> dict[str, Any]:
        """List supported profiles, payload fields and example SEO requests; no provider calls."""
        from zai_arsenkin.catalog import profile_catalog

        return profile_catalog()

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=LOCAL_READ)
    async def arsenkin_local_diagnostics() -> dict[str, Any]:
        """Explain local worker heartbeat, your queue and configured budget; no provider calls."""
        result = store.diagnostics(current_access().principal_id)
        hint = cached_hint(update_cache)  # local cache only; no network
        if hint:
            result["update_available"] = hint
        return result

    @server.tool(auth=require_scopes("arsenkin:read"), annotations={**LOCAL_READ, "openWorldHint": True})
    async def arsenkin_check_update(force_refresh: bool = False) -> dict[str, Any]:
        """Compare the installed version with the latest GitHub release and suggest an update command.

        Read-only; never installs anything and spends no Arsenkin limits. Uses a 24h cache.
        Set ARSENKIN_DISABLE_UPDATE_CHECK=1 to opt out.
        """
        return await asyncio.to_thread(check_update, update_cache, force=force_refresh)

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=LOCAL_READ)
    async def arsenkin_result_table(
        job_id: str, view: str, result_pointer: str = "", format: str = "json", max_rows: int = 500
    ) -> dict[str, Any]:
        """Create SERP URL/host frequency or cluster tables from a selected saved-result JSON pointer.

        serp_urls/serp_hosts select repeatUrl/repeatHost; clusters selects cluster records.
        Completeness covers only this selected view, never the whole provider task. No new paid calls.
        """
        from zai_arsenkin.result_tables import result_table

        job = await _owned_job(job_id, include_result=True)
        return result_table(job, view, result_pointer, format, max_rows)

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=PROVIDER_READ)
    async def arsenkin_status() -> dict[str, Any]:
        """Check Arsenkin account/API status; this does not submit a paid task."""
        return await _provider_read("arsenkin", "status", {}, registry.arsenkin().status)

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=PROVIDER_READ)
    async def arsenkin_limits() -> dict[str, Any]:
        """Read the account's total and spent Arsenkin limits; never submits a paid task."""
        return await _provider_read("arsenkin", "limits", {}, registry.arsenkin().limits)

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=PROVIDER_READ)
    async def arsenkin_projects(
        report: Literal[
            "projects",
            "project",
            "regions",
            "competitors",
            "keywords",
            "groups",
            "positions",
            "chart_visibility",
            "chart_top_statistics",
            "chart_average_position",
            "chart_median_position",
        ],
        project_id: int | None = None,
        project_region_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        group_id: int | None = None,
        parent_id: int | None = None,
        keyword_ids: list[int] | None = None,
        position_from: int | None = None,
        position_to: int | None = None,
        sort: Literal["scan_date", "-scan_date"] | None = None,
        search: str | None = None,
        name: str | None = None,
        domain: str | None = None,
        status_name: str | None = None,
        per_page: int | None = None,
        page: int | None = None,
    ) -> dict[str, Any]:
        """Read saved Arsenkin SEO projects data (Projects API v1); never starts paid position checks.

        report=projects lists projects. With project_id: project, regions, competitors, keywords,
        groups, positions (saved positions; filter by project_region_id, dates, groups, keyword_ids,
        position_from/to) and charts (chart_visibility, chart_top_statistics, chart_average_position,
        chart_median_position) that require project_region_id from report=project or regions.
        """
        optional = {
            "project_region_id": project_region_id,
            "start_date": start_date,
            "end_date": end_date,
            "group_id": group_id,
            "parent_id": parent_id,
            "keyword_ids": keyword_ids,
            "position_from": position_from,
            "position_to": position_to,
            "sort": sort,
            "search": search,
            "name": name,
            "domain": domain,
            "status_name": status_name,
            "per_page": per_page,
            "page": page,
        }
        return await _provider_read(
            "arsenkin",
            f"projects_{report}",
            {"report": report, "project_id": project_id, **optional},
            lambda: registry.arsenkin_projects().get(report, project_id, **optional),
            validate=lambda: projects_request(report, project_id, **optional),
        )

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=LOCAL_READ)
    async def arsenkin_estimate(profile: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate an Arsenkin payload and estimate cost without a live call."""
        return estimate_cost(profile, payload, settings.arsenkin_cost_per_query)

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=LOCAL_READ)
    async def arsenkin_batch_estimate(requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Plan, coalesce and price a multi-request batch without a live call."""
        return build_batch_plan(requests, settings.arsenkin_cost_per_query)

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=LOCAL_READ)
    async def arsenkin_request_hash_lookup(request_hashes: list[str]) -> dict[str, Any]:
        """Look up exact server-ledger hashes without exposing job or provider payload data."""
        access = current_access()
        unique_hashes = validate_request_hashes(request_hashes)
        lookup = await store.lookup_job_request_hashes(
            access.principal_id,
            "arsenkin",
            unique_hashes,
        )
        matches_by_hash: dict[str, list[Any]] = {value: [] for value in unique_hashes}
        for match in lookup.matches:
            matches_by_hash[match.request_hash].append(match)
        matches = [
            {
                "request_hash": value,
                "hash_kinds": sorted({match.hash_kind for match in matches_by_hash[value]}),
                "statuses": sorted({match.status for match in matches_by_hash[value]}),
                "submission_outcome_unknown": any(
                    match.submission_outcome_unknown for match in matches_by_hash[value]
                ),
            }
            for value in unique_hashes
            if matches_by_hash[value]
        ]
        return {
            "requested_count": len(request_hashes),
            "unique_request_count": len(unique_hashes),
            "matched_request_count": len(matches),
            "coverage_complete": lookup.unindexed_batch_job_count == 0,
            "unindexed_batch_job_count": lookup.unindexed_batch_job_count,
            "matches": matches,
            "missing_request_hashes": [value for value in unique_hashes if not matches_by_hash[value]],
        }

    @server.tool(auth=require_scopes("arsenkin:execute"), annotations=LOCAL_PREPARE)
    async def arsenkin_prepare(profile: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a bounded approval; trusted policy may return typed server acceptance."""
        access = current_access()
        auto_accept = settings.arsenkin_unlimited_auto_paid_allowed({profile})
        return await jobs.prepare_arsenkin(
            access.principal_id,
            profile,
            payload,
            access.monthly_cost_limit,
            access.monthly_cost_unlimited,
            auto_accept,
            settings.arsenkin_standing_policy_id,
            settings.arsenkin_max_cost_per_approval,
        )

    @server.tool(auth=require_scopes("arsenkin:execute"), annotations=LOCAL_PREPARE)
    async def arsenkin_batch_prepare(requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Create one bounded batch approval; trusted policy may accept it server-side."""
        access = current_access()
        profiles = {str(item.get("profile", "")).strip() for item in requests if isinstance(item, dict)}
        auto_accept = settings.arsenkin_unlimited_auto_paid_allowed(profiles)
        return await jobs.prepare_arsenkin_batch(
            access.principal_id,
            requests,
            access.monthly_cost_limit,
            access.monthly_cost_unlimited,
            auto_accept,
            settings.arsenkin_standing_policy_id,
            settings.arsenkin_max_cost_per_approval,
        )

    @server.tool(auth=require_scopes("arsenkin:execute"), annotations=PAID_SUBMIT)
    async def arsenkin_submit(
        profile: str, payload: dict[str, Any], approval_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        """Queue an approved paid Arsenkin request; never blocks on provider polling."""
        await registry.ensure_enabled("arsenkin")
        access = current_access()
        job = await jobs.submit_arsenkin(
            access.principal_id, profile, payload, UUID(approval_id), idempotency_key
        )
        return _job_view(job)

    @server.tool(auth=require_scopes("arsenkin:execute"), annotations=PAID_SUBMIT)
    async def arsenkin_batch_submit(
        requests: list[dict[str, Any]], approval_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        """Queue one approved batch job; worker coalesces and polls centrally."""
        await registry.ensure_enabled("arsenkin")
        access = current_access()
        job = await jobs.submit_arsenkin_batch(
            access.principal_id, requests, UUID(approval_id), idempotency_key
        )
        return _job_view(job)

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=LOCAL_READ)
    async def arsenkin_task_status(job_id: str) -> dict[str, Any]:
        """Return durable status for the principal's Arsenkin job."""
        return await _owned_job(job_id)

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=LOCAL_READ)
    async def arsenkin_get_result(job_id: str) -> dict[str, Any]:
        """Return the durable result for the principal's Arsenkin job."""
        return await _owned_job(job_id, include_result=True)

    @server.tool(auth=require_scopes("arsenkin:read"), annotations=LOCAL_READ)
    async def arsenkin_export_result(
        job_id: str, format: str = "jsonl", max_rows: int = 500
    ) -> dict[str, Any]:
        """Export a saved owned job as bounded JSONL/CSV with JSON-pointer provenance; no provider calls."""
        from zai_arsenkin.export import export_result

        job = await _owned_job(job_id, include_result=True)
        return export_result(job, format, max_rows)

    @server.tool(auth=require_scopes("arsenkin:execute"), annotations=CANCEL)
    async def arsenkin_cancel(job_id: str) -> dict[str, Any]:
        """Request cancellation of the principal's queued or running Arsenkin job."""
        access = current_access()
        return {
            "job_id": job_id,
            "cancel_requested": await store.cancel_job(UUID(job_id), access.principal_id),
        }
