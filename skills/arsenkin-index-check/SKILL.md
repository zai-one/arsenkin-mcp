---
name: arsenkin-index-check
description: Check whether URLs are indexed in Yandex and Google with Arsenkin (paid, approval first) and report non-indexed pages. Use for "проверь индексацию", "есть ли страницы в индексе", "index check".
license: LicenseRef-ZAI-ONE
compatibility: Requires arsenkin-mcp with a running worker.
metadata:
  service: arsenkin
---
# Index check

Profile `indexation` (see [references/tools.md](references/tools.md)).

## Steps
1. Get the URL list (full URLs, deduped). Ask which engines (default both: `yandex`, `google` true).
2. `arsenkin_estimate("indexation", {"queries": urls})` + `arsenkin_limits`; show cost; wait for OK.
3. `arsenkin_prepare` → `arsenkin_submit` (idempotency_key per list) → poll `arsenkin_task_status`.
4. `arsenkin_get_result` (or `arsenkin_export_result` format=csv for >200 URLs).
5. Report: totals indexed in Yandex / Google / both / neither; table of non-indexed URLs; next steps
   (sitemap, internal links, Webmaster reindex — via yandex-mcp if connected).

## Guardrails
- Approval before spend; unknown outcome → [references/errors.md](references/errors.md).
