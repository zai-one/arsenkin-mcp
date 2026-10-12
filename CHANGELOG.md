# Changelog

## Unreleased

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
