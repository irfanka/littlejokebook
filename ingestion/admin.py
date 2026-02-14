import asyncio
import os

from django.contrib import admin, messages
from temporalio.client import Client

from .models import Video

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = "little-jokebook"


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ("url", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    actions = ["ingest"]

    @admin.action(description="Ingest selected videos")
    def ingest(self, request, queryset):
        loop = asyncio.new_event_loop()
        try:
            client = loop.run_until_complete(Client.connect(TEMPORAL_ADDRESS))
            for video in queryset:
                loop.run_until_complete(
                    client.start_workflow(
                        "IngestionWorkflow",
                        video.url,
                        id=f"ingest-{video.pk}",
                        task_queue=TASK_QUEUE,
                    )
                )
            self.message_user(
                request,
                f"Started ingestion for {queryset.count()} video(s).",
                messages.SUCCESS,
            )
        finally:
            loop.close()
