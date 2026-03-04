import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities.ingestion import analyze_segment, segment_video


@workflow.defn
class IngestionWorkflow:
    @workflow.run
    async def run(self, url: str, video_id: int) -> dict:
        # Phase 1: Segment the video
        segment_infos = await workflow.execute_activity(
            segment_video,
            {"video_id": video_id, "url": url},
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        # Phase 2: Transcribe & analyze each segment
        analyzed = 0
        batch_size = 5

        for i in range(0, len(segment_infos), batch_size):
            batch = segment_infos[i : i + batch_size]
            analyze_tasks = [
                workflow.execute_activity(
                    analyze_segment,
                    {
                        "segment_id": info["segment_id"],
                        "url": url,
                        "start_time": info["start_time"],
                        "end_time": info["end_time"],
                    },
                    start_to_close_timeout=timedelta(minutes=30),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                for info in batch
            ]
            await asyncio.gather(*analyze_tasks)
            analyzed += len(analyze_tasks)

        return {
            "segment_count": len(segment_infos),
            "analyzed": analyzed,
            "transcribed": analyzed,
        }



# NOTE: RefinementWorkflow and _run_refinement disabled — the refinement
# pipeline was producing worse results than the initial segmentation pass.
# Code preserved in activities/ingestion.py for future re-enablement.
