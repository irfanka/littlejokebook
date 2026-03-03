import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'little_jokebook.settings')
django.setup()

from catalogue.models import Segment, Video

videos = Video.objects.all()
if not videos:
    print("No videos found.")
else:
    for v in videos:
        print(f"=== Video: {v.url} (ID: {v.id}) ===")
        segments = Segment.objects.filter(video=v).order_by('start_time')
        if not segments:
            print("  No segments found for this video.")
        for s in segments:
            print(f"[{s.start_time} - {s.end_time}] Type: {s.segment_type}")
            print(f"  Description: {s.description}")
            print(f"  Summary: {s.summary}")
            
            transcript = s.transcript
            if isinstance(transcript, str):
                try:
                    transcript = json.loads(transcript)
                except:
                    pass
            
            if transcript and isinstance(transcript, list):
                print(f"  Transcript: ({len(transcript)} lines)")
                for idx, line in enumerate(transcript):
                    if idx < 3 or idx >= len(transcript) - 3:
                        print(f"    {line.get('timestamp', '?')} | {line.get('speaker', '?')}: {line.get('text', '?')}")
                    elif idx == 3:
                        print("    ... (skipped)")
            else:
                print(f"  Transcript: None or empty")
            print("-" * 40)
