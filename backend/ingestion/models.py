import uuid

from django.db import models

from catalogue.models import Video
from core.models import TimestampMixin


class IngestionRun(TimestampMixin):
    run_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    video = models.ForeignKey(
        Video, on_delete=models.CASCADE, related_name="ingestion_runs"
    )
    workflow_id = models.CharField(max_length=200, editable=False)

    def save(self, *args, **kwargs):
        if not self.workflow_id:
            self.workflow_id = f"ingest-{self.run_id}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.workflow_id}"
