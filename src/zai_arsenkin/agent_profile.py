"""Arsenkin agent profile: router, compact descriptions, toolsets and prompts."""

from __future__ import annotations

from zai_arsenkin.agent_layer import Profile, Prompt

ROUTER = """Arsenkin MCP: paid SEO jobs (SERP top, clustering, Wordstat, indexation, LSI, meta tags, \
AI answers, whois, IKS...) plus free saved Projects data.
Toolsets: meta, jobs, results, projects (see arsenkin://toolsets).
Which profile when: arsenkin://guide/tools.
Job pattern: arsenkin_profile_catalog -> arsenkin_estimate (free) -> show cost and arsenkin_limits to the \
user -> arsenkin_prepare -> only after explicit OK arsenkin_submit(approval_id, idempotency_key) -> poll \
arsenkin_task_status -> arsenkin_get_result / arsenkin_result_table / arsenkin_export_result.
Many requests: arsenkin_batch_estimate/prepare/submit. Never resubmit an unknown outcome; look up \
arsenkin_request_hash_lookup first. Costs are policy units, not ruble invoices.
Playbooks: skills arsenkin-serp-clustering, arsenkin-index-check, arsenkin-text-relevance; or \
arsenkin://playbook, arsenkin://guide/{tools,clustering-params,errors}.
Errors: retry only when retryable; follow the hint."""

PAID = "Cost: spends Arsenkin limits (see estimate); requires approval_id from `arsenkin_prepare`."

DESCRIPTIONS = {
    "arsenkin_profile_catalog": "List job profiles, payload fields and examples. Use first to build a "
    "payload; which profile when: arsenkin://guide/tools. Cost: free, no provider call.",
    "arsenkin_status": "Check Arsenkin account/API availability. Use when jobs fail; for limits use "
    "`arsenkin_limits`. Cost: free.",
    "arsenkin_limits": "Read total and spent Arsenkin limits. Use before prepare/submit to show what is "
    "left. Cost: free.",
    "arsenkin_local_diagnostics": "Explain local worker heartbeat, your queue and budget. Use when jobs "
    "stay queued. Cost: free, no provider call.",
    "arsenkin_estimate": "Validate one job payload and estimate its cost. Use before `arsenkin_prepare`; "
    "for many payloads use `arsenkin_batch_estimate`. Cost: free, no provider call.",
    "arsenkin_batch_estimate": "Plan, coalesce and price a multi-request batch. Use before "
    "`arsenkin_batch_prepare`. Cost: free, no provider call.",
    "arsenkin_prepare": "Create a bounded approval for one paid job. Use after the user saw the estimate; "
    "then `arsenkin_submit`. Writes: approval record only, no spend.",
    "arsenkin_batch_prepare": "Create one bounded approval for a batch. Use after `arsenkin_batch_estimate`; "
    "then `arsenkin_batch_submit`. Writes: approval record only, no spend.",
    "arsenkin_submit": "Queue an approved paid job; returns job_id without waiting. Use only after the "
    "user's explicit OK; poll `arsenkin_task_status`. " + PAID,
    "arsenkin_batch_submit": "Queue an approved batch job; the worker coalesces and polls. Use after "
    "explicit OK; poll `arsenkin_task_status`. " + PAID,
    "arsenkin_task_status": "Read durable status of your job. Use to poll after submit; when done use "
    "`arsenkin_get_result` or `arsenkin_result_table`. Cost: free.",
    "arsenkin_get_result": "Return the stored result of your job (can be large). Use for small jobs; for "
    "compact tables use `arsenkin_result_table`, for files `arsenkin_export_result`. Cost: free.",
    "arsenkin_result_table": "Build compact SERP URL/host frequency or cluster tables from a saved result. "
    "Use for reports instead of raw results. Cost: free, no new paid calls.",
    "arsenkin_export_result": "Export a saved job as bounded JSONL/CSV with provenance. Use for "
    "spreadsheets/files. Cost: free, no provider call.",
    "arsenkin_request_hash_lookup": "Look up whether an identical request is already in the ledger. Use "
    "before resubmitting after an unknown outcome. Cost: free.",
    "arsenkin_cancel": "Request cancellation of your queued/running job. Use when the user aborts; "
    "already spent limits may not return.",
    "arsenkin_projects": "Read saved Arsenkin Projects data: projects, regions, competitors, keywords, "
    "saved positions, charts. Use for tracked-project reports; never starts checks. Cost: free.",
    "arsenkin_check_update": "Check for a newer server release; never installs. Cost: free, 24h cache.",
}

TOOLSETS = {
    "meta": (
        "arsenkin_check_update",
        "arsenkin_status",
        "arsenkin_limits",
        "arsenkin_profile_catalog",
        "arsenkin_local_diagnostics",
    ),
    "jobs": (
        "arsenkin_estimate",
        "arsenkin_batch_estimate",
        "arsenkin_prepare",
        "arsenkin_batch_prepare",
        "arsenkin_submit",
        "arsenkin_batch_submit",
        "arsenkin_task_status",
        "arsenkin_cancel",
        "arsenkin_request_hash_lookup",
    ),
    "results": ("arsenkin_get_result", "arsenkin_result_table", "arsenkin_export_result"),
    "projects": ("arsenkin_projects",),
}

PROMPTS = (
    Prompt(
        "cluster_keywords",
        "Cluster a keyword list by SERP similarity with cost approval.",
        ("keywords", "region"),
        "Cluster these keywords with Arsenkin (region {region}): {keywords}. Follow skill "
        "arsenkin-serp-clustering: estimate, show cost and limits, wait for my OK, submit, poll, then "
        "arsenkin_result_table(view=clusters) as a table.",
    ),
    Prompt(
        "index_check",
        "Check whether URLs are indexed in Yandex/Google (paid, with approval).",
        ("urls",),
        "Check indexation of: {urls}. Follow skill arsenkin-index-check; ask before spending limits.",
    ),
    Prompt(
        "text_relevance",
        "Analyse SERP-based text requirements (LSI, highlights, meta tags) for a query.",
        ("query", "url"),
        "Analyse what the TOP expects for query '{query}' versus page {url}. Follow skill "
        "arsenkin-text-relevance; show costs before submitting.",
    ),
)

PROFILE = Profile(
    service="arsenkin",
    package="zai_arsenkin",
    router=ROUTER,
    descriptions=DESCRIPTIONS,
    toolsets=TOOLSETS,
    prompts=PROMPTS,
)
