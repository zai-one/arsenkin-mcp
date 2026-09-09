🇬🇧 English · [🇷🇺 Русский](README.ru.md)

# Arsenkin MCP

MCP server for SEO tasks in Arsenkin Tools. Prepare and approve paid requests, run individual or batch jobs, and retrieve saved results from an AI assistant.

## What you can do

- Estimate and prepare supported requests before submitting them.
- Submit jobs, check their status, retrieve results and request cancellation.
- Export saved results as CSV or JSONL without starting another paid job.

## Quick start

Install Python 3.12+ (below 3.15), [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git.

You need an Arsenkin API token. Paid jobs also require configured cost limits, approval and a separate worker using the same configuration and state database. Follow the [worker setup](INSTALL.md#from-a-clone-or-source-zip).

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

## Scope and limits

CSV and JSONL exports represent saved values by their JSON paths. They are not specialised SEO tables with predefined columns for each task type. See [job execution and cost controls](docs/RUNTIME.md).

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
