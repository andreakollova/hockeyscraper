#!/usr/bin/env python3
"""
Daily video processor: picks the oldest unprocessed video from Supabase,
downloads it via yt-dlp, uploads to catbox.moe, stores the download_url.
The Discord bot then reads this URL and posts the video.
Run once per day via GitHub Actions.
"""

import os
import sys
from pathlib import Path

_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from supabase import create_client
from video_upload import download_and_upload


def main():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("[error] Missing SUPABASE_URL / SUPABASE_KEY")
        sys.exit(1)

    db = create_client(url, key)

    # Pick oldest video that has no download_url yet and hasn't been sent to Discord
    result = (
        db.table("videos")
        .select("id, title, youtube_url")
        .is_("download_url", "null")
        .neq("discord_sent", True)
        .order("scraped_at", desc=False)
        .limit(1)
        .execute()
    )

    if not result.data:
        print("No videos pending download — nothing to do.")
        return

    video = result.data[0]
    print(f"Processing: {video['title'][:70]}")
    print(f"  YouTube: {video['youtube_url']}")

    download_url = download_and_upload(video["youtube_url"])

    if download_url:
        db.table("videos").update({"download_url": download_url}).eq("id", video["id"]).execute()
        print(f"  Saved download_url: {download_url}")
    else:
        print("  [warn] Download/upload failed — download_url not saved")


if __name__ == "__main__":
    main()
