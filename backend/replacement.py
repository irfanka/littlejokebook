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
                model="gemini-3.1-pro-preview",
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
