---
name: arsenkin-serp-clustering
description: Cluster keywords by SERP overlap with Arsenkin - estimate, explicit cost approval, submit, poll, clusters table. Use for "кластеризация запросов", "сгруппируй семантику", "keyword clustering".
license: LicenseRef-ZAI-ONE
compatibility: Requires arsenkin-mcp with a running worker. Optional - keysso-mcp as keyword source.
metadata:
  service: arsenkin
---
# SERP clustering job

Paid. Parameters: [references/clustering-params.md](references/clustering-params.md).

## Steps
1. Collect queries (user list, or from keysso-mcp/Wordstat). Dedupe, lowercase, drop empties.
2. Confirm `se` (1 Яндекс / 2 Google), `region` (213 Москва), `group`/`count` (default hard/3), stoplist.
3. `arsenkin_estimate("clustering", payload)` (>1 payload: `arsenkin_batch_estimate`) + `arsenkin_limits`.
4. Show: queries count, estimate, limits left. Wait for explicit "yes/да".
5. `arsenkin_prepare` → `arsenkin_submit(..., approval_id, idempotency_key="cluster-<short-hash>")`.
6. Poll `arsenkin_task_status` every 30–60 s (tell the user it runs in background; can take minutes).
7. `arsenkin_result_table(job_id, view="clusters")` → fill
   [templates/clusters.md](templates/clusters.md); offer CSV via `arsenkin_export_result`.

## Guardrails
- Never submit without approval; never resubmit an unknown outcome
  ([references/errors.md](references/errors.md)).
- Changing parameters after approval = new estimate and new OK.
