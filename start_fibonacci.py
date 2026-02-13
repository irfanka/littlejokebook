import asyncio
import os

from temporalio.client import Client

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = "little-jokebook"


async def main():
    client = await Client.connect(TEMPORAL_ADDRESS)
    handle = await client.start_workflow(
        "FibonacciWorkflow",
        120,
        id="fibonacci-demo",
        task_queue=TASK_QUEUE,
    )
    print(f"Started workflow: {handle.id} (run_id: {handle.result_run_id})")
    print("Waiting for result...")
    result = await handle.result()
    print(f"Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
