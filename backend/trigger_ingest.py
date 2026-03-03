"""Trigger ingestion for a video."""
import asyncio
import os

os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "little_jokebook.settings")

import django
django.setup()

from temporalio.client import Client
from catalogue.models import Video
from ingestion.models import IngestionRun

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")


async def main():
    video = Video.objects.first()
    if not video:
        video = Video.objects.create(url="https://www.youtube.com/watch?v=PG_8dq4YtSQ")
        print(f"Created video {video.pk}")

    run = IngestionRun.objects.create(video=video)
    client = await Client.connect(TEMPORAL_ADDRESS)
    handle = await client.start_workflow(
        "IngestionWorkflow",
        args=[video.url, video.pk],
        id=run.workflow_id,
        task_queue="little-jokebook",
    )
    print(f"Started workflow: {handle.id}")
    result = await handle.result()
    print(f"Result: {result}")


asyncio.run(main())
