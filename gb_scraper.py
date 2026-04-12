#!/usr/bin/env python3
"""
Great Britain Hockey scraper → Supabase
- Scrapes latest news from greatbritainhockey.co.uk
- Rewrites articles in polished English field hockey journalism style
- Stores to Supabase articles table (same schema as hockey.nl scraper)
- title_sk / text_sk = rewritten English (ready for Discord/Instagram)
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

from openai import OpenAI
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

BASE_URL     = "https://www.greatbritainhockey.co.uk"
NEWS_URL     = "https://www.greatbritainhockey.co.uk/latest/news"
MAX_ARTICLES = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.greatbritainhockey.co.uk/",
}

REWRITE_SYSTEM = """\
You are a professional field hockey sports journalist and editor.

IMPORTANT RULES:
- This content is always about FIELD HOCKEY (played on grass or turf with sticks and a ball).
- Never use the words "ice hockey" or any ice hockey terminology.
- Always say "field hockey", "hockey match", "hockey player", "the pitch", etc.
- Gender: pay close attention to whether people are male or female. Use correct pronouns consistently.
- Preserve all facts, names, scores, and dates exactly as in the original.
- Do not add any information not in the original text.
- Return only the rewritten text — no preamble, no notes, no explanation.
- Rewrite in fresh, polished, publication-ready sports journalism style.
- If the article has subheadings or section titles, place one of these emojis before each \
(rotate through them): 🚀 🔥 💥 💪 🏑

HEADLINE RULES:
- NEVER use the original headline — always craft a NEW, original headline in your own words.
- The headline must capture the story's key angle but use completely different wording.
- Write as a natural, flowing English sentence. Think like a sports editor, not a translator.
- Use sentence case: capitalise only the first word and proper nouns/abbreviations (e.g. EHL, FIH, team names). \
Do NOT capitalise every word.
- Do NOT use colons (:) or dashes (-) in the headline.
- Vary the sentence structure: sometimes lead with the subject, sometimes with the result or action.
- Example: instead of "GB: victory over Belgium" → "Great Britain claim stunning victory over Belgium"
"""


def rewrite(title: str, text: str) -> tuple[str, str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("  [warn] OPENAI_API_KEY missing — skipping rewrite")
        return title, text

    client = OpenAI(api_key=api_key)
    prompt = f"""Rewrite this Great Britain field hockey article in fresh, \
publication-ready English sports journalism style. \
Preserve all facts exactly but make it engaging and stylistically distinct.

TITLE:
{title}

BODY:
{text}

Reply in exactly this format (keep the ### markers):
### TITLE ###
<rewritten title>

### BODY ###
<rewritten body>"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=4096,
            messages=[
                {"role": "system", "content": REWRITE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        output = response.choices[0].message.content.strip()
        title_rw = title
        text_rw  = text
        t_match = re.search(r"### TITLE ###\s*\n(.+?)(?:\n\n### BODY ###|\Z)", output, re.DOTALL)
        b_match = re.search(r"### BODY ###\s*\n(.+)", output, re.DOTALL)
        if t_match:
            title_rw = t_match.group(1).strip()
        if b_match:
            text_rw = b_match.group(1).strip()
        return title_rw, text_rw
    except Exception as e:
        print(f"  [warn] Rewrite failed: {e}")
        return title, text


def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("[error] Missing SUPABASE_URL / SUPABASE_KEY")
        sys.exit(1)
    return create_client(url, key)


def load_existing_urls(db: Client) -> set:
    res = db.table("articles").select("url").execute()
    return {row["url"] for row in res.data}


def fetch(url: str, retries: int = 3) -> "requests.Response | None":
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            print(f"  [warn] {url} attempt {attempt + 1}: {e}")
            time.sleep(2)
    return None


def get_article_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    urls = []
    # Match both absolute and relative article URLs
    abs_re = re.compile(r"^https?://www\.greatbritainhockey\.co\.uk/latest/news/[a-z0-9][a-z0-9\-]+$")
    rel_re = re.compile(r"^/latest/news/[a-z0-9][a-z0-9\-]+$")
    for a in soup.find_all("a", href=True):
        href = a["href"].rstrip("/")
        if abs_re.match(href):
            full_url = href
        elif rel_re.match(href):
            full_url = BASE_URL + href
        else:
            continue
        if full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)
    return urls[:MAX_ARTICLES]


def _is_boilerplate(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in [
        "cookie", "privacy policy", "terms and conditions",
        "subscribe to our", "follow us on", "sign up",
        "newsletter", "©", "all rights reserved",
    ])


def scrape_article(url: str) -> dict:
    r = fetch(url)
    if r is None:
        return {}

    soup = BeautifulSoup(r.text, "html.parser")

    # Title — h1 first, then og:title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        if og:
            title = og.get("content", "").strip()

    # Remove nav/header/footer noise
    for tag in soup.find_all(["header", "nav", "footer", "aside"]):
        tag.decompose()

    # Body text — try article tag, then main, then broad divs
    paragraphs = []
    containers = soup.find_all("article") or soup.find_all("main") or []
    if not containers:
        containers = soup.find_all("div", class_=re.compile(
            r"content|body|article|text|post|entry", re.I
        ))
    for container in containers:
        for p in container.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if txt and len(txt) > 40 and not _is_boilerplate(txt) and txt not in paragraphs:
                paragraphs.append(txt)

    text = "\n\n".join(paragraphs)

    # Image — og:image first, then largest img on page
    image_url = ""
    og_img = soup.find("meta", property="og:image")
    if og_img:
        image_url = og_img.get("content", "").strip()
    if not image_url:
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if (src and src.startswith("http")
                    and "logo" not in src.lower()
                    and "icon" not in src.lower()
                    and "avatar" not in src.lower()):
                image_url = src
                break

    return {"title": title, "text": text, "image_url": image_url}


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Starting GB Hockey scraper")

    db = get_supabase()
    existing_urls = load_existing_urls(db)
    print(f"  Existing articles in DB: {len(existing_urls)}")

    r = fetch(NEWS_URL)
    if r is None:
        print("[error] Failed to fetch GB Hockey news page")
        sys.exit(1)

    article_urls = get_article_links(r.text)
    if not article_urls:
        print("[warn] No article links found on listing page")
        return
    print(f"  Found {len(article_urls)} articles on page")

    new_count = 0
    for url in article_urls:
        if url in existing_urls:
            print(f"  [skip]  {url.split('/')[-1]}")
            continue

        print(f"  [fetch] {url.split('/')[-1]}")
        detail = scrape_article(url)
        time.sleep(0.8)

        if not detail or not detail.get("title"):
            print(f"  [error] Failed to scrape: {url}")
            continue

        title = detail["title"]
        text  = detail["text"]

        if not text.strip():
            print(f"  [skip]  No body text found for: {url.split('/')[-1]}")
            continue

        print(f"  [rewrite] {title[:60]}…")
        title_rw, text_rw = rewrite(title, text)

        row = {
            "url":        url,
            "title":      title,
            "text":       text,
            "title_sk":   title_rw,
            "text_sk":    text_rw,
            "image_url":  detail.get("image_url", ""),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "published":  True,
        }
        db.table("articles").insert(row).execute()
        existing_urls.add(url)
        new_count += 1
        print(f"  [saved]  {title[:70]}")

    print(f"\n  Done — new: {new_count}, total: {len(existing_urls)}")


if __name__ == "__main__":
    main()
