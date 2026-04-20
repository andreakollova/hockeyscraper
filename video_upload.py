"""
Shared utility: download a YouTube video via yt-dlp and upload to gofile.io (fallback: catbox.moe).
Runs in GitHub Actions — plenty of RAM, no IP blocks.
Returns the public download URL or None on failure.
"""

import os
import shutil
import tempfile
from pathlib import Path

import requests


def _upload_gofile(file_path: str) -> str | None:
    size_mb = os.path.getsize(file_path) / 1024 / 1024
    print(f"    Uploading to gofile.io ({size_mb:.0f} MB)…")
    try:
        # Get best server
        srv = requests.get("https://api.gofile.io/servers", timeout=15).json()
        server = srv["data"]["servers"][0]["name"]

        with open(file_path, "rb") as f:
            resp = requests.post(
                f"https://{server}.gofile.io/contents/uploadfile",
                files={"file": (os.path.basename(file_path), f, "video/mp4")},
                timeout=600,
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok":
            url = data["data"]["downloadPage"]
            print(f"    gofile.io OK: {url}")
            return url
        print(f"    gofile.io unexpected: {data}")
    except Exception as e:
        print(f"    gofile.io failed: {e}")
    return None


def _upload_catbox(file_path: str) -> str | None:
    size_mb = os.path.getsize(file_path) / 1024 / 1024
    print(f"    Uploading to catbox.moe ({size_mb:.0f} MB)…")
    try:
        fname = Path(file_path).name
        with open(file_path, "rb") as f:
            resp = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload"},
                files={"fileToUpload": (fname, f)},
                timeout=600,
            )
        resp.raise_for_status()
        url = resp.text.strip()
        if url.startswith("https://"):
            print(f"    catbox OK: {url}")
            return url
        print(f"    catbox unexpected: {url[:120]}")
    except Exception as e:
        print(f"    catbox failed: {e}")
    return None


def download_and_upload(yt_url: str) -> str | None:
    """Download video from YouTube, upload to gofile.io (fallback: catbox.moe).
    Returns public download URL or None if everything fails.
    """
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        print("    [warn] yt-dlp not installed")
        return None

    tmp_dir = tempfile.mkdtemp(prefix="hockey_video_")
    try:
        ydl_opts = {
            # Highest quality: prefer 1080p+, merge best video+audio
            "format": "bestvideo[height>=1080]+bestaudio/bestvideo+bestaudio/best",
            "format_sort": ["res:1080", "vcodec:h264", "acodec:aac"],
            "merge_output_format": "mp4",
            "outtmpl": os.path.join(tmp_dir, "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": False,
            "no_warnings": False,
            # Android player client bypasses YouTube throttling on server IPs
            "extractor_args": {"youtube": {"player_client": ["android"]}},
        }
        print(f"    Downloading: {yt_url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(yt_url, download=True)

        files = sorted(
            [f for f in Path(tmp_dir).iterdir() if f.is_file()],
            key=lambda f: f.stat().st_size,
            reverse=True,
        )
        if not files:
            print("    [warn] no output file")
            return None

        file_path = str(files[0])
        return _upload_gofile(file_path) or _upload_catbox(file_path)

    except Exception as e:
        print(f"    [warn] download_and_upload failed: {e}")
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
