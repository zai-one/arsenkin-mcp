🇬🇧 English · [🇷🇺 Русский](README.ru.md)

# Arsenkin MCP

**Run your SEO checks in batches from your AI assistant.**

Collect SERPs, check keyword frequency, cluster queries or inspect indexation through Arsenkin Tools. The MCP server gives your assistant a workflow for estimating requests, getting approval, tracking jobs and working with saved results.

[Quick start](#quick-start) · [Connect your assistant](#connect-your-assistant) · [Issues](https://github.com/zai-one/arsenkin-mcp/issues)

Try asking your assistant:

> Prepare a frequency-check batch for my keyword list. Show the request plan and estimated cost before submitting anything.

## What you can do

| Your task | What the MCP server provides |
|---|---|
| Research keywords | Keyword frequency, related phrases, demand trends, search suggestions and clustering. |
| Inspect search visibility | SERP, position, indexation and relevant-URL checks. |
| Manage a batch | Discover profiles, estimate, approve, submit, diagnose the worker and export saved results. |
| Read the result | URL/host occurrence tables and cluster records with source pointers, in JSON or CSV. |
| Review saved projects | `arsenkin_projects` reads your SEO projects through the [Projects API](https://help.arsenkin.ru/projects-api): regions, competitors, keywords, groups, saved positions and visibility/top/average/median charts. It never starts a position check. |
| Check remaining limits | `arsenkin_limits` returns the account's total and spent limits before you prepare paid tasks. |

[Saved-result examples and worker help](docs/RESULT_TABLES.md) show how to turn a completed SEO task into a useful table.

## Quick start

Prefer a ready package? [Install the release and generate your client configuration](INSTALL.md#install-a-release-package). No source checkout is required.

Install **Python 3.12–3.14** and [uv](https://docs.astral.sh/uv/getting-started/installation/). Clone with Git or [download the ZIP](https://github.com/zai-one/arsenkin-mcp/archive/refs/heads/main.zip). With a ZIP, open the extracted directory and skip the first two commands.

Have your Arsenkin API token ready for the configuration wizard. Start with a local request estimate, as shown below; enable paid execution after reviewing the access and limits section.

```sh
git clone https://github.com/zai-one/arsenkin-mcp.git
cd arsenkin-mcp
uv sync --frozen --extra standalone
uv run --frozen --extra standalone python scripts/configure.py
uv run --frozen --extra standalone arsenkin-mcp --config mcp.local.json --check-config
```

The wizard creates a local configuration and stores secrets in private files. It refuses to overwrite an existing setup. `--check-config` validates local settings; use `arsenkin_status` separately to check account connectivity.

## Connect your assistant

Add this configuration to an MCP client that uses `mcpServers`, such as Claude Desktop or Cursor. Replace `/ABSOLUTE/PATH/` with your absolute path; Windows JSON paths can use forward slashes, such as `D:/Tools/`.

```json
{
  "mcpServers": {
    "arsenkin": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/arsenkin-mcp",
        "run",
        "--frozen",
        "--extra",
        "standalone",
        "arsenkin-mcp",
        "--config",
        "/ABSOLUTE/PATH/arsenkin-mcp/mcp.local.json"
      ]
    }
  }
}
```

The client starts the MCP server for you. Refresh its tool list, then make your first request. For clients with a different config format, reuse the same `command` and `args`; `uv` must be available to the client process.

### First request

> Prepare an estimate for a frequency-check request with one phrase. Ask me for the region and frequency type; do not submit a paid job.

An estimate validates the request locally and shows the server cost estimate without calling Arsenkin. Submitting a paid job is a separate step. Cost estimates use configured policy units, not a guaranteed invoice amount or a ruble quote.

If tools do not appear, check the absolute path, whether the client can find `uv`, and the `--check-config` result. For access errors, check account credentials and permissions. [Installation and troubleshooting](INSTALL.md).


<!-- agent-block:start -->
## Install in your agent

Run `arsenkin-mcp` straight from GitHub with [uv](https://docs.astral.sh/uv/). Pin a release tag (`@vX.Y.Z`) for
reproducible installs; placeholders below are examples, keep secrets in files outside the repo.

**Claude Code**

```bash
claude mcp add arsenkin --env ARSENKIN_TOKEN_FILE=/path/to/arsenkin.token --env ARSENKIN_STATE_PATH=/path/to/arsenkin.sqlite -- uvx --from git+https://github.com/zai-one/arsenkin-mcp arsenkin-mcp
```

Skills for Claude Code: copy `skills/*` into `~/.claude/skills/` (or install this repo as a plugin — it
ships `.claude-plugin/plugin.json` with the skills and the MCP server).

**Cursor** — `.cursor/mcp.json` ([one-click install](cursor://anysphere.cursor-deeplink/mcp/install?name=arsenkin&config=eyJjb21tYW5kIjogInV2eCIsICJhcmdzIjogWyItLWZyb20iLCAiZ2l0K2h0dHBzOi8vZ2l0aHViLmNvbS96YWktb25lL2Fyc2Vua2luLW1jcCIsICJhcnNlbmtpbi1tY3AiXSwgImVudiI6IHsiQVJTRU5LSU5fVE9LRU5fRklMRSI6ICIvcGF0aC90by9hcnNlbmtpbi50b2tlbiIsICJBUlNFTktJTl9TVEFURV9QQVRIIjogIi9wYXRoL3RvL2Fyc2Vua2luLnNxbGl0ZSJ9fQ==)):

```json
{
  "mcpServers": {
    "arsenkin": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/zai-one/arsenkin-mcp",
        "arsenkin-mcp"
      ],
      "env": {
        "ARSENKIN_TOKEN_FILE": "/path/to/arsenkin.token",
        "ARSENKIN_STATE_PATH": "/path/to/arsenkin.sqlite"
      }
    }
  }
}
```

**Codex** — `~/.codex/config.toml` (skills: copy `skills/*` into `~/.codex/skills/`, or keep the
repo's `.agents/skills` link):

```toml
[mcp_servers.arsenkin]
command = "uvx"
args = ["--from", "git+https://github.com/zai-one/arsenkin-mcp", "arsenkin-mcp"]
env = { ARSENKIN_TOKEN_FILE = "/path/to/arsenkin.token", ARSENKIN_STATE_PATH = "/path/to/arsenkin.sqlite" }
```

**Claude Desktop** — `claude_desktop_config.json`: the same `mcpServers` block as Cursor.

## Skills (agent playbooks)

Each skill is a self-contained folder `skills/<name>/` (`SKILL.md` + its own `references/`,
`templates/`); copy the folder and it works. Agents load the short `SKILL.md` only when the task matches
and open the other files on demand. Clients without skills get the same flows as MCP prompts and the
`arsenkin://playbook` / `arsenkin://guide/{topic}` resources.

| Skill | What it does |
|---|---|
| `arsenkin-serp-clustering` | Cluster keywords by SERP overlap: estimate, approval, submit, poll, clusters table. |
| `arsenkin-index-check` | Yandex/Google indexation of URLs with a non-indexed report. |
| `arsenkin-text-relevance` | LSI/highlights, Title/H1 of the TOP vs your page, optional copywriting brief. |

## Toolsets and read-only mode

`ARSENKIN_TOOLSETS=all` (default) or a comma list (see `arsenkin://toolsets`); `ARSENKIN_TOOLS=a,b` adds single tools,
`ARSENKIN_DISABLED_TOOLS=a,b` removes them, `ARSENKIN_READ_ONLY=1` removes every write/paid tool.

## Example requests

- Сколько лимитов Arsenkin осталось?
- Кластеризуй этот список запросов для Москвы — сначала покажи стоимость.
- Проверь индексацию этих URL в Яндексе и Google.
- Что ТОП ожидает в тексте по запросу «ремонт киа»?
- Покажи сохранённые позиции проекта за месяц.
- Выгрузи результат задачи в CSV.
<!-- agent-block:end -->

## Access and limits

For execution, configure paid-task limits and approval, then run the worker with the same config and SQLite state. Exported values keep their JSON paths; specialised SEO tables are not generated automatically. Cancelling a job does not guarantee a refund for work already submitted.

### Updates

`arsenkin_check_update` compares the installed version with the latest [GitHub release](https://github.com/zai-one/arsenkin-mcp/releases) and returns the release link and the matching `uv tool install --force ...` command. It never installs anything and spends no Arsenkin limits. The result is cached for 24 hours next to the state database; when a newer release is cached, the server instructions and `arsenkin_local_diagnostics` mention it. Set `ARSENKIN_DISABLE_UPDATE_CHECK=1` to turn off both the tool's network request and the startup refresh.

Authenticated HTTP is available for a server deployment. See [HTTP setup](INSTALL.md#http), [configuration and permissions](docs/RUNTIME.md) and [Python package integration](INSTALL.md#python-package-and-platform-integration).

<details>
<summary>For developers: project checks</summary>

```sh
uv sync --frozen --all-groups --extra standalone
uv run --frozen --extra standalone python scripts/verify.py
uv run --frozen --extra standalone python scripts/verify_install.py
```

Tests use synthetic fixtures. A passing test run does not establish live provider connectivity.

</details>

## Built by ZAI.ONE

[ZAI.ONE](https://zai.one) is a digital agency working on websites, SEO, advertising and analytics. We also build tools that connect AI assistants to everyday work. [Talk to us on Telegram](https://t.me/zai_one) about setup, automation or an integration for your team.

## Use and feedback

You may install and use this project for your own accounts under [LicenseRef-ZAI-ONE](LICENSE).
This is not an open-source license. Third-party notices remain in [NOTICE](NOTICE).
If it helps, give the repository a ⭐. Missing something or found a bug? [Open an issue](https://github.com/zai-one/arsenkin-mcp/issues/new/choose).
I'm working on this project; accepted improvements are implemented here. Support is not guaranteed.
