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
  - **segment_type**: A short category label (e.g. "performance", "interview", "ad_read", etc.)
  - **description**: A brief description of the new segment.
  - **comedians**: List of comedians' names involved.

Guidelines:
- Watch and listen to the entire chunk.
- ONLY list NEW segment starts. Do not list the carried-over segment from the context unless a completely new segment of the same type starts.
- Ensure timestamps are in absolute video time (between {start_time_str} and {end_time_str}).
"""
