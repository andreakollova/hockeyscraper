"""
Shared utility: download a YouTube video via yt-dlp and upload to catbox.moe.
Runs in GitHub Actions — plenty of RAM, no IP blocks.
Returns the public download URL or None on failure.
"""

import os
import shutil
import tempfile
from pathlib import Path

import requests


def _upload_catbox(file_path: str) -> str | None:
    print(f"    Uploading to catbox.moe ({os.path.getsize(file_path) / 1024 / 1024:.0f} MB)…")
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload"},
                files={"fileToUpload": (os.path.basename(file_path), f, "video/mp4")},
                timeout=600,
            )
        resp.raise_for_status()
        url = resp.text.strip()
        if url.startswith("https://"):
            print(f"    catbox OK: {url}")
            return url
        print(f"    catbox unexpected response: {url[:120]}")
    except Exception as e:
        print(f"    catbox upload failed: {e}")
    return None


def download_and_upload(yt_url: str) -> str | None:
    """Download video from YouTube and upload to catbox.moe.
    Returns public download URL or None if anything fails.
    """
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        print("    [warn] yt-dlp not installed — skipping download")
        return None

    tmp_dir = tempfile.mkdtemp(prefix="hockey_video_")
    try:
        ydl_opts = {
            # Prefer pre-merged mp4 (no ffmpeg needed); max 720p to keep file size sane
            "format": "22/18/best[height<=720][ext=mp4]/best[ext=mp4]/best",
            "outtmpl": os.path.join(tmp_dir, "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        print(f"    Downloading via yt-dlp: {yt_url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(yt_url, download=True)

        # Find the downloaded file
        files = sorted(
            [f for f in Path(tmp_dir).iterdir() if f.is_file()],
            key=lambda f: f.stat().st_size,
            reverse=True,
        )
        if not files:
            print("    [warn] yt-dlp produced no output file")
            return None

        return _upload_catbox(str(files[0]))

    except Exception as e:
        print(f"    [warn] download_and_upload failed: {e}")
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
