"""Operator approvals for paid or writing calls: prepare -> operator accepts outside MCP -> apply.

The MCP tool only *prepares* an approval (stores the request digest). The operator accepts it with
``arsenkin-projects-approval --approval-id ID --digest D --accept``; the agent cannot. Apply
succeeds once, only for the exact same request, before the approval expires.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

TTL_SECONDS = 30 * 60


def request_digest(tool: str, request: dict[str, Any]) -> str:
    body = json.dumps({"tool": tool, "request": request}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class ApprovalStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        now = time.time()
        return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("expires", 0) > now}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".approvals-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def prepare(self, tool: str, request: dict[str, Any], summary: str) -> dict[str, Any]:
        data = self._load()
        approval_id = uuid4().hex
        digest = request_digest(tool, request)
        data[approval_id] = {
            "tool": tool,
            "digest": digest,
            "summary": summary[:500],
            "accepted": False,
            "expires": time.time() + TTL_SECONDS,
        }
        self._save(data)
        return {"approval_id": approval_id, "digest": digest, "expires_in_s": TTL_SECONDS}

    def accept(self, approval_id: str, digest: str) -> dict[str, Any]:
        data = self._load()
        record = data.get(approval_id)
        if record is None or record["digest"] != digest:
            raise ValueError("approval not found, expired or digest mismatch")
        record["accepted"] = True
        self._save(data)
        return {"approval_id": approval_id, "tool": record["tool"], "summary": record["summary"]}

    def consume(self, approval_id: str, tool: str, request: dict[str, Any]) -> None:
        """Single-use check: accepted, same tool and identical request, not expired."""
        data = self._load()
        record = data.get(str(approval_id))
        if record is None or not record.get("accepted"):
            raise PermissionError("approval is not accepted by the operator (or expired)")
        if record["tool"] != tool or record["digest"] != request_digest(tool, request):
            raise PermissionError("approval does not match this exact request")
        del data[str(approval_id)]
        self._save(data)

    def pending(self) -> list[dict[str, Any]]:
        return [{"approval_id": k, **v} for k, v in self._load().items() if not v.get("accepted")]


def main(argv: list[str] | None = None, *, default_path: Path) -> int:
    parser = argparse.ArgumentParser(description="Operator approval for paid/writing MCP calls")
    parser.add_argument("--path", type=Path, default=default_path)
    parser.add_argument("--list", action="store_true", help="show pending approvals")
    parser.add_argument("--approval-id")
    parser.add_argument("--digest")
    parser.add_argument("--accept", action="store_true")
    args = parser.parse_args(argv)
    store = ApprovalStore(args.path)
    if args.list or not args.accept:
        print(json.dumps(store.pending(), ensure_ascii=False, indent=2))
        return 0
    if not args.approval_id or not args.digest:
        parser.error("--accept needs --approval-id and --digest")
    print(json.dumps(store.accept(args.approval_id, args.digest), ensure_ascii=False))
    return 0


def default_path() -> Path:
    state = Path(os.environ.get("ARSENKIN_STATE_PATH", "state/arsenkin.sqlite"))
    return state.with_name("arsenkin-projects-approvals.json")


def cli() -> int:
    return main(default_path=default_path())


if __name__ == "__main__":
    raise SystemExit(cli())
