import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities.ingestion import analyze_segment, refine_triplet, segment_video


def _build_triplet_groups(segment_infos: list[dict]) -> list[list[dict]]:
    """Build non-overlapping groups of triplets for parallel refinement.

    Triplet centered on index i uses segments i-1, i, i+1.
    Two triplets are non-overlapping when their center indices differ by >= 3.
    We create 3 groups where centers are spaced 3 apart:
      Group 0: centers 1, 4, 7, 10, ...
      Group 1: centers 2, 5, 8, 11, ...
      Group 2: centers 3, 6, 9, 12, ...
    """
    n = len(segment_infos)
    if n < 3:
        return []

    groups: list[list[dict]] = [[], [], []]
    for i in range(1, n - 1):
        group_idx = (i - 1) % 3
        groups[group_idx].append({
            "prev_segment_id": segment_infos[i - 1]["segment_id"],
            "curr_segment_id": segment_infos[i]["segment_id"],
            "next_segment_id": segment_infos[i + 1]["segment_id"],
        })

    return [g for g in groups if g]


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

        # Phase 3: Refine segment boundaries using triplets
        refined = await _run_refinement(segment_infos)

        return {
            "segment_count": len(segment_infos),
            "analyzed": analyzed,
            "transcribed": analyzed,
            "boundaries_refined": refined,
        }


@workflow.defn
class RefinementWorkflow:
    """Standalone workflow to refine boundaries of existing segments."""

    @workflow.run
    async def run(self, video_id: int) -> dict:
        # We need segment_infos but segments already exist in DB.
        # Fetch them via a lightweight activity.
        segment_infos = await workflow.execute_activity(
            "fetch_segment_infos",
            video_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        refined = await _run_refinement(segment_infos)

        return {
            "segment_count": len(segment_infos),
            "boundaries_refined": refined,
        }


async def _run_refinement(segment_infos: list[dict]) -> int:
    """Run triplet refinement across non-overlapping groups.

    Groups are processed sequentially (to avoid races on shared segments).
    Within each group, triplets run in parallel batches of up to 5
    (staying under the Gemini 25-req/min rate limit).
    """
    groups = _build_triplet_groups(segment_infos)
    if not groups:
        return 0

    refined_count = 0
    batch_size = 5

    for group in groups:
        for batch_start in range(0, len(group), batch_size):
            batch = group[batch_start : batch_start + batch_size]
            tasks = [
                workflow.execute_activity(
                    refine_triplet,
                    triplet,
                    start_to_close_timeout=timedelta(minutes=10),
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                for triplet in batch
            ]
            results = await asyncio.gather(*tasks)
            refined_count += sum(1 for r in results if r.get("changed"))

    return refined_count
