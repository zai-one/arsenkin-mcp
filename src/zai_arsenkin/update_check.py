"""Read-only update suggestion: compare the installed version with the latest GitHub release.

Never installs anything. One unauthenticated HTTPS GET to the public GitHub API, a short
timeout, a ~24h local cache, a 30 minute back-off after a failed fetch and graceful failure
offline. Opt out with ARSENKIN_DISABLE_UPDATE_CHECK=1.
"""

from __future__ import annotations

import json
import math
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
FAILURE_BACKOFF_SECONDS = 30 * 60
FORCE_MIN_INTERVAL_SECONDS = 60
TIMEOUT_SECONDS = 3.0
# Plain version tags only: no suffixes, whitespace or other characters ever reach a command.
_TAG = re.compile(r"v?(\d{1,6}(?:\.\d{1,6}){0,3})")
_VERSION = re.compile(r"\d{1,6}(?:\.\d{1,6}){0,3}")
_RELEASE_URL = re.compile(re.escape(RELEASES_URL) + r"(?:/tag/v?\d{1,6}(?:\.\d{1,6}){0,3})?")
_ERROR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_TRUE = {"1", "true", "yes", "on"}

Fetch = Callable[[], dict[str, Any]]


def disabled() -> bool:
    return os.environ.get(ENV_DISABLE, "").strip().casefold() in _TRUE


def parse_version(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    match = _TAG.fullmatch(value)
    if match is None:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def update_command(version: str) -> str:
    wheel = f"{WHEEL_PREFIX}-{version}-py3-none-any.whl"
    url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{wheel}"
    return f'uv tool install --force "{DISTRIBUTION}[standalone] @ {url}"'


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
    if parse_version(tag) is None or payload.get("draft") or payload.get("prerelease"):
        raise ValueError("latest release has no stable version tag")
    url = payload.get("html_url")
    if not isinstance(url, str) or _RELEASE_URL.fullmatch(url) is None:
        url = RELEASES_URL
    return {"latest_version": str(tag).removeprefix("v"), "release_url": url, "checked_at": now}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _read_cache(path: Path) -> dict[str, Any]:
    """Return {"release": validated record or None, "last_attempt_at", "last_error"}; never raises."""
    state: dict[str, Any] = {"release": None, "last_attempt_at": None, "last_error": None}
    try:
        if path.stat().st_size > 4096:
            return state
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if not isinstance(data, dict):
        return state
    latest, url = data.get("latest_version"), data.get("release_url")
    checked = _number(data.get("checked_at"))
    if (
        isinstance(latest, str)
        and _VERSION.fullmatch(latest)
        and isinstance(url, str)
        and _RELEASE_URL.fullmatch(url)
        and checked is not None
    ):
        state["release"] = {"latest_version": latest, "release_url": url, "checked_at": checked}
    attempt = _number(data.get("last_attempt_at"))
    state["last_attempt_at"] = attempt if attempt is not None else checked
    error = data.get("last_error")
    if error is not None:
        state["last_error"] = error if isinstance(error, str) and _ERROR_NAME.fullmatch(error) else "Error"
    return state


def _write_cache(path: Path, state: dict[str, Any]) -> None:
    record = {**(state["release"] or {}), "last_attempt_at": state["last_attempt_at"]}
    if state["last_error"] is not None:
        record["last_error"] = state["last_error"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
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


def _unavailable(error: str, release: dict[str, Any] | None, retry_at: float | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "enabled": True,
        "status": "unavailable",
        "current_version": __version__,
        "update_available": None,
        "error": error,
        "releases_url": RELEASES_URL,
    }
    if retry_at is not None:
        result["retry_after_unix"] = int(retry_at)
    if release is not None:
        result["last_known"] = _view(release, "stale_cache")
    return result


def _without_fetch(state: dict[str, Any], *, force: bool, now: float) -> dict[str, Any] | None:
    """Return the answer when no fetch is allowed now, or None when a fetch may run."""
    release, attempt, error = state["release"], state["last_attempt_at"], state["last_error"]
    since_attempt = None if attempt is None else now - attempt
    if force:
        if since_attempt is None or not 0 <= since_attempt < FORCE_MIN_INTERVAL_SECONDS:
            return None
        if error is None and release is not None:
            return _view(release, "cache")
        return _unavailable(error or "Throttled", release, attempt + FORCE_MIN_INTERVAL_SECONDS)
    if release is not None and 0 <= now - release["checked_at"] < CACHE_TTL_SECONDS:
        return _view(release, "cache")
    if error is not None and since_attempt is not None and 0 <= since_attempt < FAILURE_BACKOFF_SECONDS:
        return _unavailable(error, release, attempt + FAILURE_BACKOFF_SECONDS)
    return None


def check_update(
    cache_path: Path, *, force: bool = False, fetch: Fetch | None = None, now: float | None = None
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
    state = _read_cache(cache_path)
    answer = _without_fetch(state, force=force, now=now)
    if answer is not None:
        return answer
    fetch = fetch_latest_release if fetch is None else fetch  # resolved per call so tests can patch it
    try:
        record = _release_record(fetch(), now)
    except Exception as exc:  # offline, rate limited, malformed: degrade quietly
        error = type(exc).__name__
        error = error if _ERROR_NAME.fullmatch(error) else "Error"
        _write_cache(cache_path, {**state, "last_attempt_at": now, "last_error": error})
        return _unavailable(error, state["release"], now + FAILURE_BACKOFF_SECONDS)
    _write_cache(cache_path, {"release": record, "last_attempt_at": now, "last_error": None})
    return _view(record, "github")


def cached_hint(cache_path: Path) -> str | None:
    """Short startup hint from the local cache only; never touches the network."""
    if disabled():
        return None
    release = _read_cache(cache_path)["release"]
    if release is None:
        return None
    view = _view(release, "cache")
    if not view["update_available"]:
        return None
    return (
        f"{DISPLAY_NAME} {view['latest_version']} is available (installed {__version__}). "
        f"Mention it once to the user if relevant; {CHECK_TOOL} returns release notes and the update command."
    )


def refresh_in_background(cache_path: Path) -> threading.Thread | None:
    """Refresh a stale cache in a daemon thread so the next start can show a hint.

    Obeys the same cache TTL and failure back-off as check_update.
    """
    if disabled():
        return None
    if _without_fetch(_read_cache(cache_path), force=False, now=time.time()) is not None:
        return None
    thread = threading.Thread(target=check_update, args=(cache_path,), name="update-check", daemon=True)
    thread.start()
    return thread
