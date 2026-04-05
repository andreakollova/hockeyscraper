#!/usr/bin/env python3
"""
FIH video scraper → Supabase
Scrapes latest videos from fih.hockey/videos
Stores to Supabase `videos` table with category='fih'.
"""

import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

FIH_VIDEOS_URL = "https://www.fih.hockey/videos"
MAX_VIDEOS = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; HockeyNewsBot/1.0; +https://hockeytea.com)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("[error] Missing SUPABASE_URL / SUPABASE_KEY")
        sys.exit(1)
    return create_client(url, key)


def load_existing_video_ids(db: Client) -> set:
    res = db.table("videos").select("youtube_id").execute()
    return {row["youtube_id"] for row in res.data}


def fetch(url: str) -> "requests.Response | None":
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            print(f"  [warn] {url} attempt {attempt + 1}: {e}")
            time.sleep(3)
    return None


def scrape_fih_videos(html: str) -> list[dict]:
    """Extract YouTube video IDs from the FIH videos page.

    FIH renders videos as <article data-video-id="YT_ID" asset-title="...">
    rather than standard YouTube links or iframes.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen_ids: set[str] = set()

    for article in soup.find_all("article", attrs={"data-video-id": True}):
        video_id = article.get("data-video-id", "").strip()
        if not video_id or video_id in seen_ids:
            continue
        seen_ids.add(video_id)

        title = article.get("asset-title", "").strip()
        if not title:
            # fallback: look for a heading inside the article
            heading = article.find(["h1", "h2", "h3", "h4"])
            title = heading.get_text(strip=True) if heading else f"FIH Hockey Video {video_id}"

        results.append({
            "youtube_id":    video_id,
            "title":         title,
            "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "youtube_url":   f"https://www.youtube.com/watch?v={video_id}",
            "category":      "fih",
        })

        if len(results) >= MAX_VIDEOS:
            break

    return results


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Starting FIH video scraper")

    db = get_supabase()
    existing_ids = load_existing_video_ids(db)
    print(f"  Existing videos in DB: {len(existing_ids)}")

    r = fetch(FIH_VIDEOS_URL)
    if r is None:
        print("[error] Failed to fetch FIH videos page")
        sys.exit(1)

    videos = scrape_fih_videos(r.text)
    print(f"  Found {len(videos)} video(s) on page")

    new_count = 0
    for v in videos:
        if v["youtube_id"] in existing_ids:
            print(f"  [skip]  {v['youtube_id']}")
            continue

        print(f"  [new]   {v['title'][:60]}")
        row = {
            "youtube_id":    v["youtube_id"],
            "title":         v["title"],
            "title_sk":      v["title"],  # already English
            "thumbnail_url": v["thumbnail_url"],
            "youtube_url":   v["youtube_url"],
            "category":      v["category"],
            "published_at":  datetime.now(timezone.utc).isoformat(),
            "scraped_at":    datetime.now(timezone.utc).isoformat(),
        }
        db.table("videos").insert(row).execute()
        existing_ids.add(v["youtube_id"])
        new_count += 1
        time.sleep(0.3)

    print(f"\n[done] New FIH videos: {new_count}")


if __name__ == "__main__":
    main()
