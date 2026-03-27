#!/usr/bin/env python3
"""
Hockey.nl article scraper
- Downloads the 20 latest articles from hockey.nl/nieuws
- On each run adds only new articles (skips already saved ones)
- Saves metadata to articles.json
- Downloads images to ./images/
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.hockey.nl"
NIEUWS_URL = "https://www.hockey.nl/nieuws"
CDN_BASE = "https://cdn.static-hw.nl"
# On Render: set DATA_DIR=/data (persistent disk mount point)
# Locally: defaults to the script's own directory
_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
ARTICLES_FILE = _DATA_DIR / "articles.json"
IMAGES_DIR = _DATA_DIR / "images"

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

MAX_ARTICLES = 20


# ── helpers ───────────────────────────────────────────────────────────────────

def load_existing() -> dict:
    """Load articles.json; return dict keyed by article URL."""
    if ARTICLES_FILE.exists():
        with open(ARTICLES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {a["url"]: a for a in data}
    return {}


def save_articles(articles: dict) -> None:
    """Persist articles dict (values) sorted newest-first."""
    lst = sorted(articles.values(), key=lambda a: a.get("scraped_at", ""), reverse=True)
    with open(ARTICLES_FILE, "w", encoding="utf-8") as f:
        json.dump(lst, f, ensure_ascii=False, indent=2)


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


def image_filename(url: str) -> str:
    """Derive a safe local filename from an image URL."""
    name = urlparse(url).path.split("/")[-1]
    name = re.sub(r"[?#].*$", "", name)
    return name or "image.jpg"


def download_image(url: str) -> "str | None":
    """Download image, return local relative path or None on failure."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    fname = image_filename(url)
    local = IMAGES_DIR / fname
    if local.exists():
        return f"images/{fname}"
    r = fetch(url)
    if r is None:
        return None
    with open(local, "wb") as f:
        f.write(r.content)
    return f"images/{fname}"


# ── listing page parser ───────────────────────────────────────────────────────

def get_article_links(html: str) -> list[str]:
    """
    Extract article URLs from the /nieuws listing page.
    The rendered HTML contains <a href="/nieuws/[slug]"> links.
    """
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


# ── article detail scraper ────────────────────────────────────────────────────

def scrape_article(url: str) -> dict:
    """
    Fetch individual article page and extract:
    title, body text, main image URL.
    """
    r = fetch(url)
    if r is None:
        return {}

    soup = BeautifulSoup(r.text, "html.parser")

    # ── title ──────────────────────────────────────────────────────────────
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        if og:
            title = og.get("content", "").strip()

    # ── body text ──────────────────────────────────────────────────────────
    # Article content lives in <article class="prose ..."> elements.
    # Navigation/sidebar uses identical classes but sits inside <header>/<nav>.
    # Strategy: remove header and nav first, then collect prose <article> tags.
    for tag in soup.find_all(["header", "nav", "footer"]):
        tag.decompose()

    paragraphs = []
    for art in soup.find_all("article", class_="prose"):
        for p in art.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if txt and txt not in paragraphs:
                paragraphs.append(txt)

    text = "\n\n".join(paragraphs)

    # ── main image ─────────────────────────────────────────────────────────
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

    return {
        "title": title,
        "text": text,
        "image_url": image_url,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Starting hockey.nl scraper")
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    existing = load_existing()
    print(f"  Existing articles in JSON: {len(existing)}")

    # 1. Fetch listing page
    print(f"  Fetching {NIEUWS_URL} …")
    r = fetch(NIEUWS_URL)
    if r is None:
        print("[error] Could not fetch news page. Aborting.")
        sys.exit(1)

    # 2. Extract article URLs from HTML
    article_urls = get_article_links(r.text)
    if not article_urls:
        print("[error] No article links found. Page structure may have changed.")
        sys.exit(1)
    print(f"  Found {len(article_urls)} articles on listing page")

    # 3. Process each article – skip already saved ones
    new_count = 0
    for url in article_urls:
        if url in existing:
            print(f"  [skip]  {url.split('/')[-1]}")
            continue

        print(f"  [fetch] {url.split('/')[-1]}")
        article = scrape_article(url)
        time.sleep(0.6)   # polite crawl delay

        article["url"] = url
        article.pop("date", None)
        article["scraped_at"] = datetime.now(timezone.utc).isoformat()

        # download image
        if article.get("image_url"):
            local_path = download_image(article["image_url"])
            article["image_local"] = local_path or ""
            if local_path:
                print(f"           image → {local_path}")
        else:
            article["image_local"] = ""

        existing[url] = article
        new_count += 1

    # 4. Persist
    save_articles(existing)

    print(f"\n  Done.")
    print(f"  New articles added : {new_count}")
    print(f"  Total in JSON      : {len(existing)}")
    print(f"  Images dir         : {IMAGES_DIR}")
    print(f"  JSON file          : {ARTICLES_FILE}")


if __name__ == "__main__":
    main()
