# Arsenkin playbook

Arsenkin jobs spend account limits; Projects data is free.

1. **Pick a profile** — `arsenkin://guide/tools` (which profile for which task) and
   `arsenkin_profile_catalog` (payload fields, examples).
2. **Price it** — `arsenkin_estimate` (one) or `arsenkin_batch_estimate` (many). Free. Show the user the
   estimate and `arsenkin_limits` (total/spent).
3. **Approve** — `arsenkin_prepare` / `arsenkin_batch_prepare` returns `approval_id` (bounded, short-lived).
4. **Submit only after explicit OK** — `arsenkin_submit(profile, payload, approval_id, idempotency_key)`.
   Same payload + key = same job; never resubmit an unknown outcome — `arsenkin_request_hash_lookup` first.
5. **Poll** — `arsenkin_task_status` every 30–60 s; jobs run in the background worker.
6. **Read compactly** — `arsenkin_result_table` (serp_urls / serp_hosts / clusters) or
   `arsenkin_export_result` (CSV/JSONL); `arsenkin_get_result` only for small jobs.
7. **Projects** — `arsenkin_projects` for saved positions/charts (free); opt-in `arsenkin_projects_write`
   to create projects, add keywords/groups/regions/competitors and run a paid position check
   (`arsenkin://guide/projects`).
8. **Semantics → clusters → positions** — skill `arsenkin-semantic-core`: `wordstat-phrases` →
   `wordstat-frequency` → `clustering` → project groups + `run_positions` (or one-off `positions`).

Skills: `arsenkin-serp-clustering`, `arsenkin-index-check`, `arsenkin-text-relevance`, `arsenkin-semantic-core`.
Guides: `arsenkin://guide/tools`, `arsenkin://guide/clustering-params`, `arsenkin://guide/projects`,
`arsenkin://guide/errors`, `arsenkin://guide/tool-map` (every tool by toolset).
