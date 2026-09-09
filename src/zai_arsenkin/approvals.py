"""Trusted operator CLI; acceptance is not exposed as an MCP tool."""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from zai_arsenkin.config import ServiceConfig
from zai_arsenkin.onboarding import load_config
from zai_arsenkin.state import StateStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("approval_id", type=UUID)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--request-hash", required=True)
    parser.add_argument("--config", help="Operator JSON settings")
    args = parser.parse_args()
    try:
        load_config(args.config)
        accepted = StateStore(ServiceConfig.from_env()).accept(
            args.approval_id,
            principal=args.principal,
            digest=args.request_hash,
        )
    except (ValueError, OSError):
        parser.exit(2, "configuration/state error; check local files\n")
    print(json.dumps({"accepted": accepted}))
    if not accepted:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
