#!/usr/bin/env python3
"""
Send Discord webhook notifications for newly scraped articles.
Run this right after run_all.py — only notifies articles scraped in the last 2 hours
so each GitHub Actions run notifies only its own fresh articles.
"""
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}

_SOURCE_FLAGS = [
    ("greatbritainhockey", "🇬🇧", "GB Hockey"),
    ("hockey.ie",          "🇮🇪", "Hockey Ireland"),
    ("scottish-hockey",    "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Scottish Hockey"),
    ("hockey.org.au",      "🇦🇺", "Hockey Australia"),
    ("eshockey.es",        "🇪🇸", "Spain Hockey"),
    ("cahockey.org.ar",    "🇦🇷", "Argentina Hockey"),
    ("hockey.de",          "🇩🇪", "Hockey Germany"),
    ("hockey.be",          "🇧🇪", "Hockey Belgium"),
    ("hockeyindia",        "🇮🇳", "Hockey India"),
    ("eurohockey.org",     "🇪🇺", "EuroHockey"),
    ("fih.hockey",         "🏑",  "FIH Hockey"),
]

_EMBED_COLORS = {
    "🇳🇱": 0xFF8C00, "🇬🇧": 0x003087, "🇮🇪": 0x009A44,
    "🏴󠁧󠁢󠁳󠁣󠁴󠁿": 0x003078, "🇦🇺": 0xFFB300, "🇪🇸": 0xAA151B,
    "🇦🇷": 0x74ACE0, "🇩🇪": 0x505050, "🇧🇪": 0x0064C8,
    "🇮🇳": 0xFF9933, "🇪🇺": 0x004699, "🏑": 0x009664,
}


def _source_info(url: str) -> tuple[str, str]:
    for frag, flag, credit in _SOURCE_FLAGS:
        if frag in url:
            return flag, credit
    return "🇳🇱", "HockeyNL"


def get_new_articles(since_minutes: int = 120) -> list[dict]:
    """Fetch articles scraped in the last `since_minutes` minutes, not yet published."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).isoformat()
    params = {
        "select": "id,title,title_sk,text,text_sk,image_url,url,scraped_at",
        "published": "eq.false",
        "rejected": "not.is.true",
        "scraped_at": f"gte.{cutoff}",
        "order": "scraped_at.asc",
        "limit": "25",
    }
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/articles",
        headers=SUPABASE_HEADERS,
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def post_webhook(articles: list[dict]) -> None:
    embeds = []
    for a in articles[:10]:  # Discord allows max 10 embeds per message
        flag, credit = _source_info(a.get("url", ""))
        title = (a.get("title_sk") or a.get("title") or "(no title)")[:250]
        text = (a.get("text_sk") or a.get("text") or "")
        snippet = (text[:200] + "…") if len(text) > 200 else text
        snippet = snippet.replace("\n", " ").strip()

        embed = {
            "title": f"{flag} {title}",
            "description": snippet or "_(no preview)_",
            "color": _EMBED_COLORS.get(flag, 0xFFA500),
            "footer": {"text": f"Source: {credit} • Waiting for Discord bot approval"},
        }
        if a.get("url"):
            embed["url"] = a["url"]
        if a.get("image_url"):
            embed["thumbnail"] = {"url": a["image_url"]}

        embeds.append(embed)

    n = len(articles)
    payload = {
        "content": f"📰 **{n} new article{'s' if n != 1 else ''} scraped — waiting for approval**",
        "embeds": embeds,
    }
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
    resp.raise_for_status()


def main() -> None:
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL not set — skipping Discord notifications")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Supabase credentials not set — skipping")
        sys.exit(1)

    articles = get_new_articles(since_minutes=120)
    if not articles:
        print("No new articles in the last 2 hours — nothing to notify")
        return

    print(f"Sending Discord webhook for {len(articles)} article(s)...")
    for a in articles:
        print(f"  • {(a.get('title_sk') or a.get('title') or '')[:80]}")

    post_webhook(articles)
    print("✓ Discord webhook sent")


if __name__ == "__main__":
    main()
