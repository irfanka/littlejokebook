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
- Name people when you can identify them. Keep descriptions concise but informative.
- Ensure timestamps are in absolute video time (between {start_time_str} and {end_time_str}).
"""

def _format_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
