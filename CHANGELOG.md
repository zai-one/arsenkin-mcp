# Changelog

## Unreleased — Projects writes, semantics -> clusters -> positions, router eval

- Projects API edits/deletes per docs (PUT/DELETE projects, groups, tags; DELETE regions, competitors, keywords; keywords/restore; group/keyword tag assignment; groups `with_children`; single tag read). Every projects write, including paid `run_positions`, now needs an operator-accepted, request-bound approval (`arsenkin-projects-approval`).

- API re-check (help.arsenkin.ru/api, projects-api, 2026-10): all 19 job profiles were already covered; Projects API gaps closed.
- `arsenkin_projects`: new reports `group` (by id) and `tags`; keyword filters `tag_ids`, `tag_mode`, `without_tags`, `is_deleted`; positions for several regions (`project_region_ids`), `group_ids`, `with_snippets`.
- `run_positions` is paid outside the `arsenkin_prepare` approval and monthly cost ledger, so it stays disabled (`paid_disabled`) until the operator sets `ARSENKIN_PROJECTS_PAID=on`; `groups` read accepts documented `tag_ids`.
- New opt-in toolset `projects_write` (off by default; default tool list and token budget unchanged): `arsenkin_projects_write` — `create_project`, `create_group`, `add_keywords`, `add_region`, `add_competitor`, paid `run_positions`. Additive only (no PUT/DELETE); returns a free preview of the exact body until `confirm=true`; scope `arsenkin:execute`.
- Skill `arsenkin-semantic-core` (self-contained semantics -> clusters -> positions), guide `arsenkin://guide/projects`, prompt `semantic_core`.
- Router: one line per intent (RU/EN -> first tool; skill; guide) with `arsenkin://playbook` and the generated `arsenkin://guide/tool-map` as fallbacks; `ARSENKIN_TOOLSETS=default|all`.
- `tests/test_routing_eval.py` + `tests/evals/routing_cases.json` (17 RU/EN requests): fails if any tool, skill or guide is unreachable.

## 0.5.0

- License: LicenseRef-ZAI-ONE 2.0 (source-available). Free install and use for yourself and your organisation, including commercial work; issues and pull requests welcome; copying code into other projects, redistribution, embedding and competing hosted services need written permission. Earlier releases keep their LicenseRef-ZAI-ONE 1.0 terms (see NOTICE). README first screen: one-line scope, why this MCP, maintenance and update check.
- Agent layer: router `instructions` (≤1200 chars) describing the estimate → prepare → explicit OK → submit → poll → result job pattern; compact template descriptions with `Cost:` lines and titles.
- Toolsets `meta`, `jobs`, `results`, `projects` (`ARSENKIN_TOOLSETS`, `ARSENKIN_TOOLS`, `ARSENKIN_DISABLED_TOOLS`, `ARSENKIN_READ_ONLY`); `arsenkin://toolsets`.
- Resources `arsenkin://playbook`, `arsenkin://guide/{tools,clustering-params,errors}` ("which profile when" table covering all 19 documented API tools); prompts `cluster_keywords`, `index_check`, `text_relevance`.
- Self-contained skills `arsenkin-serp-clustering`, `arsenkin-index-check`, `arsenkin-text-relevance` with references and templates; Claude Code plugin manifest, Cursor rule, `.agents/skills`.
- `scripts/token_budget.py` + tests gate instruction, description and SKILL.md sizes.
- Install snippets and the plugin manifest pin `git+https://github.com/zai-one/arsenkin-mcp@v0.5.0`.

## 0.4.0

- `arsenkin_projects`: read-only Projects API v1 (projects, regions, competitors, keywords, groups, saved positions, visibility/top/average/median charts). `POST /positions/run` and all write endpoints stay unavailable.
- `arsenkin_limits`: account limits via the documented `info` `{"query": "limits"}` request.
- `arsenkin_check_update`: suggests an update from the latest GitHub release (24h cache, offline-safe, never installs); cached hint in server instructions and `arsenkin_local_diagnostics`; opt out with `ARSENKIN_DISABLE_UPDATE_CHECK=1`.
- MCP tool annotations on every tool; paid submit and cancel are marked destructive.
- Update check hardening: only plain `vX.Y.Z` tags (and validated cached values) can reach results, commands or server instructions; failed fetches are cached with a 30 minute back-off, `force_refresh` runs at most once a minute, and the startup refresh obeys the same rules.
- `arsenkin_local_diagnostics` reports the cached update notice as `update_hint` (string) instead of `update_available`, which in `arsenkin_check_update` is a boolean.
- Docs: `ARSENKIN_DISABLE_UPDATE_CHECK` in `.env.example` and `docs/RUNTIME.md`, including the startup request to `api.github.com`. Tests and the clean-install gate disable the check.

## 0.3.0

- Profile catalog with payload examples and local worker/queue/budget diagnostics.
- Saved-result tables for SERP URL/host occurrences and cluster records, with pointer provenance and JSON/CSV.

- Installable setup wizard and JSON/TOML client snippets that work outside the checkout.
- English and Russian agency information, integration contact and package installation guide.

## 0.2.0

- Explicit config-file startup and offline configuration doctor.
- Install/client guide, local setup wizard and clean-wheel validation.
- arsenkin_export_result returns bounded JSONL/CSV from saved owned jobs, with JSON pointers preserving provenance. It does not launch or pay for another job. Profile-specific SEO table views are future work.
- LicenseRef-ZAI-ONE and Issues-based feedback policy.
