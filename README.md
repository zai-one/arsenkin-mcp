🇬🇧 English · [🇷🇺 Русский](README.ru.md)

# Arsenkin MCP

Arsenkin paid SEO jobs with approvals, durable queue and saved results.

Install it on your own computer or server and connect an MCP client. No AI Kit or
central ZAI platform installation is required. Provider credentials and API access
are required; provider charges and account restrictions still apply.

## Quick start

Install Python 3.12+ (below 3.15), [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git.

```sh
git clone https://github.com/zai-one/arsenkin-mcp.git
cd arsenkin-mcp
uv sync --frozen --extra standalone
uv run --frozen --extra standalone python scripts/configure.py
uv run --frozen --extra standalone arsenkin-mcp --config mcp.local.json --check-config
uv run --frozen --extra standalone arsenkin-mcp --config mcp.local.json
```

The last command starts stdio and waits for an MCP client; it is not an interactive chat.
See [INSTALL.md](INSTALL.md) for credentials, client configuration, HTTP and package integration.
`--check-config` checks local settings only; it never validates a provider account over the network.

## Included in 0.2.0

arsenkin_export_result returns bounded JSONL/CSV from saved owned jobs, with JSON pointers preserving provenance. It does not launch or pay for another job. Profile-specific SEO table views are future work.

Existing tool names and schemas remain supported. Writes and paid operations retain
their server policy and approval controls. See [runtime configuration](docs/RUNTIME.md).

## Verification

```sh
uv sync --frozen --all-groups --extra standalone
uv run --frozen --extra standalone python scripts/verify.py
uv run --frozen --extra standalone python scripts/verify_install.py
```

Tests use synthetic fixtures. A passing test run does not establish live provider connectivity.

## Use and feedback

You may install and use this project for your own accounts under [LicenseRef-ZAI-ONE](LICENSE).
This is not an open-source license. Third-party notices remain in [NOTICE](NOTICE).
If it helps, give the repository a ⭐. Missing something or found a bug? [Open an issue](https://github.com/zai-one/arsenkin-mcp/issues/new/choose).
I'm working on this project; accepted improvements are implemented here. Support is not guaranteed.
