from django.db import models

from core.models import TimestampMixin


class Video(TimestampMixin, models.Model):
    url = models.URLField()

    def __str__(self):
        return self.url
