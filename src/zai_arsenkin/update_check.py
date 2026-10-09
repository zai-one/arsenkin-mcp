"""Read-only update suggestion: compare the installed version with the latest GitHub release.

Never installs anything. One unauthenticated HTTPS GET to the public GitHub API, a short
timeout, a ~24h local cache and graceful failure offline. Opt out with
ARSENKIN_DISABLE_UPDATE_CHECK=1.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from zai_arsenkin import __version__

REPOSITORY = "zai-one/arsenkin-mcp"
DISTRIBUTION = "zai-arsenkin-mcp"
WHEEL_PREFIX = "zai_arsenkin_mcp"
DISPLAY_NAME = "Arsenkin MCP"
CHECK_TOOL = "arsenkin_check_update"
ENV_DISABLE = "ARSENKIN_DISABLE_UPDATE_CHECK"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
CACHE_FILE = "update-check.json"
CACHE_TTL_SECONDS = 24 * 3600
FORCE_MIN_INTERVAL_SECONDS = 300
TIMEOUT_SECONDS = 3.0
_TAG = re.compile(r"v?(\d{1,6}(?:\.\d{1,6}){0,3})\Z")
_TRUE = {"1", "true", "yes", "on"}

Fetch = Callable[[], dict[str, Any]]


def disabled() -> bool:
    return os.environ.get(ENV_DISABLE, "").strip().casefold() in _TRUE


def parse_version(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    match = _TAG.fullmatch(value.strip())
    if match is None:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def update_command(version: str) -> str:
    wheel = f"{WHEEL_PREFIX}-{version}-py3-none-any.whl"
    url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{wheel}"
    return f'uv tool install --force "{DISTRIBUTION} @ {url}"'


def fetch_latest_release() -> dict[str, Any]:
    response = httpx.get(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{DISTRIBUTION}/{__version__} update-check",
        },
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    response.raise_for_status()
    if len(response.content) > 1_000_000:
        raise ValueError("release metadata too large")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("unexpected release metadata")
    return payload


def _release_record(payload: dict[str, Any], now: float) -> dict[str, Any]:
    tag = payload.get("tag_name")
    version = parse_version(tag)
    if version is None or payload.get("draft") or payload.get("prerelease"):
        raise ValueError("latest release has no stable version tag")
    url = payload.get("html_url")
    if not isinstance(url, str) or not url.startswith(f"https://github.com/{REPOSITORY}/releases/"):
        url = RELEASES_URL
    return {"latest_version": str(tag).strip().removeprefix("v"), "release_url": url, "checked_at": now}


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > 4096:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or parse_version(data.get("latest_version")) is None
        or not isinstance(data.get("checked_at"), (int, float))
        or not isinstance(data.get("release_url"), str)
        or not data["release_url"].startswith(f"https://github.com/{REPOSITORY}/releases")
    ):
        return None
    return data


def _write_cache(path: Path, record: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass  # The cache is an optimisation; never fail the caller.


def _view(record: dict[str, Any], source: str) -> dict[str, Any]:
    latest = record["latest_version"]
    current, newest = parse_version(__version__), parse_version(latest)
    available = bool(current is not None and newest is not None and newest > current)
    return {
        "enabled": True,
        "status": "ok",
        "source": source,
        "current_version": __version__,
        "latest_version": latest,
        "update_available": available,
        "release_url": record["release_url"],
        "update_command": update_command(latest) if available else None,
        "checked_at_unix": int(record["checked_at"]),
        "auto_update": False,
        "note": "Suggestion only; nothing is installed automatically. Restart the MCP client after updating.",
    }


def check_update(
    cache_path: Path, *, force: bool = False, fetch: Fetch = fetch_latest_release, now: float | None = None
) -> dict[str, Any]:
    """Return current/latest version and an update command; never raises."""
    if disabled():
        return {
            "enabled": False,
            "status": "disabled",
            "current_version": __version__,
            "update_available": None,
            "note": f"Update checks are disabled by {ENV_DISABLE}.",
        }
    now = time.time() if now is None else now
    cached = _read_cache(cache_path)
    if cached is not None:
        age = now - cached["checked_at"]
        fresh = 0 <= age < CACHE_TTL_SECONDS
        if (fresh and not force) or (force and 0 <= age < FORCE_MIN_INTERVAL_SECONDS):
            return _view(cached, "cache")
    try:
        record = _release_record(fetch(), now)
    except Exception as exc:  # offline, rate limited, malformed: degrade quietly
        result: dict[str, Any] = {
            "enabled": True,
            "status": "unavailable",
            "current_version": __version__,
            "update_available": None,
            "error": type(exc).__name__,
            "releases_url": RELEASES_URL,
        }
        if cached is not None:
            result["last_known"] = _view(cached, "stale_cache")
        return result
    _write_cache(cache_path, record)
    return _view(record, "github")


def cached_hint(cache_path: Path) -> str | None:
    """Short startup hint from the local cache only; never touches the network."""
    if disabled():
        return None
    cached = _read_cache(cache_path)
    if cached is None:
        return None
    view = _view(cached, "cache")
    if not view["update_available"]:
        return None
    return (
        f"{DISPLAY_NAME} {view['latest_version']} is available (installed {__version__}). "
        f"Mention it once to the user if relevant; {CHECK_TOOL} returns release notes and the update command."
    )


def refresh_in_background(cache_path: Path) -> threading.Thread | None:
    """Refresh a stale cache in a daemon thread so the next start can show a hint."""
    if disabled():
        return None
    cached = _read_cache(cache_path)
    if cached is not None and 0 <= time.time() - cached["checked_at"] < CACHE_TTL_SECONDS:
        return None
    thread = threading.Thread(target=check_update, args=(cache_path,), name="update-check", daemon=True)
    thread.start()
    return thread
