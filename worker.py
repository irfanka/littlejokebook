import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from activities.ingestion import download_video
from workflows.ingestion import IngestionWorkflow

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = "little-jokebook"


async def main():
    client = await Client.connect(TEMPORAL_ADDRESS)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[IngestionWorkflow],
        activities=[download_video],
    )
    print(f"Worker started, listening on task queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
