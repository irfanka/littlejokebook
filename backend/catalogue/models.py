from django.db import models

from core.models import TimestampMixin


class Video(TimestampMixin):
    url = models.URLField()

    def __str__(self):
        return self.url


class Segment(TimestampMixin):
    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name="segments")
    start_time = models.PositiveIntegerField(help_text="Start time in seconds")
    end_time = models.PositiveIntegerField(help_text="End time in seconds")
    segment_type = models.CharField(max_length=100)
    description = models.TextField()
    summary = models.TextField(blank=True, default="")
    transcript = models.JSONField(blank=True, default=list)

    class Meta:
        ordering = ["start_time"]

    def __str__(self):
        return f"[{self.start_time}s-{self.end_time}s] {self.segment_type}"


class Comedian(TimestampMixin):
    name = models.CharField(max_length=200, unique=True)

    def __str__(self):
        return self.name


class SegmentComedian(TimestampMixin):
    segment = models.ForeignKey(Segment, on_delete=models.CASCADE, related_name="segment_comedians")
    comedian = models.ForeignKey(Comedian, on_delete=models.CASCADE, related_name="segment_comedians")

    class Meta:
        unique_together = [("segment", "comedian")]

    def __str__(self):
        return f"{self.comedian} in {self.segment}"
