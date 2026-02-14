import asyncio
import os

from google import genai
from google.genai import types
from pydantic import BaseModel
from temporalio import activity

from catalogue.models import Comedian, Segment, SegmentComedian, Video


# --- Pydantic schemas ---


class SegmentResult(BaseModel):
    start_time: int
    end_time: int
    segment_type: str
    description: str
    comedians: list[str]


class SegmentResultList(BaseModel):
    segments: list[SegmentResult]


class TranscriptLine(BaseModel):
    timestamp: int
    speaker: str
    text: str


class TranscriptResult(BaseModel):
    summary: str
    transcript: list[TranscriptLine]


class SegmentVideoInput(BaseModel):
    video_id: int
    url: str


class AnalyzeSegmentInput(BaseModel):
    segment_id: int
    url: str
    start_time: int
    end_time: int


# --- Activity helpers ---


async def _stream_json_with_heartbeat(
    client,
    *,
    model: str,
    contents,
    config: dict,
    label: str,
    beat_interval_seconds: int = 20,
) -> tuple[str, object | None]:
    text_chunks: list[str] = []
    stream_chunks = 0
    usage_metadata = None
    stop = asyncio.Event()
    beat_count = 0
    stage = "initializing"

    def _details(status: str) -> dict:
        return {
            "phase": label,
            "status": status,
            "stage": stage,
            "beats": beat_count,
            "stream_chunks": stream_chunks,
            "text_chunks": len(text_chunks),
            "chars": sum(len(c) for c in text_chunks),
        }

    async def _beater() -> None:
        nonlocal beat_count
        while not stop.is_set():
            beat_count += 1
            activity.heartbeat(_details("running"))
            try:
                await asyncio.wait_for(stop.wait(), timeout=beat_interval_seconds)
            except asyncio.TimeoutError:
                pass

    beater = asyncio.create_task(_beater())
    try:
        stage = "requesting_stream"
        stream = await client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        )

        stage = "reading_stream"
        async for chunk in stream:
            stream_chunks += 1
            text = getattr(chunk, "text", None)
            if text:
                text_chunks.append(text)
            usage_metadata = getattr(chunk, "usage_metadata", usage_metadata)
            activity.heartbeat(_details("streaming"))

        stage = "completed"
        activity.heartbeat(_details("done"))
    finally:
        stop.set()
        await beater

    return "".join(text_chunks), usage_metadata


# --- Activities ---


@activity.defn
async def segment_video(input_data: dict) -> list[dict]:
    """Analyze a video with Gemini and save time-based segments."""
    payload = SegmentVideoInput.model_validate(input_data)

    async with genai.Client(api_key=os.environ["GEMINI_API_KEY"]).aio as client:
        response_text, usage = await _stream_json_with_heartbeat(
            client,
            model="gemini-3-flash-preview",
            contents=types.Content(
                parts=[
                    types.Part(file_data=types.FileData(file_uri=payload.url)),
                    types.Part(text=SEGMENTATION_PROMPT),
                ]
            ),
            config={
                "response_mime_type": "application/json",
                "response_json_schema": SegmentResultList.model_json_schema(),
            },
            label="segment_video",
        )

    assert response_text, "Gemini returned no text"
    segments_data = SegmentResultList.model_validate_json(response_text).segments

    video = Video.objects.get(pk=payload.video_id)

    # Clear previous results
    Segment.objects.filter(video=video).delete()

    segment_infos = []
    for s in segments_data:
        segment = Segment.objects.create(
            video=video,
            start_time=s.start_time,
            end_time=s.end_time,
            segment_type=s.segment_type,
            description=s.description,
        )
        segment_infos.append(
            {
                "segment_id": segment.pk,
                "start_time": s.start_time,
                "end_time": s.end_time,
            }
        )

        for name in s.comedians:
            comedian, _ = Comedian.objects.get_or_create(name=name)
            SegmentComedian.objects.create(segment=segment, comedian=comedian)

    if usage is not None:
        activity.logger.info(
            f"Saved {len(segments_data)} segments for video {payload.video_id} "
            f"(input tokens: {usage.prompt_token_count}, "
            f"output tokens: {usage.candidates_token_count})"
        )
    else:
        activity.logger.info(
            f"Saved {len(segments_data)} segments for video {payload.video_id}"
        )

    return segment_infos


@activity.defn
async def analyze_segment(input_data: dict) -> int:
    """Analyze a single segment clip with context and store summary + transcript."""
    payload = AnalyzeSegmentInput.model_validate(input_data)

    segment = (
        Segment.objects.filter(pk=payload.segment_id)
        .prefetch_related("segment_comedians__comedian")
        .first()
    )
    if not segment:
        activity.logger.warning(
            f"Segment {payload.segment_id} no longer exists; skipping transcript write"
        )
        return payload.segment_id

    known_speakers = [
        link.comedian.name for link in segment.segment_comedians.all()
    ]
    known_speakers_text = ", ".join(known_speakers) if known_speakers else "(none)"

    main_comedian = "(unknown)"
    if segment.segment_type == "performance" and known_speakers:
        main_comedian = known_speakers[0]

    context_prompt = TRANSCRIPT_CONTEXT_TEMPLATE.format(
        segment_type=segment.segment_type,
        description=segment.description,
        known_speakers=known_speakers_text,
        main_comedian=main_comedian,
    )

    async with genai.Client(api_key=os.environ["GEMINI_API_KEY"]).aio as client:
        response_text, usage = await _stream_json_with_heartbeat(
            client,
            model="gemini-3-flash-preview",
            contents=types.Content(
                parts=[
                    types.Part(
                        file_data=types.FileData(file_uri=payload.url),
                        video_metadata=types.VideoMetadata(
                            start_offset=f"{payload.start_time}s",
                            end_offset=f"{payload.end_time}s",
                        ),
                    ),
                    types.Part(text=context_prompt),
                    types.Part(text=TRANSCRIPT_PROMPT),
                ]
            ),
            config={
                "response_mime_type": "application/json",
                "response_json_schema": TranscriptResult.model_json_schema(),
            },
            label=f"analyze_segment:{payload.segment_id}",
        )

    assert response_text, "Gemini returned no text"
    result = TranscriptResult.model_validate_json(response_text)

    segment.summary = result.summary
    segment.transcript = [line.model_dump() for line in result.transcript]
    segment.save()

    if usage is not None:
        activity.logger.info(
            f"Analyzed segment {payload.segment_id} "
            f"(input tokens: {usage.prompt_token_count}, "
            f"output tokens: {usage.candidates_token_count})"
        )
    else:
        activity.logger.info(f"Analyzed segment {payload.segment_id}")

    return payload.segment_id


# --- Prompts ---


SEGMENTATION_PROMPT = """\
You are analyzing a video of a live comedy show, podcast, or performance.

Your job is to divide the entire video into consecutive, non-overlapping time \
segments based on what is happening. Cover the full duration from start to end \
with no gaps.

For each segment, identify:
- **start_time** and **end_time** in seconds
- **segment_type**: a short category label (e.g. "intro", "performance", \
"interview", "ad_read", "audience_interaction", "musical_performance", \
"host_monologue", "panel_discussion", "break", "outro", etc.). Use whatever \
labels fit — don't force things into categories that don't apply.
- **description**: a brief description of what's happening, who's involved, \
and any notable moments.
- **comedians**: list of names of comedians/performers visible or participating \
in this segment. Use their full name as it would commonly appear. Empty list if \
no comedian is identifiable.

Guidelines:
- Watch and listen carefully to both audio and video.
- Identify transitions between different performers, speakers, or activities.
- Note when someone new takes the stage or the mic.
- Distinguish between prepared material (bits, sets) and spontaneous \
interaction (banter, crowd work, interviews).
- If there are advertisements, sponsorship reads, or promotional segments, \
label them clearly.
- Name people when you can identify them.
- Keep descriptions concise but informative.
- Don't split a single continuous activity into multiple segments just because \
it's long. If the same thing is still happening with the same people, it's one \
segment. Only start a new segment when something meaningfully changes — a \
different person, a different activity, or a distinct new subject.
"""

TRANSCRIPT_CONTEXT_TEMPLATE = """\
Segment context (from a prior analysis pass):
- segment_type: {segment_type}
- description: {description}
- known speakers in this segment: {known_speakers}
- main comedian for this segment (if performance): {main_comedian}

Use this context as identity hints. Prefer these known speaker names where they match.
If this is a performance segment, strongly prefer labeling the performer's lines with the main comedian name above.
Do not invent names. If you are unsure who is speaking, use "Unknown".
"""


TRANSCRIPT_PROMPT = """\
Transcribe this video clip completely and accurately.

Produce:
- **summary**: a tweet-length summary (up to ~280 characters) of what was \
actually said or performed. Focus on the content itself — the jokes, topics, \
arguments, stories — not who was involved or what type of segment it is.
- **transcript**: a list of every line of dialogue, with:
  - **timestamp**: seconds from the start of this clip (starting at 0)
  - **speaker**: the name of the person speaking, "Audience" for crowd \
reactions (laughter, cheering, heckling, applause), or "Unknown" if identity \
cannot be determined confidently
  - **text**: exactly what was said. Do not paraphrase or skip words. \
Transcribe every word faithfully, including filler words, false starts, and \
profanity.

Be thorough. Do not summarize or abbreviate the dialogue. Every sentence \
that is spoken must appear in the transcript.
"""
