# Arsenkin Projects (saved SEO projects)

Reads (`arsenkin_projects`, free): `projects` -> `project` (status_name, schedules) -> `regions`
(project_region_id) -> `keywords` (group_id, search, tag_ids/tag_mode/without_tags, is_deleted) ->
`groups` / `group(group_id)` / `tags` -> `positions` (project_region_id or project_region_ids for
several regions, keyword_ids, group_ids, dates, position_from/to, with_snippets) -> charts
(`chart_visibility`, `chart_top_statistics`, `chart_average_position`, `chart_median_position`).

Writes (`arsenkin_projects_write`, opt-in toolset `projects_write`, `ARSENKIN_TOOLSETS=all`):

| action | required | notes |
|---|---|---|
| `create_project` | domain (URL), name | returns project id |
| `create_group` | project_id, name | parent_id for a subgroup (max 2 levels), url |
| `add_keywords` | project_id, keywords (≤10 000) | group_id, url, tags (≤50) |
| `add_region` | project_id, region_id | engine_type 0 Yandex/1 Google, device, depth 10–100 |
| `update_project` / `delete_project` | project_id (+ name) | delete is destructive |
| `delete_region` / `delete_competitor` / `delete_tag` | project_id, target_id | destructive |
| `delete_keywords` / `restore_keywords` | project_id, keyword_ids | soft delete; hard_delete=true is final |
| `update_group` / `delete_group` | project_id, group_id | name, active, parent_id (0 = root), url |
| `create_tag` / `update_tag` | project_id, name/color `#RRGGBB` (update: target_id) | `system_tag` returns the system tag |
| `tag_groups` / `untag_groups` | project_id, group_ids, tag_ids | |
| `tag_keywords` / `untag_keywords` | project_id, keyword_ids, tag_ids or system_tag | |
| `add_competitor` | project_id, url | max 30 per project |
| `run_positions` | project_id | paid; disabled unless the operator sets `ARSENKIN_PROJECTS_PAID=on` (`code: paid_disabled`); not counted against ARSENKIN monthly budgets or `ARSENKIN_MAX_COST_PER_APPROVAL`; limits charged on queueing; narrow by project_region_ids/group_ids/keyword_ids; sagging_positions=true |

Every write: preview (no confirm, `needs_confirmation`, exact body) -> `confirm=true` returns
`approval_id` + `operator_command` -> the operator (never the agent) runs it -> repeat the same call
with `approval_id` (single use, request-bound, 30 min; `--list` shows the operator the exact request).
`code: outcome_unknown` = sent, result lost: never retry; check with reads, the operator runs `--resolve`.
`run_positions` additionally needs the operator setting `ARSENKIN_PROJECTS_PAID=on`; otherwise `code: paid_disabled` (use one-off `positions` instead).
After `run_positions` poll `report=project` until `status_name=finished`, then `report=positions`.
422 `scan_not_started` = already running / no phrases / no limits (nothing charged).
