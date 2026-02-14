import hashlib
from datetime import datetime, timezone

import yt_dlp
from temporalio import activity


@activity.defn
async def download_video(url: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
    filename = f"{now}-{url_hash}"

    opts = {
        "outtmpl": f"./videos/{filename}.%(ext)s",
        "format": "best",
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)
