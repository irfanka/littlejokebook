import asyncio
import os

from django.contrib import admin, messages
from django.utils.safestring import mark_safe
from temporalio.client import Client

from ingestion.models import IngestionRun

from .models import Comedian, Segment, SegmentComedian, Video

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
TASK_QUEUE = "little-jokebook"


class SegmentInline(admin.TabularInline):
    model = Segment
    extra = 0
    show_change_link = True
    fields = (
        "formatted_start_time",
        "formatted_end_time",
        "segment_type",
        "description",
        "summary",
        "formatted_transcript",
    )
    readonly_fields = (
        "formatted_start_time",
        "formatted_end_time",
        "segment_type",
        "description",
        "summary",
        "formatted_transcript",
    )
    ordering = ("start_time",)

    @staticmethod
    def _format_seconds(total_seconds: int | None) -> str:
        if total_seconds is None:
            return "-"
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @admin.display(description="Start")
    def formatted_start_time(self, obj):
        return self._format_seconds(getattr(obj, "start_time", None))

    @admin.display(description="End")
    def formatted_end_time(self, obj):
        return self._format_seconds(getattr(obj, "end_time", None))

    @admin.display(description="Transcript")
    def formatted_transcript(self, obj):
        if not obj or not obj.transcript:
            return "-"
        lines = []
        for line in obj.transcript:
            m, s = divmod(line["timestamp"], 60)
            lines.append(f"<b>{m:02d}:{s:02d}</b> <b>{line['speaker']}</b>: {line['text']}")
        return mark_safe("<br>".join(lines))


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ("url", "segment_count", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    inlines = [SegmentInline]
    actions = ["ingest"]

    @admin.display(description="Segments")
    def segment_count(self, obj):
        return obj.segments.count()

    @admin.action(description="Ingest selected videos")
    def ingest(self, request, queryset):
        loop = asyncio.new_event_loop()
        try:
            client = loop.run_until_complete(Client.connect(TEMPORAL_ADDRESS))
            for video in queryset:
                run = IngestionRun.objects.create(video=video)
                loop.run_until_complete(
                    client.start_workflow(
                        "IngestionWorkflow",
                        args=[video.url, video.pk],
                        id=run.workflow_id,
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


@admin.register(Segment)
class SegmentAdmin(admin.ModelAdmin):
    list_display = ("video", "start_time", "end_time", "segment_type", "description")
    list_filter = ("segment_type", "video")
    readonly_fields = ("formatted_transcript",)
    ordering = ("video", "start_time")

    @admin.display(description="Transcript")
    def formatted_transcript(self, obj):
        if not obj.transcript:
            return "-"
        lines = []
        for line in obj.transcript:
            m, s = divmod(line["timestamp"], 60)
            lines.append(f"<b>{m:02d}:{s:02d}</b> <b>{line['speaker']}</b>: {line['text']}")
        return mark_safe("<br>".join(lines))


class SegmentComedianInline(admin.TabularInline):
    model = SegmentComedian
    extra = 0
    readonly_fields = ("segment",)


@admin.register(Comedian)
class ComedianAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at", "updated_at")
    search_fields = ("name",)
    inlines = [SegmentComedianInline]
