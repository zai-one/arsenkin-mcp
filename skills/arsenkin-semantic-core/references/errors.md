# Arsenkin errors

| Situation | Action |
|---|---|
| estimate over approval/monthly budget | reduce queries or ask the operator to raise the budget |
| approval expired / payload changed | estimate + prepare again; re-confirm |
| submit outcome unknown | do NOT resubmit; `arsenkin_request_hash_lookup`, then `arsenkin_task_status` |
| job queued for long | `arsenkin_local_diagnostics` (worker heartbeat, queue) |
| provider rate limit | wait the cooldown; worker retries polling itself |
| limits exhausted | `arsenkin_limits`; tell the user |
| partial table | `complete=false` describes only the selected view |
