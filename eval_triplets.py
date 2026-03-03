import asyncio
import json
import os

import django
import litellm
from django.conf import settings
from litellm import Router
from pydantic import BaseModel

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "little_jokebook.settings")
django.setup()

from catalogue.models import Segment, Video


class EvalResult(BaseModel):
    is_correct: bool
    reason: str


EVAL_PROMPT = """\
You are an expert evaluator of video segmentation pipelines.
We are checking if the boundaries of the CURRENT SEGMENT are correctly placed.
To provide context, you get PREVIOUS and NEXT segment metadata.

PREVIOUS SEGMENT:
{prev_info}

CURRENT SEGMENT:
Type: {curr_type}
Description: {curr_desc}
Summary: {curr_summary}
Time: {curr_start}s to {curr_end}s

NEXT SEGMENT:
{next_info}

--- FULL TRANSCRIPT OF CURRENT SEGMENT ---
{transcript}
------------------------------------------

Task: Decide whether CURRENT SEGMENT boundaries are materially correct.

IMPORTANT: Do NOT be overzealous.
Small handoff chatter is acceptable.
Examples of acceptable boundary noise:
- host saying goodbye / one-liner transition
- a quick intro line before comedian starts
- brief applause/reaction lines
- tiny overlap at start/end (< ~20 seconds)

Only mark INCORRECT for MATERIAL boundary errors, such as:
1) WRONG EVENT DOMINATES START: substantial early portion belongs to previous segment (roughly >20% of segment OR >45 seconds).
2) WRONG EVENT DOMINATES END: substantial late portion belongs to next segment (roughly >20% of segment OR >45 seconds).
3) MAJOR MISSING START: current event clearly starts much later, so beginning is mostly wrong-event content.
4) MAJOR MISSING END: segment cuts off clearly before current event meaningfully concludes.

Heuristic priority:
- Ask: "Is this segment mostly the right event?"
- If yes, mark CORRECT even with transitional handoff.
- If no, mark INCORRECT and explain what dominates incorrectly.

Do not over-index on transcript timestamps; they may be noisy. Judge by semantic flow.

Return strict JSON only:
{{"is_correct": <true|false>, "reason": "..."}}
"""


MODEL_GROUP = "segment-eval"
PRIMARY_DEPLOYMENT = "gemini-primary"
FALLBACK_DEPLOYMENT = "openai-fallback"
MAX_PARALLEL = 6
REQUESTS_PER_MINUTE = 22  # stay under Gemini's 25/min quota
litellm.drop_params = True


class RateLimiter:
    """Sliding-window rate limiter: at most `max_per_minute` acquires in any 60s window."""

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self.timestamps: list[float] = []
        self.lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self.lock:
                now = asyncio.get_event_loop().time()
                self.timestamps = [t for t in self.timestamps if now - t < 60]
                if len(self.timestamps) < self.max_per_minute:
                    self.timestamps.append(now)
                    return
                wait = 60 - (now - self.timestamps[0]) + 0.1
            await asyncio.sleep(wait)


def build_router() -> Router:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY missing in Django settings")
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing in Django settings")
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY missing in Django settings")

    model_list = [
        {
            "model_name": MODEL_GROUP,
            "litellm_params": {
                "model": "gemini/gemini-3.1-pro-preview",
                "api_key": settings.GEMINI_API_KEY,
            },
            "model_info": {"id": PRIMARY_DEPLOYMENT},
        },
        {
            "model_name": FALLBACK_DEPLOYMENT,
            "litellm_params": {
                "model": "openai/gpt-5.2-pro",
                "api_key": settings.OPENAI_API_KEY,
            },
            "model_info": {"id": FALLBACK_DEPLOYMENT},
        },
        {
            "model_name": MODEL_GROUP,
            "litellm_params": {
                "model": "anthropic/claude-opus-4-6",
                "api_key": settings.ANTHROPIC_API_KEY,
            },
            "model_info": {"id": "anthropic/claude-opus-4-6"},
        },
    ]

    # Let LiteLLM do provider fallback automatically.
    # If MODEL_GROUP fails, fallback to FALLBACK_DEPLOYMENT.
    return Router(
        model_list=model_list,
        fallbacks=[{MODEL_GROUP: [FALLBACK_DEPLOYMENT]}],
        set_verbose=False,
    )


def transcript_to_text(t_data: list[dict]) -> str:
    lines = []
    for line in t_data:
        ts = line.get("timestamp", "?")
        speaker = line.get("speaker", "Unknown")
        text = line.get("text", "")
        lines.append(f"[{ts}s] {speaker}: {text}")
    return "\n".join(lines)


def parse_eval_json(text: str) -> EvalResult:
    text = (text or "").strip()
    try:
        return EvalResult.model_validate_json(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return EvalResult.model_validate_json(text[start : end + 1])

    raise ValueError("Model response did not contain valid JSON.")


def build_prompt(
    curr_seg: Segment, prev_seg: Segment | None, next_seg: Segment | None
) -> str:
    prev_info = (
        f"Type: {prev_seg.segment_type} | Desc: {prev_seg.description}"
        if prev_seg
        else "(None, this is the first segment)"
    )
    next_info = (
        f"Type: {next_seg.segment_type} | Desc: {next_seg.description}"
        if next_seg
        else "(None, this is the last segment)"
    )

    t_data = curr_seg.transcript
    if isinstance(t_data, str):
        try:
            t_data = json.loads(t_data)
        except json.JSONDecodeError:
            t_data = []

    if not t_data:
        return ""

    return EVAL_PROMPT.format(
        prev_info=prev_info,
        curr_type=curr_seg.segment_type,
        curr_desc=curr_seg.description,
        curr_summary=curr_seg.summary,
        curr_start=curr_seg.start_time,
        curr_end=curr_seg.end_time,
        next_info=next_info,
        transcript=transcript_to_text(t_data),
    )


async def evaluate_one(
    router: Router,
    sem: asyncio.Semaphore,
    rate_limiter: RateLimiter,
    segments: list[Segment],
    i: int,
    progress: dict | None = None,
):
    curr = segments[i]
    prev_seg = segments[i - 1] if i > 0 else None
    next_seg = segments[i + 1] if i < len(segments) - 1 else None

    prompt = build_prompt(curr, prev_seg, next_seg)
    if not prompt:
        return (
            curr,
            EvalResult(
                is_correct=False,
                reason="No transcript available to verify this segment.",
            ),
            "none",
        )

    async with sem:
        await rate_limiter.acquire()
        try:
            resp = await router.acompletion(
                model=MODEL_GROUP,
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp["choices"][0]["message"]["content"]
            result = parse_eval_json(content)
            model_used = resp.get("model", "unknown")
            ret = curr, result, model_used
        except Exception as e:
            ret = (
                curr,
                EvalResult(is_correct=False, reason=f"Eval error: {e}"),
                "error",
            )

        if progress is not None:
            progress["done"] += 1
            status = "✅" if ret[1].is_correct else "❌"
            print(
                f"  [{progress['done']}/{progress['total']}] "
                f"Segment {curr.id} ({curr.start_time}s-{curr.end_time}s) {status}",
                flush=True,
            )
        return ret


async def main() -> None:
    v = Video.objects.latest("created_at")
    if not v:
        print("No videos found.")
        return

    segments = list(Segment.objects.filter(video=v).order_by("start_time"))
    if not segments:
        print("No segments found.")
        return

    router = build_router()

    print(
        f"Evaluating {len(segments)} segments in parallel "
        f"(max={MAX_PARALLEL}, {REQUESTS_PER_MINUTE} req/min) with LiteLLM Router"
    )

    sem = asyncio.Semaphore(MAX_PARALLEL)
    rate_limiter = RateLimiter(REQUESTS_PER_MINUTE)
    progress = {"done": 0, "total": len(segments)}
    tasks = [
        evaluate_one(router, sem, rate_limiter, segments, i, progress)
        for i in range(len(segments))
    ]
    rows = await asyncio.gather(*tasks)
    rows.sort(key=lambda r: r[0].start_time)

    correct_count = 0
    print("\n" + "=" * 56)
    print("STRICT TRIPLET EVALUATION")
    print("=" * 56 + "\n")

    usage = {}
    for curr, result, model_used in rows:
        usage[model_used] = usage.get(model_used, 0) + 1
        if result.is_correct:
            correct_count += 1

        status = "✅ CORRECT" if result.is_correct else "❌ INCORRECT"
        print(
            f"Segment ID {curr.id} [{curr.start_time}s - {curr.end_time}s]: {status} ({model_used})"
        )
        print(f"Reason: {result.reason}\n")

    print("=" * 56)
    print(
        f"FINAL STRICT SCORE: {correct_count}/{len(segments)} "
        f"({(correct_count / len(segments)) * 100:.1f}%)"
    )
    print(f"Model usage: {usage}")


if __name__ == "__main__":
    asyncio.run(main())
