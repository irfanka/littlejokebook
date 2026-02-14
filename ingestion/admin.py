from django.contrib import admin

from .models import IngestionRun


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):
    list_display = ("workflow_id", "video", "created_at")
    readonly_fields = ("run_id", "workflow_id", "created_at", "updated_at")
