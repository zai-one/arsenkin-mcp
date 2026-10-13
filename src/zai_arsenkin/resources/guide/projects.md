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
| `add_competitor` | project_id, url | max 30 per project |
| `run_positions` | project_id | paid; limits charged on queueing; narrow by project_region_ids/group_ids/keyword_ids; sagging_positions=true |

Every write returns a free preview (`needs_confirmation`, exact body) until `confirm=true`.
After `run_positions` poll `report=project` until `status_name=finished`, then `report=positions`.
422 `scan_not_started` = already running / no phrases / no limits (nothing charged).
Edits and deletes are not exposed: do them in the Arsenkin UI.
