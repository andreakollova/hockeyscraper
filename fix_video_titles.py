#!/usr/bin/env python3
"""
One-off script: translate all video title_sk fields to English using GPT-4o-mini.
Run once: python fix_video_titles.py
"""

import os
from pathlib import Path

_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from supabase import create_client
from openai import OpenAI

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_KEY   = os.environ["OPENAI_API_KEY"]

db     = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_KEY)

SYSTEM = (
    "You are a sports journalist. Translate the following field hockey video title to "
    "natural English. Output only the translated title, nothing else."
)


def translate_to_english(title: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=120,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user",   "content": title},
        ],
    )
    return resp.choices[0].message.content.strip()


def main():
    res = db.table("videos").select("id, title, title_sk").execute()
    videos = res.data or []
    print(f"Found {len(videos)} videos")

    fixed = 0
    for v in videos:
        original = v["title"]
        current  = v["title_sk"] or ""

        english = translate_to_english(original)

        if english == current:
            print(f"  [skip] {original[:60]}")
            continue

        db.table("videos").update({"title_sk": english}).eq("id", v["id"]).execute()
        print(f"  [ok]   {original[:50]!r}  →  {english!r}")
        fixed += 1

    print(f"\nDone — updated {fixed}/{len(videos)} titles.")


if __name__ == "__main__":
    main()
