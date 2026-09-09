"""Separate durable worker for the same account/configuration as the MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json

from zai_arsenkin.config import ServiceConfig
from zai_arsenkin.execution import _next_attempt_seconds, execute_cancellation, execute_job
from zai_arsenkin.onboarding import load_config
from zai_arsenkin.runtime import Runtime


class Worker:
    def __init__(self, runtime: Runtime):
        self.runtime, self.store = runtime, runtime.store

    async def run_once(self) -> bool:
        if not self.runtime.config.enabled("arsenkin"):
            return False
        claim = self.store.claim_job()
        if claim is None:
            return False
        job, owner = claim
        token = self.runtime.job_claim.set((job.job_id, owner))

        async def execute() -> None:
            if job.status == "cancel_requested":
                status, result, error = await execute_cancellation(job, self.runtime.registry)
            else:
                status, result, error = await execute_job(job, self.runtime.registry)
            result = self.runtime.clean(result, durable=True)
            delay = _next_attempt_seconds(job, self.runtime.registry, status, result, error)
            self.store.settle_job(job.job_id, owner, status=status, result=result, error=error, delay=delay)

        try:
            await self.runtime.run_owned(
                "arsenkin",
                str(job.principal_id),
                "worker:" + job.operation,
                {"job_id": str(job.job_id), "request_hash": job.request_hash},
                execute,
            )
        except asyncio.CancelledError:
            # Preserve intent/lease on shutdown. Expiry converts uncertain POST
            # to failed/unknown; known task references resume polling.
            raise
        except Exception:
            # Settlement retains newer checkpoints and turns dispatching into
            # unknown regardless of this generic fallback.
            self.store.settle_job(
                job.job_id,
                owner,
                status="waiting",
                result=None,
                error="worker_interrupted",
                delay=60,
                preserve_checkpoint=True,
            )
        finally:
            self.runtime.job_claim.reset(token)
        return True


async def run(once: bool) -> None:
    worker = Worker(Runtime(ServiceConfig.from_env(), "stdio"))
    while True:
        processed = await worker.run_once()
        if once:
            print(json.dumps({"processed": processed}))
            return
        await asyncio.sleep(2 if processed else 5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--config", help="Operator JSON settings")
    args = parser.parse_args()
    try:
        load_config(args.config)
        asyncio.run(run(args.once))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"configuration/state error: {type(exc).__name__}; check local configuration\n")


if __name__ == "__main__":
    main()
