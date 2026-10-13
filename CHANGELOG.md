# Changelog

## Unreleased

- Agent layer: router `instructions` (≤1200 chars) describing the estimate → prepare → explicit OK → submit → poll → result job pattern; compact template descriptions with `Cost:` lines and titles.
- Toolsets `meta`, `jobs`, `results`, `projects` (`ARSENKIN_TOOLSETS`, `ARSENKIN_TOOLS`, `ARSENKIN_DISABLED_TOOLS`, `ARSENKIN_READ_ONLY`); `arsenkin://toolsets`.
- Resources `arsenkin://playbook`, `arsenkin://guide/{tools,clustering-params,errors}` ("which profile when" table covering all 19 documented API tools); prompts `cluster_keywords`, `index_check`, `text_relevance`.
- Self-contained skills `arsenkin-serp-clustering`, `arsenkin-index-check`, `arsenkin-text-relevance` with references and templates; Claude Code plugin manifest, Cursor rule, `.agents/skills`.
- `scripts/token_budget.py` + tests gate instruction, description and SKILL.md sizes.

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
