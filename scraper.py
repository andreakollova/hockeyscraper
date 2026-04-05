#!/usr/bin/env python3
"""
Hockey.nl scraper → Supabase
- Stiahne 20 najnovších článkov z hockey.nl/nieuws
- Stiahne videá Hoofdklasse Dames + Heren z homepage hockey.nl
- Preloží title do slovenčiny (OpenAI GPT-4o-mini)
- Uloží do Supabase tabuliek `articles` a `videos`
- Pri ďalšom spustení preskočí už uložené záznamy
"""

import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Load .env file if present
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from openai import OpenAI
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

BASE_URL     = "https://www.hockey.nl"
NIEUWS_URL   = "https://www.hockey.nl/nieuws"
HOME_URL     = "https://www.hockey.nl"
CDN_BASE     = "https://cdn.static-hw.nl"
MAX_ARTICLES = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    "Referer": "https://www.hockey.nl/",
}

TRANSLATE_SYSTEM = """\
You are a professional field hockey sports journalist translating Dutch to English.
Rules:
- Translate naturally into polished English sports journalism style.
- Always use "field hockey" (never ice hockey terminology).
- Preserve player names, club names, scores, and dates exactly.
- Output only the translated text, no comments or explanations.
"""


# ── OpenAI preklad ─────────────────────────────────────────────────────────────

def translate(title: str, text: str) -> tuple[str, str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("  [warn] OPENAI_API_KEY chýba — preskakujem preklad")
        return title, text

    client = OpenAI(api_key=api_key)
    prompt = f"""Prelož nasledujúci článok o pozemnom hokeji z holandčiny do slovenčiny.

NADPIS:
{title}

TEXT:
{text}

Odpovedz presne v tomto formáte (zachovaj značky ###):
### NADPIS ###
<preložený nadpis>

### TEXT ###
<preložený text>"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=4096,
            messages=[
                {"role": "system", "content": TRANSLATE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        output = response.choices[0].message.content.strip()
        title_sk = title
        text_sk  = text
        title_match = re.search(r"### NADPIS ###\s*\n(.+?)(?:\n\n### TEXT ###|\Z)", output, re.DOTALL)
        text_match  = re.search(r"### TEXT ###\s*\n(.+)", output, re.DOTALL)
        if title_match:
            title_sk = title_match.group(1).strip()
        if text_match:
            text_sk  = text_match.group(1).strip()
        return title_sk, text_sk
    except Exception as e:
        print(f"  [warn] Preklad zlyhal: {e}")
        return title, text


def translate_title(title: str) -> str:
    """Translate a Dutch video title to English."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return title
    client = OpenAI(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            messages=[
                {"role": "system", "content": TRANSLATE_SYSTEM},
                {"role": "user", "content": f"Translate this title to English (title only, no comments):\n{title}"},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [warn] Title translation failed: {e}")
        return title


# ── Supabase ──────────────────────────────────────────────────────────────────

def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("[error] Chýbajú env premenné SUPABASE_URL a SUPABASE_KEY")
        sys.exit(1)
    return create_client(url, key)


def load_existing_urls(db: Client) -> set:
    res = db.table("articles").select("url").execute()
    return {row["url"] for row in res.data}


def load_existing_video_ids(db: Client) -> set:
    res = db.table("videos").select("youtube_id").execute()
    return {row["youtube_id"] for row in res.data}


def insert_article(db: Client, article: dict) -> None:
    db.table("articles").insert(article).execute()


def insert_video(db: Client, video: dict) -> None:
    db.table("videos").insert(video).execute()


# ── HTTP fetch ────────────────────────────────────────────────────────────────

def fetch(url: str, retries: int = 3) -> "requests.Response | None":
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            print(f"  [warn] {url} attempt {attempt+1}: {e}")
            time.sleep(2)
    return None


# ── Listing page ──────────────────────────────────────────────────────────────

def get_article_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    urls = []
    slug_re = re.compile(r"^/nieuws/[a-z0-9][a-z0-9\-]+$")
    for tag in soup.find_all("a", href=True):
        path = tag["href"].rstrip("/")
        if slug_re.match(path) and path not in seen:
            seen.add(path)
            urls.append(BASE_URL + path)
    return urls[:MAX_ARTICLES]


# ── Filters ───────────────────────────────────────────────────────────────────

def _is_editorial_note(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in [
        "redactie@hockey.nl",
        "redakcia@hockey.nl",
        "mail naar redactie",
        "laat het ons weten",
        "stuur een mail",
        "aanvullingen?",
        "iets mist",
    ])


# ── Article detail ────────────────────────────────────────────────────────────

def scrape_article(url: str) -> dict:
    r = fetch(url)
    if r is None:
        return {}

    soup = BeautifulSoup(r.text, "html.parser")

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        if og:
            title = og.get("content", "").strip()

    for tag in soup.find_all(["header", "nav", "footer"]):
        tag.decompose()
    paragraphs = []
    for art in soup.find_all("article", class_="prose"):
        for p in art.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if txt and txt not in paragraphs:
                paragraphs.append(txt)
    text = "\n\n".join(p for p in paragraphs if not _is_editorial_note(p))

    image_url = ""
    og_img = soup.find("meta", property="og:image")
    if og_img:
        image_url = og_img.get("content", "").strip()
    if not image_url:
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if CDN_BASE in src or "media/" in src:
                image_url = src
                break

    return {"title": title, "text": text, "image_url": image_url}


# ── Video scraping z homepage ─────────────────────────────────────────────────

def scrape_videos_from_homepage(html: str) -> list[dict]:
    """
    Parsuje homepage hockey.nl a vracia videá rozdelené podľa kategórie.
    Hľadá sekcie s nadpisom obsahujúcim 'Dames' alebo 'Heren',
    potom v každej sekcii zbiera YouTube linky a thumbnaily.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Nájdeme všetky YouTube watch linky s h3 názvom
    # Každý link má tvar: <a href="https://www.youtube.com/watch?v=...">...<h3>Názov</h3>...</a>
    yt_re = re.compile(r"youtube\.com/watch\?v=([\w-]+)")

    # Prejdeme celý DOM a ku každému video linku zistíme kategóriu
    # podľa najbližšieho nadradeného elementu obsahujúceho "Dames"/"Heren" v headingu
    for a_tag in soup.find_all("a", href=yt_re):
        href = a_tag.get("href", "")
        m = yt_re.search(href)
        if not m:
            continue
        video_id = m.group(1)

        # Nadpis videa
        h3 = a_tag.find("h3")
        title = h3.get_text(strip=True) if h3 else ""
        if not title:
            continue

        # Thumbnail
        img = a_tag.find("img", src=re.compile(r"i\.ytimg\.com"))
        thumbnail = img["src"] if img else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        # Kategória — hľadáme najbližší ancestor s textom Dames/Heren
        category = _detect_category(a_tag)

        results.append({
            "youtube_id": video_id,
            "title": title,
            "thumbnail_url": thumbnail,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "category": category,
        })

    return results


def _detect_category(tag) -> str:
    """
    Prejde nadradenou hierarchiou tagu a hľadá sekciu s 'Dames' alebo 'Heren'.
    """
    node = tag.parent
    depth = 0
    while node and node.name and depth < 15:
        text = node.get_text(" ", strip=True)
        # Hľadáme heading obsahujúci Dames alebo Heren
        for heading in node.find_all(["h1", "h2", "h3", "h4", "span", "p"], limit=5):
            t = heading.get_text(strip=True)
            if "dames" in t.lower():
                return "dames"
            if "heren" in t.lower():
                return "heren"
        node = node.parent
        depth += 1
    return "heren"  # fallback


def scrape_videos(db: Client, html: str) -> int:
    existing_ids = load_existing_video_ids(db)
    print(f"  Existujúce videá v DB: {len(existing_ids)}")

    videos = scrape_videos_from_homepage(html)
    print(f"  Nájdených {len(videos)} videí na homepage")

    new_count = 0
    for v in videos:
        if v["youtube_id"] in existing_ids:
            print(f"    [skip]  {v['youtube_id']}")
            continue

        print(f"    [new]   [{v['category']}] {v['title'][:60]}")
        title_sk = translate_title(v["title"])

        row = {
            "youtube_id":    v["youtube_id"],
            "title":         v["title"],
            "title_sk":      title_sk,
            "thumbnail_url": v["thumbnail_url"],
            "youtube_url":   v["youtube_url"],
            "category":      v["category"],
            "published_at":  datetime.now(timezone.utc).isoformat(),
            "scraped_at":    datetime.now(timezone.utc).isoformat(),
        }
        insert_video(db, row)
        existing_ids.add(v["youtube_id"])
        new_count += 1
        time.sleep(0.3)

    return new_count


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Starting hockey.nl video scraper")

    db = get_supabase()

    # Articles are now handled by multi_scraper.py (Dutch → English)
    # This script only scrapes hockey.nl videos (Hoofdklasse Dames / Heren)

    print(f"  Fetching homepage for videos …")
    r_home = fetch(HOME_URL)
    if r_home is None:
        print("[error] Failed to load homepage — skipping videos")
        sys.exit(1)

    new_videos = scrape_videos(db, r_home.text)
    print(f"  Videos — new: {new_videos}")

    print(f"\n[done]")


if __name__ == "__main__":
    main()
