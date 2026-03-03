"""Trigger boundary refinement for an existing video's segments."""
import asyncio
import os
import uuid

os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "little_jokebook.settings")

import django
django.setup()

from temporalio.client import Client
from catalogue.models import Video

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")


async def main():
    video = Video.objects.first()
    if not video:
        print("No videos found.")
        return

    client = await Client.connect(TEMPORAL_ADDRESS)
    workflow_id = f"refine-{video.pk}-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        "RefinementWorkflow",
        args=[video.pk],
        id=workflow_id,
        task_queue="little-jokebook",
    )
    print(f"Started refinement workflow: {handle.id}")
    result = await handle.result()
    print(f"Result: {result}")


asyncio.run(main())
