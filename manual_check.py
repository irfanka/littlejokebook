import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'little_jokebook.settings')
django.setup()

from catalogue.models import Segment, Video

v = Video.objects.first()
segments = Segment.objects.filter(video=v).order_by('start_time')

for s in segments:
    print(f"\n--- Segment [{s.start_time}s - {s.end_time}s] ID {s.id} ---")
    print(f"Desc: {s.description}")
    
    t_data = s.transcript
    if isinstance(t_data, str):
        try:
            t_data = json.loads(t_data)
        except:
            t_data = []
            
    if not t_data:
        print("Transcript: (None)")
        continue
        
    print("Transcript (First 5):")
    for line in t_data[:5]:
        print(f"  {line.get('timestamp')}s | {line.get('speaker')}: {line.get('text')}")
        
    print("Transcript (Last 5):")
    for line in t_data[-5:]:
        print(f"  {line.get('timestamp')}s | {line.get('speaker')}: {line.get('text')}")
