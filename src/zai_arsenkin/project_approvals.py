"""Operator approvals for paid or writing calls: prepare -> operator accepts outside MCP -> apply.

The MCP tool only *prepares* an approval (stores the request digest). The operator accepts it with
``arsenkin-projects-approval --approval-id ID --digest D --accept``; the agent cannot. Apply
succeeds once, only for the exact same request, before the approval expires.

The server and the operator CLI are separate processes sharing one JSON file, so every
read-modify-write runs under an exclusive lock: without it an acceptance written from a stale
read could resurrect an approval the server had just consumed (a second paid call).

Approvals are bound to the account (account id, credential fingerprint, provider URL and state
database) and the file is namespaced per state database, so an approval from one account can never
authorize the same request in another. Consuming an approval turns it into a durable dispatch
record: a completed write replays its saved result, a write whose outcome is unknown (response lost
after sending) blocks a new approval for the same request until the operator resolves it with
``--resolve applied|not_applied``. Unaccepted approvals are bounded per principal, globally and in
bytes, so preparation alone cannot grow the shared file without limit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

TTL_SECONDS = 30 * 60
OUTCOME_TTL_SECONDS = 24 * 60 * 60  # completed/rejected dispatch records, for idempotent replay
UNRESOLVED = frozenset({"dispatching", "unknown"})  # never expire: the operator must resolve them
MAX_PENDING_PER_PRINCIPAL = 10
MAX_PENDING_TOTAL = 100
MAX_REQUEST_BYTES = 2 * 1024 * 1024  # = the transport's request ceiling
MAX_STORE_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 64 * 1024


class ApprovalQuotaExceeded(Exception):
    """Too many unaccepted approvals, or the request/store is too large to keep."""


class OutcomeUnresolved(Exception):
    """An identical request was sent and its outcome is unknown; the operator must resolve it."""

    def __init__(self, approval_id: str) -> None:
        super().__init__(f"approval {approval_id} has an unknown provider outcome")
        self.approval_id = approval_id


def request_digest(tool: str, request: dict[str, Any], account: str = "") -> str:
    body = json.dumps(
        {"tool": tool, "request": request, "account": account},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def approvals_path(state_path: Path) -> Path:
    """One approvals file per state database (a.sqlite and b.sqlite never share approvals)."""
    state_path = Path(state_path)
    return state_path.with_name(state_path.name + ".projects-approvals.json")


def account_binding(account_id: str, state_path: Path, token: str, provider_url: str) -> str:
    """Opaque account identity bound into every approval digest; never stores the credential."""
    credential = hashlib.sha256(("arsenkin-projects-approval:" + token).encode("utf-8")).hexdigest()
    body = json.dumps(
        [account_id, str(Path(state_path).absolute()), provider_url, credential], ensure_ascii=False
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


class ApprovalStore:
    def __init__(self, path: Path, account: str = "", account_id: str | None = None) -> None:
        self.path = Path(path)
        self.account, self.account_id = account, account_id

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Exclusive cross-process lock on a sidecar file (fcntl on POSIX, msvcrt on Windows)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path.with_name(self.path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                        break
                    except OSError:  # LK_LOCK gives up after ~10 s; keep waiting
                        continue
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # closing the descriptor releases the lock

    def operator_command(self, program: str, ticket: dict[str, Any]) -> str:
        """The exact accept command, pinned to this store's absolute path (cwd/env independent)."""
        return " ".join(
            [
                program,
                "--path",
                shlex.quote(str(self.path.absolute())),
                "--approval-id",
                ticket["approval_id"],
                "--digest",
                ticket["digest"],
                "--accept",
            ]
        )

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        now = time.time()
        return {
            k: v
            for k, v in data.items()
            if isinstance(v, dict) and (v.get("state") in UNRESOLVED or v.get("expires", 0) > now)
        }

    def _save(self, data: dict[str, dict[str, Any]], text: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".approvals-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data) if text is None else text)
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def prepare(
        self, tool: str, request: dict[str, Any], summary: str, principal: str | None = None
    ) -> dict[str, Any]:
        approval_id = uuid4().hex
        digest = request_digest(tool, request, self.account)
        if _size(request) > MAX_REQUEST_BYTES:
            raise ApprovalQuotaExceeded("request is too large for an approval; split it into smaller writes")
        with self._locked():
            data = self._load()
            for key, record in data.items():
                if (
                    record.get("state") in UNRESOLVED
                    and record.get("digest") == digest
                    and record.get("principal") == principal
                ):
                    raise OutcomeUnresolved(key)
            waiting = [record for record in data.values() if "state" not in record]
            if len(waiting) >= MAX_PENDING_TOTAL or (
                sum(record.get("principal") == principal for record in waiting) >= MAX_PENDING_PER_PRINCIPAL
            ):
                raise ApprovalQuotaExceeded(
                    "too many approvals waiting for the operator; accept or let them expire (30 min)"
                )
            data[approval_id] = {
                "tool": tool,
                "digest": digest,
                "summary": summary[:500],
                # The operator sees exactly what the digest covers (--list), not just a summary.
                "request": request,
                "principal": principal,
                "account": self.account,
                "account_id": self.account_id,
                "accepted": False,
                "expires": time.time() + TTL_SECONDS,
            }
            text = json.dumps(data)
            if len(text.encode("utf-8")) > MAX_STORE_BYTES:
                raise ApprovalQuotaExceeded("approval store is full; accept or let pending approvals expire")
            self._save(data, text)
        return {"approval_id": approval_id, "digest": digest, "expires_in_s": TTL_SECONDS}

    def accept(self, approval_id: str, digest: str) -> dict[str, Any]:
        with self._locked():
            data = self._load()
            record = data.get(approval_id)
            if record is None or record["digest"] != digest or "state" in record:
                raise ValueError("approval not found, expired, already used or digest mismatch")
            record["accepted"] = True
            self._save(data)
        return {
            "approval_id": approval_id,
            "tool": record["tool"],
            "summary": record["summary"],
            "request": record.get("request"),
        }

    def consume(
        self, approval_id: str, tool: str, request: dict[str, Any], principal: str | None = None
    ) -> dict[str, Any] | None:
        """Claim an accepted approval for dispatch: same tool, account, principal and exact request.

        Returns None when the caller may dispatch now (the approval became a durable "dispatching"
        record). An approval that was already used returns its dispatch record instead (state
        completed/rejected/unknown/dispatching) so the caller replays it without a second call.
        """
        with self._locked():
            data = self._load()
            record = data.get(str(approval_id))
            if record is None:
                raise PermissionError("approval is not accepted by the operator (or expired)")
            if (
                record["tool"] != tool
                or record["digest"] != request_digest(tool, request, self.account)
                or record.get("principal") != principal
                or record.get("account", "") != self.account
            ):
                raise PermissionError("approval does not match this exact request")
            if "state" in record:
                return {"state": record["state"], "result": record.get("result")}
            if not record.get("accepted"):
                raise PermissionError("approval is not accepted by the operator (or expired)")
            record["state"], record["updated"] = "dispatching", time.time()
            self._save(data)
        return None

    def finish(self, approval_id: str, state: str, result: Any = None) -> None:
        """Record the dispatch outcome: completed, rejected (provider surely did nothing) or unknown."""
        if state not in {"completed", "rejected", "unknown"}:
            raise ValueError("unknown dispatch state")
        with self._locked():
            data = self._load()
            record = data.get(str(approval_id))
            if record is None:
                return
            self._settle(record, state)
            if state == "completed" and result is not None and _size(result) <= MAX_RESULT_BYTES:
                record["result"] = result
            self._save(data)

    @staticmethod
    def _settle(record: dict[str, Any], state: str) -> None:
        record["state"], record["updated"] = state, time.time()
        if state not in UNRESOLVED:
            record["expires"] = time.time() + OUTCOME_TTL_SECONDS
            record.pop("request", None)  # keep the digest and summary, not the full body

    def resolve(self, approval_id: str, applied: bool) -> dict[str, Any]:
        """Operator reconciliation of an unknown outcome after checking the provider."""
        with self._locked():
            data = self._load()
            record = data.get(approval_id)
            if record is None or record.get("state") not in UNRESOLVED:
                raise ValueError("no unresolved dispatch with this approval id")
            self._settle(record, "completed" if applied else "rejected")
            record["resolved_by_operator"] = True
            self._save(data)
        return {"approval_id": approval_id, "state": record["state"], "summary": record["summary"]}

    def pending(self) -> list[dict[str, Any]]:
        with self._locked():
            data = self._load()
        return [
            {"approval_id": k, **v} for k, v in data.items() if not v.get("accepted") and "state" not in v
        ]

    def unresolved(self) -> list[dict[str, Any]]:
        """Sent writes without a known outcome; they block an identical request until resolved."""
        with self._locked():
            data = self._load()
        return [{"approval_id": k, **v} for k, v in data.items() if v.get("state") in UNRESOLVED]


def main(argv: list[str] | None = None, *, default_path: Path) -> int:
    parser = argparse.ArgumentParser(description="Operator approval for paid/writing MCP calls")
    parser.add_argument("--path", type=Path, default=default_path)
    parser.add_argument("--list", action="store_true", help="show pending and unresolved approvals")
    parser.add_argument("--approval-id")
    parser.add_argument("--digest")
    parser.add_argument("--accept", action="store_true")
    parser.add_argument(
        "--resolve",
        choices=("applied", "not_applied"),
        help="after checking the provider, settle a write whose outcome is unknown",
    )
    args = parser.parse_args(argv)
    store = ApprovalStore(args.path)
    if args.resolve:
        if not args.approval_id:
            parser.error("--resolve needs --approval-id")
        print(json.dumps(store.resolve(args.approval_id, args.resolve == "applied"), ensure_ascii=False))
        return 0
    if args.list or not args.accept:
        print(json.dumps(store.pending() + store.unresolved(), ensure_ascii=False, indent=2))
        return 0
    if not args.approval_id or not args.digest:
        parser.error("--accept needs --approval-id and --digest")
    print(json.dumps(store.accept(args.approval_id, args.digest), ensure_ascii=False))
    return 0


def default_path() -> Path:
    return approvals_path(Path(os.environ.get("ARSENKIN_STATE_PATH", "state/arsenkin.sqlite")))


def cli() -> int:
    return main(default_path=default_path())


if __name__ == "__main__":
    raise SystemExit(cli())
