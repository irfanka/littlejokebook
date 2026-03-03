import yt_dlp
url = "https://www.youtube.com/watch?v=tjT1DbnWTb8"
with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
    info = ydl.extract_info(url, download=False)
    print("Duration:", info.get('duration'))
