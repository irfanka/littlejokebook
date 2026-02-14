from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from activities.ingestion import download_video


@workflow.defn
class IngestionWorkflow:
    @workflow.run
    async def run(self, url: str) -> str:
        return await workflow.execute_activity(
            download_video,
            url,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(minutes=5),
        )
