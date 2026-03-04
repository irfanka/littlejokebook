import asyncio
import os
import re
import yt_dlp

from google import genai
from google.genai import types
from pydantic import BaseModel
from temporalio import activity

from catalogue.models import Comedian, Segment, SegmentComedian, Video


# --- Pydantic schemas ---


class SegmentBoundary(BaseModel):
    timestamp: str
    segment_type: str
    description: str
    comedians: list[str]


class ChunkResultList(BaseModel):
    timeline_log: str
    boundaries: list[SegmentBoundary]


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

def _format_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def _parse_time(time_str: str) -> int:
    try:
        if ":" in time_str:
            parts = time_str.split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(float(parts[1]))
        return int(float(time_str))
    except (ValueError, TypeError):
        return 0

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
    """Analyze a video with Gemini using sliding windows and save time-based segments."""
    payload = SegmentVideoInput.model_validate(input_data)
    
    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
        info = ydl.extract_info(payload.url, download=False)
        duration = int(info.get('duration', 0))
        
    if duration == 0:
        raise ValueError("Could not determine video duration.")
        
    chunk_dur = 600
    overlap = 60
    stride = chunk_dur - overlap
    
    active_segment = None
    all_segments = []
    usage_total_prompt = 0
    usage_total_candidates = 0
    
    video = Video.objects.get(pk=payload.video_id)
    Segment.objects.filter(video=video).delete()

    async with genai.Client(api_key=os.environ["GEMINI_API_KEY"]).aio as client:
        for t_start in range(0, duration, stride):
            t_end = min(t_start + chunk_dur, duration)
            is_last_chunk = (t_end == duration)
            
            start_str = _format_time(t_start)
            end_str = _format_time(t_end)
            
            if active_segment:
                ctx = (
                    f"CONTEXT FROM PREVIOUS CHUNK:\n"
                    f"The segment currently ongoing at {start_str} is of type "
                    f"'{active_segment['type']}'.\n"
                    f"Description: {active_segment['description']}\n"
                    f"Comedians: {', '.join(active_segment['comedians']) if active_segment['comedians'] else '(none)'}\n"
                )
            else:
                ctx = "This is the very first chunk of the video. The show is about to start."
                
            prompt_text = CHUNK_PROMPT.format(
                chunk_duration=t_end - t_start,
                start_time_str=start_str,
                end_time_str=end_str,
                context_section=ctx
            )
            
            response_text, usage = await _stream_json_with_heartbeat(
                client,
                model="gemini-3-flash-preview",
                contents=types.Content(
                    parts=[
                        types.Part(
                            file_data=types.FileData(file_uri=payload.url),
                            video_metadata=types.VideoMetadata(
                                start_offset=f"{t_start}s",
                                end_offset=f"{t_end}s",
                            ),
                        ),
                        types.Part(text=prompt_text),
                    ]
                ),
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": ChunkResultList.model_json_schema(),
                },
                label=f"segment_video_chunk_{t_start}_{t_end}",
            )
            
            assert response_text, f"Gemini returned no text for chunk {t_start}-{t_end}"
            chunk_data = ChunkResultList.model_validate_json(response_text)
            
            if usage is not None:
                usage_total_prompt += getattr(usage, "prompt_token_count", 0)
                usage_total_candidates += getattr(usage, "candidates_token_count", 0)
            
            for b in chunk_data.boundaries:
                b_sec = _parse_time(b.timestamp)
                
                if b_sec < t_start:
                    continue
                if not is_last_chunk and b_sec >= t_start + stride:
                    continue
                    
                new_seg = {
                    "start_time": b_sec,
                    "type": b.segment_type,
                    "description": b.description,
                    "comedians": b.comedians
                }
                
                # Check for duplicate starts (sometimes LLM just outputs the context segment start again)
                if all_segments and all_segments[-1]["start_time"] == b_sec:
                    all_segments[-1] = new_seg
                else:
                    all_segments.append(new_seg)
                active_segment = new_seg
                
    if not all_segments:
        activity.logger.warning(f"No segments found for video {payload.video_id}")
        return []
        
    # ensure the first segment starts at 0
    if all_segments[0]["start_time"] > 0:
        all_segments[0]["start_time"] = 0
        
    segment_infos = []
    for i, seg_data in enumerate(all_segments):
        start_time = seg_data["start_time"]
        end_time = all_segments[i+1]["start_time"] if i + 1 < len(all_segments) else duration
        
        if start_time >= end_time:
            continue
            
        segment = Segment.objects.create(
            video=video,
            start_time=start_time,
            end_time=end_time,
            segment_type=seg_data["type"],
            description=seg_data["description"],
        )
        
        for name in seg_data["comedians"]:
            comedian, _ = Comedian.objects.get_or_create(name=name)
            SegmentComedian.objects.create(segment=segment, comedian=comedian)
            
        segment_infos.append(
            {
                "segment_id": segment.pk,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        
    activity.logger.info(
        f"Saved {len(segment_infos)} segments for video {payload.video_id} "
        f"using chunking (total input tokens: {usage_total_prompt}, "
        f"output tokens: {usage_total_candidates})"
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
                "max_output_tokens": 65536,
            },
            label=f"analyze_segment:{payload.segment_id}",
        )

    assert response_text, "Gemini returned no text"
    response_text = response_text.replace("\x00", "")
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


CHUNK_PROMPT = """\
You are analyzing a {chunk_duration}-second chunk of a comedy show video.
This chunk covers the time range from {start_time_str} to {end_time_str} in the full video.

{context_section}

Your job is to identify when segment transitions occur WITHIN this specific chunk.
First, write a chronological log of key visual and audio transitions in `timeline_log`. Include absolute timestamps (e.g. "15:30").

Then, list the segment boundaries (transitions) in the `boundaries` field:
- If NO segment transition occurs in this chunk (i.e. the previous segment continues uninterrupted), return an empty list.
- If a new segment begins, provide:
  - **timestamp**: The exact absolute time it starts (e.g., "MM:SS" or "HH:MM:SS"). MUST be between {start_time_str} and {end_time_str}.
  - **segment_type**: A short category label (e.g. "intro", "performance", "interview", "ad_read", "audience_interaction", "musical_performance", "host_monologue", "panel_discussion", "break", "outro", etc.)
  - **description**: A brief description of what is happening in the new segment.
  - **comedians**: List of names of comedians/performers visible or participating. Empty list if no comedian is identifiable.

Guidelines:
- Watch and listen carefully to the entire chunk.
- ONLY list NEW segment starts. Do not list the carried-over segment from the context unless a completely new segment of the same type starts.
- Identify transitions between different performers, speakers, or activities.
- Distinguish between prepared material (bits, sets) and spontaneous interaction (banter, crowd work, interviews).
- **Within a single performer's set or standup special**, also identify major \
THEMATIC transitions — when the comedian moves from one high-level topic or \
theme to a distinctly different one (e.g., from politics to religion, from \
aging to death, from relationships to technology). Each theme may contain \
multiple related jokes or bits — group those together as ONE segment. \
Do NOT split on individual jokes, callbacks, or brief asides — only on clear, \
sustained topic shifts where the comedian moves to a new subject and stays there.
- Name people when you can identify them. Keep descriptions concise but informative.
- Ensure timestamps are in absolute video time (between {start_time_str} and {end_time_str}).
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


# --- Boundary refinement ---


@activity.defn
async def fetch_segment_infos(video_id: int) -> list[dict]:
    """Fetch existing segment IDs and times for a video (used by standalone refinement)."""
    segments = Segment.objects.filter(video_id=video_id).order_by("start_time")
    return [
        {
            "segment_id": s.pk,
            "start_time": s.start_time,
            "end_time": s.end_time,
        }
        for s in segments
    ]


class RefineTripletInput(BaseModel):
    prev_segment_id: int
    curr_segment_id: int
    next_segment_id: int


class RefinedSegmentInfo(BaseModel):
    segment_type: str
    description: str
    summary: str


class RefinementResult(BaseModel):
    reasoning: str
    boundaries_changed: bool
    corrected_start_time: int
    corrected_end_time: int
    prev_segment: RefinedSegmentInfo
    curr_segment: RefinedSegmentInfo
    next_segment: RefinedSegmentInfo


def _format_transcript_absolute(transcript: list[dict], start_time: int) -> str:
    """Format transcript lines with absolute timestamps for the refinement prompt."""
    lines = []
    for line in transcript:
        abs_ts = line.get("timestamp", 0) + start_time
        speaker = line.get("speaker", "Unknown")
        text = line.get("text", "")
        lines.append(f"[{abs_ts}s] {speaker}: {text}")
    return "\n".join(lines) if lines else "(no transcript)"


def _reslice_transcripts(
    prev_seg,
    curr_seg,
    next_seg,
    new_curr_start: int,
    new_curr_end: int,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Re-partition transcript lines across three segments based on new boundaries."""
    all_lines = []
    for seg in [prev_seg, curr_seg, next_seg]:
        for line in (seg.transcript or []):
            all_lines.append({
                "abs_ts": line.get("timestamp", 0) + seg.start_time,
                "speaker": line.get("speaker", "Unknown"),
                "text": line.get("text", ""),
            })
    all_lines.sort(key=lambda x: x["abs_ts"])

    prev_lines = [l for l in all_lines if l["abs_ts"] < new_curr_start]
    curr_lines = [l for l in all_lines if new_curr_start <= l["abs_ts"] < new_curr_end]
    next_lines = [l for l in all_lines if l["abs_ts"] >= new_curr_end]

    def _to_relative(lines, seg_start):
        return [
            {
                "timestamp": max(0, l["abs_ts"] - seg_start),
                "speaker": l["speaker"],
                "text": l["text"],
            }
            for l in lines
        ]

    return (
        _to_relative(prev_lines, prev_seg.start_time),
        _to_relative(curr_lines, new_curr_start),
        _to_relative(next_lines, new_curr_end),
    )


REFINEMENT_PROMPT = """\
You are refining the boundary placement of a video segmentation pipeline.
Below are three consecutive segments from a comedy show with their transcripts.
All timestamps are ABSOLUTE (seconds from video start).

PREVIOUS SEGMENT:
- Type: {prev_type}
- Description: {prev_desc}
- Summary: {prev_summary}
- Time: {prev_start}s to {prev_end}s
- Transcript:
{prev_transcript}

CURRENT SEGMENT (the one being checked):
- Type: {curr_type}
- Description: {curr_desc}
- Summary: {curr_summary}
- Time: {curr_start}s to {curr_end}s
- Transcript:
{curr_transcript}

NEXT SEGMENT:
- Type: {next_type}
- Description: {next_desc}
- Summary: {next_summary}
- Time: {next_start}s to {next_end}s
- Transcript:
{next_transcript}

TASK:
Examine the two boundaries of the CURRENT (middle) segment:

1. START boundary ({curr_start}s): Does content at the beginning of CURRENT \
actually belong to PREVIOUS? Look for: wrong speaker continuing from \
previous segment, same topic/conversation continuing, the actual transition \
(new performer introduced, topic change) happening later.

2. END boundary ({curr_end}s): Does content at the end of CURRENT actually \
belong to NEXT? Look for: next segment's performer/topic already started, \
host introducing next act, clear transition happening before the boundary.

TOLERANCE:
- Small overlaps (<20s) of transitional chatter (host intro/outro, brief \
applause, one-liner handoff) are ACCEPTABLE and should NOT be corrected.
- Only correct MATERIAL errors: substantial content (>20s or >20% of a \
segment) is placed in the wrong segment.

IMPORTANT CONSTRAINTS ON CORRECTED TIMESTAMPS:
- corrected_start_time MUST be >= {prev_start} and < corrected_end_time
- corrected_end_time MUST be > corrected_start_time and <= {next_end}

If boundaries need adjustment:
- Set boundaries_changed to true
- Set corrected_start_time / corrected_end_time to the corrected absolute \
timestamps (in seconds)
- Provide updated segment_type, description, and summary for ALL THREE \
segments reflecting the corrected content

If boundaries are correct:
- Set boundaries_changed to false
- Set corrected_start_time to {curr_start} and corrected_end_time to \
{curr_end}
- Return the existing segment_type, description, and summary for all three \
segments unchanged

Return strict JSON matching the schema.
"""


@activity.defn
async def refine_triplet(input_data: dict) -> dict:
    """Refine boundaries of the middle segment in a triplet using transcript analysis."""
    payload = RefineTripletInput.model_validate(input_data)

    prev_seg = Segment.objects.get(pk=payload.prev_segment_id)
    curr_seg = Segment.objects.get(pk=payload.curr_segment_id)
    next_seg = Segment.objects.get(pk=payload.next_segment_id)

    # Capture original values before any changes
    orig_start = curr_seg.start_time
    orig_end = curr_seg.end_time

    prompt = REFINEMENT_PROMPT.format(
        prev_type=prev_seg.segment_type,
        prev_desc=prev_seg.description,
        prev_summary=prev_seg.summary,
        prev_start=prev_seg.start_time,
        prev_end=prev_seg.end_time,
        prev_transcript=_format_transcript_absolute(
            prev_seg.transcript or [], prev_seg.start_time
        ),
        curr_type=curr_seg.segment_type,
        curr_desc=curr_seg.description,
        curr_summary=curr_seg.summary,
        curr_start=curr_seg.start_time,
        curr_end=curr_seg.end_time,
        curr_transcript=_format_transcript_absolute(
            curr_seg.transcript or [], curr_seg.start_time
        ),
        next_type=next_seg.segment_type,
        next_desc=next_seg.description,
        next_summary=next_seg.summary,
        next_start=next_seg.start_time,
        next_end=next_seg.end_time,
        next_transcript=_format_transcript_absolute(
            next_seg.transcript or [], next_seg.start_time
        ),
    )

    async with genai.Client(api_key=os.environ["GEMINI_API_KEY"]).aio as client:
        response_text, usage = await _stream_json_with_heartbeat(
            client,
            model="gemini-3-flash-preview",
            contents=types.Content(
                parts=[types.Part(text=prompt)]
            ),
            config={
                "response_mime_type": "application/json",
                "response_json_schema": RefinementResult.model_json_schema(),
            },
            label=f"refine_triplet:{curr_seg.pk}",
        )

    assert response_text, "Gemini returned no text for refinement"
    response_text = response_text.replace("\x00", "")
    result = RefinementResult.model_validate_json(response_text)

    if usage is not None:
        activity.logger.info(
            f"Refine triplet {prev_seg.pk}/{curr_seg.pk}/{next_seg.pk} "
            f"(input tokens: {getattr(usage, 'prompt_token_count', '?')}, "
            f"output tokens: {getattr(usage, 'candidates_token_count', '?')})"
        )

    # Determine if boundaries actually changed
    new_start = result.corrected_start_time
    new_end = result.corrected_end_time
    start_changed = new_start != orig_start
    end_changed = new_end != orig_end

    if not start_changed and not end_changed:
        activity.logger.info(
            f"Triplet {prev_seg.pk}/{curr_seg.pk}/{next_seg.pk}: "
            f"boundaries correct. {result.reasoning}"
        )
        return {
            "curr_segment_id": curr_seg.pk,
            "changed": False,
            "reasoning": result.reasoning,
        }

    # Sanity-check the corrected timestamps
    if new_start < prev_seg.start_time or new_start >= new_end:
        activity.logger.warning(
            f"Invalid corrected_start_time {new_start} for triplet "
            f"{prev_seg.pk}/{curr_seg.pk}/{next_seg.pk}. Skipping."
        )
        return {
            "curr_segment_id": curr_seg.pk,
            "changed": False,
            "reasoning": f"Invalid start time: {result.reasoning}",
        }

    if new_end > next_seg.end_time or new_end <= new_start:
        activity.logger.warning(
            f"Invalid corrected_end_time {new_end} for triplet "
            f"{prev_seg.pk}/{curr_seg.pk}/{next_seg.pk}. Skipping."
        )
        return {
            "curr_segment_id": curr_seg.pk,
            "changed": False,
            "reasoning": f"Invalid end time: {result.reasoning}",
        }

    # Re-slice transcripts
    prev_transcript, curr_transcript, next_transcript = _reslice_transcripts(
        prev_seg, curr_seg, next_seg, new_start, new_end
    )

    # Update previous segment
    prev_seg.end_time = new_start
    prev_seg.segment_type = result.prev_segment.segment_type
    prev_seg.description = result.prev_segment.description
    prev_seg.summary = result.prev_segment.summary
    prev_seg.transcript = prev_transcript
    prev_seg.save()

    # Update current segment
    curr_seg.start_time = new_start
    curr_seg.end_time = new_end
    curr_seg.segment_type = result.curr_segment.segment_type
    curr_seg.description = result.curr_segment.description
    curr_seg.summary = result.curr_segment.summary
    curr_seg.transcript = curr_transcript
    curr_seg.save()

    # Update next segment
    next_seg.start_time = new_end
    next_seg.segment_type = result.next_segment.segment_type
    next_seg.description = result.next_segment.description
    next_seg.summary = result.next_segment.summary
    next_seg.transcript = next_transcript
    next_seg.save()

    activity.logger.info(
        f"Triplet {prev_seg.pk}/{curr_seg.pk}/{next_seg.pk}: "
        f"boundaries adjusted. Start: {orig_start}→{new_start}, "
        f"End: {orig_end}→{new_end}. {result.reasoning}"
    )

    return {
        "curr_segment_id": curr_seg.pk,
        "changed": True,
        "old_start": orig_start,
        "new_start": new_start,
        "old_end": orig_end,
        "new_end": new_end,
        "reasoning": result.reasoning,
    }
