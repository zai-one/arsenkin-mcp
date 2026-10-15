# Changelog

## 0.6.0 — Projects writes, semantics -> clusters -> positions, router eval

- Upgrade note: `ARSENKIN_TOOLSETS=all` now also enables the new opt-in `projects_write` toolset (writes and the paid `run_positions`, still behind operator approval and `ARSENKIN_PROJECTS_PAID=on`). Unset or `default` keeps the 0.5.0 surface; list toolsets explicitly if you relied on `all` meaning the previous set.
- Optional lazy tool loading (`ARSENKIN_TOOL_LOADING=eager|lazy`, default `eager` = unchanged): lazy lists a small core plus `arsenkin_enable_toolsets` (toolsets with one-line description and token cost); enabling registers the tools at runtime and sends `notifications/tools/list_changed`. Allowlists, read-only mode and approval gates still apply. Setup wizard asks eager vs lazy. Startup tokens (tools/list + instructions, ≈chars/4): eager ≈3678, lazy ≈1285. Scope: enablement is per MCP session (FastMCP session visibility), so one client's enabled toolsets never appear for another. The HTTP transport here is stateless (every request is its own session), so enabled toolsets do not persist there: for shared HTTP use `eager` or `ARSENKIN_TOOLSETS`. README lazy section lists verified `tools/list_changed` client support with sources.
- Projects API edits/deletes per docs (PUT/DELETE projects, groups, tags; DELETE regions, competitors, keywords; keywords/restore; group/keyword tag assignment; groups `with_children`; single tag read). Every projects write, including paid `run_positions`, now needs an operator-accepted, request-bound approval (`arsenkin-projects-approval`).
- Projects approval hardening: prepare/accept/consume hold an exclusive file lock, so an operator acceptance written from a stale read can no longer re-arm an approval the server just consumed (a second paid `run_positions`); approvals are bound to the preparing principal; `operator_command` pins `--path` to the server's approval file (works from any directory, without `ARSENKIN_STATE_PATH`); `arsenkin-projects-approval --path FILE --list` shows the exact request the digest covers.
- Security: projects approvals are bound to the account (account id, credential fingerprint, provider URL, state database) and stored per state database (`<state>.sqlite.projects-approvals.json` next to `ARSENKIN_STATE_PATH`), so an approval accepted for one account can no longer authorize the same write in another.
- Fix: a projects write whose response is lost after sending (timeout, connection reset, invalid reply) now returns `code: outcome_unknown` (not retryable) instead of a retryable provider error; the approval becomes a durable dispatch record, replaying it never calls the provider again (a completed write replays its saved result), and a new approval for the same request is refused until the operator checks the provider and runs `arsenkin-projects-approval --approval-id ID --resolve applied|not_applied`.
- Fix: preparing projects approvals is bounded (10 waiting per principal, 100 in total, 2 MiB per request, 8 MiB per file; `code: approval_quota_exceeded`), and approval file I/O no longer blocks the server's event loop.
- Docs: `run_positions` is not counted against `ARSENKIN_PRINCIPAL_MONTHLY_COST_LIMIT` / `ARSENKIN_ACCOUNT_MONTHLY_COST_LIMIT` or `ARSENKIN_MAX_COST_PER_APPROVAL` and has no cost estimate; its only gates are `ARSENKIN_PROJECTS_PAID=on` (off by default) and a single-use operator approval (accepted by the owner as the intended design).
- API re-check (help.arsenkin.ru/api, projects-api, 2026-10): all 19 job profiles were already covered; Projects API gaps closed.
- `arsenkin_projects`: new reports `group` (by id) and `tags`; keyword filters `tag_ids`, `tag_mode`, `without_tags`, `is_deleted`; positions for several regions (`project_region_ids`), `group_ids`, `with_snippets`.
- `run_positions` is paid outside the `arsenkin_prepare` approval and monthly cost ledger, so it stays disabled (`paid_disabled`) until the operator sets `ARSENKIN_PROJECTS_PAID=on`; `groups` read accepts documented `tag_ids`.
- New opt-in toolset `projects_write` (off by default; default tool list and token budget unchanged): `arsenkin_projects_write` — `create_project`, `create_group`, `add_keywords`, `add_region`, `add_competitor`, paid `run_positions`, plus the edits/deletes listed above. Free preview of the exact body without `confirm`; `confirm=true` returns an operator `approval_id`; scope `arsenkin:execute`.
- Skill `arsenkin-semantic-core` (self-contained semantics -> clusters -> positions), guide `arsenkin://guide/projects`, prompt `semantic_core`.
- Router: one line per intent (RU/EN -> first tool; skill; guide) with `arsenkin://playbook` and the generated `arsenkin://guide/tool-map` as fallbacks; `ARSENKIN_TOOLSETS=default|all`.
- `tests/test_routing_eval.py` + `tests/evals/routing_cases.json` (21 RU/EN requests incl. paraphrases and negative cases): fails if any tool, skill or guide is unreachable.

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
