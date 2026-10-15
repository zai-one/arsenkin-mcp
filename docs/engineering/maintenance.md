# Arsenkin MCP maintenance

Independent source of Arsenkin adapters, MCP tools and paid-job business logic.
Follow the ZAI task protocol when in that workspace; runtime must work outside it.
Use Python and synthetic offline fixtures. Tests never call real paid APIs.
Preserve ownership, scopes, approvals, budgets, durable queue and uncertain state.

Use Python for scripts. Preserve existing MCP names/schemas and server-owned
authorization, account boundaries, budgets, approvals and unknown-outcome state.
Do not use live provider accounts or credentials in tests. Keep setup local and explicit.
Run scripts/verify.py and scripts/verify_install.py before releases.
Use Issues for requested changes. Preserve LICENSE, NOTICE and third-party licenses.
Never publish operational handoffs, private extraction refs, credentials or runtime state.
Production deployment is a separate action.
When operating in a workspace with a task/verifier protocol, follow that protocol.

## Using this server from an agent

Agent playbooks live in `skills/` (self-contained `SKILL.md` + `references/`/`templates/`), mirrored as MCP
prompts and `arsenkin://playbook` / `arsenkin://guide/{topic}`. Keep `resources/guide/*.md` and copies inside
skills identical (`scripts/token_budget.py` checks this and all size budgets).
