---
name: arsenkin-semantic-core
description: Semantics -> clusters -> positions with Arsenkin alone - Wordstat phrases and frequency, SERP clustering, then a project with cluster groups and a position check. Use for "собери семантику", "ядро и кластеры", "semantic core", "позиции по кластерам".
license: LicenseRef-ZAI-ONE
compatibility: Requires arsenkin-mcp with a running worker. Positions via projects need ARSENKIN_TOOLSETS=all (projects_write); otherwise the one-off positions profile.
metadata:
  service: arsenkin
---
# Semantic core: semantics -> clusters -> positions

Profiles: [references/tools.md](references/tools.md). Clustering: [references/clustering-params.md](references/clustering-params.md).
Projects: [references/projects.md](references/projects.md). Errors: [references/errors.md](references/errors.md).

Every paid step: `arsenkin_estimate` + `arsenkin_limits` -> show -> explicit OK -> `arsenkin_prepare` ->
`arsenkin_submit(approval_id, idempotency_key)` -> poll `arsenkin_task_status`.

## Steps
1. Ask: domain, 3–20 seeds, region (213 Москва), engine (Yandex default).
2. Semantics: profile `wordstat-phrases` (seeds, region) -> `arsenkin_result_table`/`arsenkin_get_result`.
   Optional `suggest` for long tail, `semantics` (mode url/domain) for competitors' phrases.
3. Frequency: `wordstat-frequency` (ws base+"quoted") on the merged list; drop quoted < 10, duplicates,
   foreign brands, off-topic. Optional `commerce` to split commercial vs informational.
4. Clusters: `clustering` (se, region, group hard, count 3) -> `arsenkin_result_table(view="clusters")`.
5. Map clusters to pages: `relevant-url` (host) shows which URL ranks; else mark "new page".
6. Positions, tracked: `arsenkin_projects_write` — `create_project` (or reuse `arsenkin_projects`),
   `add_region`, one `create_group` per cluster, `add_keywords(group_id, url)`, `run_positions`
   (each with confirm=true after OK) -> poll `arsenkin_projects(report="project")` -> `report="positions"`.
   Positions, one-off (no project): profile `positions` (queries, url, se).
7. Output with [templates/semantic-core.md](templates/semantic-core.md).

## Guardrails
- One approval per paid step; never batch-approve the whole flow.
- Never resubmit an unknown outcome: `arsenkin_request_hash_lookup` first.
- Partial data stays partial: list unfinished steps and job_ids.
