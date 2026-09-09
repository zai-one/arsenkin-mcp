from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

SCOPES = frozenset({"arsenkin:read", "arsenkin:execute"})


def secret_file(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        return ""
    path = Path(value)
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("credential file missing or too large")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("credential file must be owner-private")
    result = path.read_text(encoding="utf-8").strip()
    if not result:
        raise ValueError("credential file empty")
    return result


@dataclass(frozen=True)
class ServiceConfig:
    state_path: Path
    account_id: str = "default"
    principal_id: str = "local-operator"
    token: str = field(default="", repr=False)
    public_key: str = ""
    issuer: str = "arsenkin-operator"
    audience: str = "arsenkin-mcp"
    local_scopes: frozenset[str] = SCOPES
    arsenkin_cost_per_query: float = 4.0
    arsenkin_auto_paid_policy: str = "manual"
    arsenkin_standing_policy_id: str = "arsenkin-standing-v1"
    arsenkin_max_cost_per_approval: float = 0
    arsenkin_max_inflight: int = 5
    arsenkin_server_queue_target: int = 50
    arsenkin_micro_batch_window_seconds: int = 5
    arsenkin_poll_interval_seconds: int = 60
    arsenkin_rate_limit_cooldown_seconds: int = 120
    principal_monthly_cost_limit: float = 0
    account_monthly_cost_limit: float = 0
    request_rate_limit: int = 30
    principal_rate_limit: int = 30
    max_concurrency: int = 2

    def __post_init__(self) -> None:
        if not all(
            isinstance(v, str) and 1 <= len(v) <= 256
            for v in (self.account_id, self.principal_id, self.issuer, self.audience)
        ):
            raise ValueError("bounded identity fields required")
        if not isinstance(self.token, str) or len(self.token) > 4096:
            raise ValueError("invalid credential")
        if not self.local_scopes <= SCOPES:
            raise ValueError("unknown local scope")
        if self.arsenkin_auto_paid_policy not in {"manual", "unlimited"}:
            raise ValueError("unknown paid policy")
        if not 1 <= len(self.arsenkin_standing_policy_id) <= 128:
            raise ValueError("bounded standing policy id required")
        if not 1 <= self.principal_rate_limit <= self.request_rate_limit <= 30:
            raise ValueError("request limit must be within original ceiling")
        if not 1 <= self.max_concurrency <= 5 or not 1 <= self.arsenkin_max_inflight <= 5:
            raise ValueError("invalid concurrency ceiling")
        if not 1 <= self.arsenkin_server_queue_target <= 50:
            raise ValueError("invalid durable backlog ceiling")
        if not 1 <= self.arsenkin_micro_batch_window_seconds <= 30:
            raise ValueError("invalid queue delay")
        if not 30 <= self.arsenkin_poll_interval_seconds <= 900:
            raise ValueError("invalid polling interval")
        if not 60 <= self.arsenkin_rate_limit_cooldown_seconds <= 3600:
            raise ValueError("invalid rate-limit cooldown")
        for value in (
            self.arsenkin_cost_per_query,
            self.arsenkin_max_cost_per_approval,
            self.principal_monthly_cost_limit,
            self.account_monthly_cost_limit,
        ):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1_000_000:
                raise ValueError("finite nonnegative cost limits required")
            scaled = Decimal(str(value)) * 1_000_000
            if scaled != scaled.to_integral_value():
                raise ValueError("cost precision limited to six decimals")
        if self.arsenkin_cost_per_query <= 0:
            raise ValueError("positive estimate price required")

    @property
    def secrets(self) -> tuple[str, ...]:
        return (self.token,)

    def enabled(self, provider: str) -> bool:
        return provider == "arsenkin" and bool(self.token)

    def arsenkin_unlimited_auto_paid_allowed(self, requested: set[str]) -> bool:
        return self.arsenkin_auto_paid_policy == "unlimited" and bool(requested)

    @classmethod
    def from_env(cls) -> ServiceConfig:
        public = os.environ.get("ARSENKIN_MCP_PUBLIC_KEY_FILE", "")
        return cls(
            state_path=Path(os.environ.get("ARSENKIN_STATE_PATH", "state/arsenkin.sqlite")),
            account_id=os.environ.get("ARSENKIN_ACCOUNT_ID", "default"),
            principal_id=os.environ.get("ARSENKIN_LOCAL_PRINCIPAL", "local-operator"),
            token=secret_file("ARSENKIN_TOKEN_FILE"),
            public_key=Path(public).read_text(encoding="utf-8") if public else "",
            issuer=os.environ.get("ARSENKIN_MCP_ISSUER", "arsenkin-operator"),
            audience=os.environ.get("ARSENKIN_MCP_AUDIENCE", "arsenkin-mcp"),
            arsenkin_auto_paid_policy=os.environ.get("ARSENKIN_AUTO_PAID_POLICY", "manual"),
            arsenkin_standing_policy_id=os.environ.get("ARSENKIN_STANDING_POLICY_ID", "arsenkin-standing-v1"),
            **{
                name: float(
                    os.environ.get(
                        name.upper() if name.startswith("arsenkin_") else "ARSENKIN_" + name.upper(),
                        str(default),
                    )
                )
                for name, default in {
                    "arsenkin_cost_per_query": 4,
                    "arsenkin_max_cost_per_approval": 0,
                    "principal_monthly_cost_limit": 0,
                    "account_monthly_cost_limit": 0,
                }.items()
            },
            **{
                name: int(os.environ.get("ARSENKIN_" + name.upper(), str(default)))
                for name, default in {
                    "request_rate_limit": 30,
                    "principal_rate_limit": 30,
                    "max_concurrency": 2,
                }.items()
            },
            **{
                name: int(os.environ.get(name.upper(), str(default)))
                for name, default in {
                    "arsenkin_max_inflight": 5,
                    "arsenkin_server_queue_target": 50,
                    "arsenkin_micro_batch_window_seconds": 5,
                    "arsenkin_poll_interval_seconds": 60,
                    "arsenkin_rate_limit_cooldown_seconds": 120,
                }.items()
            },
            local_scopes=frozenset(
                os.environ.get("ARSENKIN_LOCAL_SCOPES", "arsenkin:read arsenkin:execute").split()
            ),
        )
