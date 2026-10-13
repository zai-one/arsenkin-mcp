---
name: arsenkin-text-relevance
description: Compare a page with what the TOP expects for a query - SERP highlights/LSI (sp), Title/H1 (check-h), optional copywriting brief - via paid Arsenkin jobs with approval. Use for "текстовый анализ", "LSI", "ТЗ на текст", "релевантность страницы".
license: LicenseRef-ZAI-ONE
compatibility: Requires arsenkin-mcp with a running worker.
metadata:
  service: arsenkin
---
# Text relevance

Profiles `sp`, `check-h`, `copyrighters` ([references/tools.md](references/tools.md)).

## Steps
1. Confirm query (1–5), page URL, `se`, `region`.
2. Plan 2–3 jobs: `sp` (highlights/LSI for the query, depth 10), `check-h` (meta tags of the user's URL
   and TOP URLs), optional `copyrighters` (brief). Get payload fields from `arsenkin_profile_catalog`.
3. `arsenkin_batch_estimate` + `arsenkin_limits` → show total cost → explicit OK.
4. `arsenkin_batch_prepare` → `arsenkin_batch_submit` → poll `arsenkin_task_status`.
5. Read results (`arsenkin_get_result`), compare with the page, fill
   [templates/text-brief.md](templates/text-brief.md).

## Guardrails
- Ask before adding `copyrighters` (extra cost). Unknown outcome: [references/errors.md](references/errors.md).
