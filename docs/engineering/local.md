# Project development guidance

- Python 3.12+; paid Arsenkin jobs, durable queue and unknown-outcome state.
- Preserve ownership, paid-job budgets, approvals and durable reconciliation.
- Checks: `uv run python scripts/verify.py`; `python scripts/check_engineering.py --check`. Run install checks before release.

Read only the task-relevant public documentation. Personal workspace notes are optional and remain outside Git.

- [README](../../README.md): supported features and installation.
- [Contributing](../../CONTRIBUTING.md): development and release checks.

Agent usage playbooks live in `skills/` and MCP `arsenkin://playbook` / `arsenkin://guide/{topic}`. Keep topic copies and installed resources consistent; run token-budget and clean-install checks before releases.
