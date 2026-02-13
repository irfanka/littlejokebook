import asyncio
import time
from datetime import timedelta

from temporalio import activity, workflow


@activity.defn
async def compute_fibonacci(duration_seconds: int) -> dict:
    """Compute Fibonacci numbers for the given duration, reporting progress via heartbeat."""
    deadline = time.monotonic() + duration_seconds
    a, b = 0, 1
    count = 0

    while time.monotonic() < deadline:
        a, b = b, a + b
        count += 1

        if count % 500_000 == 0:
            activity.heartbeat(count)
            await asyncio.sleep(0)

    return {
        "terms_computed": count,
        "duration_seconds": duration_seconds,
    }


@workflow.defn
class FibonacciWorkflow:
    @workflow.run
    async def run(self, duration_seconds: int = 120) -> dict:
        workflow.logger.info(f"Starting Fibonacci computation for {duration_seconds}s")

        result = await workflow.execute_activity(
            compute_fibonacci,
            duration_seconds,
            start_to_close_timeout=timedelta(minutes=5),
            heartbeat_timeout=timedelta(seconds=30),
        )

        workflow.logger.info(f"Done: {result['terms_computed']:,} terms")
        return result
