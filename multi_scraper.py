#!/usr/bin/env python3
"""
Multi-country field hockey scraper → Supabase
Scrapes the latest 4 articles from:
  - Netherlands (hockey.nl)
  - Ireland   (hockey.ie)
  - Scotland  (scottish-hockey.org.uk)
  - Australia (hockey.org.au)
  - Spain     (eshockey.es)
  - Argentina (cahockey.org.ar)
  - Germany   (hockey.de)
  - Belgium   (hockey.be)

All articles are rewritten / translated into polished English.
Stored in the same Supabase `articles` table as the NL and GB scrapers.
Respects robots.txt conventions: polite crawl delays, only public news pages.
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

MAX_ARTICLES = 4  # scrape only the last 4 articles per site

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; HockeyNewsBot/1.0; +https://hockeytea.com)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Site configs ───────────────────────────────────────────────────────────────
# Each entry: (site_key, news_url, base_url, lang, article_path_re)
# lang: "en" = already English (rewrite for polish)
#       "es" = Spanish → translate to English
#       "de" = German  → translate to English
#       "fr" = French  → translate to English

SITES = [
    {
        "key":       "netherlands",
        "name":      "Hockey Netherlands",
        "news_url":  "https://www.hockey.nl/nieuws",
        "base_url":  "https://www.hockey.nl",
        "lang":      "nl",
        "link_re":   re.compile(r"^https?://(?:www\.)?hockey\.nl/nieuws/[a-z0-9][a-z0-9\-]+/?$", re.I),
        "rel_re":    re.compile(r"^/nieuws/[a-z0-9][a-z0-9\-]+/?$", re.I),
    },
    # Ireland uses WordPress REST API (news listing page has no article links)
    {
        "key":       "ireland",
        "name":      "Ireland Hockey",
        "news_url":  "https://hockey.ie/wp-json/wp/v2/posts?per_page=4&_fields=link",
        "base_url":  "https://hockey.ie",
        "lang":      "en",
        "wp_api":    True,  # fetch article URLs from WP REST API JSON
    },
    {
        "key":       "scotland",
        "name":      "Scottish Hockey",
        "news_url":  "https://www.scottish-hockey.org.uk/news/",
        "base_url":  "https://www.scottish-hockey.org.uk",
        "lang":      "en",
        # Articles live at root-level slugs; use CSS selectors to avoid nav links
        "selector":  "article a[href], h2 a[href], h3 a[href], .entry-title a[href]",
        "link_re":   re.compile(r"^https?://(?:www\.)?scottish-hockey\.org\.uk/[a-z0-9][a-z0-9\-]+/?$", re.I),
        "rel_re":    re.compile(r"^/[a-z0-9][a-z0-9\-]+/?$", re.I),
    },
    {
        "key":       "australia",
        "name":      "Hockey Australia",
        "news_url":  "https://www.hockey.org.au/news/",
        "base_url":  "https://www.hockey.org.au",
        "lang":      "en",
        "link_re":   re.compile(r"^https?://(?:www\.)?hockey\.org\.au/news/[a-z0-9][a-z0-9\-]+/?$", re.I),
        "rel_re":    re.compile(r"^/news/[a-z0-9][a-z0-9\-]+/?$", re.I),
    },
    {
        "key":       "spain",
        "name":      "Hockey Spain",
        "news_url":  "https://eshockey.es/noticias/",
        "base_url":  "https://eshockey.es",
        "lang":      "es",
        # Articles live at root-level slugs: /some-article-slug/
        "selector":  "article a[href], h2 a[href], h3 a[href]",
        "link_re":   re.compile(r"^https?://eshockey\.es/[a-z0-9\u00c0-\u017e][a-z0-9\u00c0-\u017e\-]+/?$", re.I),
        "rel_re":    re.compile(r"^/[a-z0-9\u00c0-\u017e][a-z0-9\u00c0-\u017e\-]+/?$", re.I),
    },
    {
        "key":       "argentina",
        "name":      "Argentina Hockey",
        "news_url":  "https://www.cahockey.org.ar/noticias/",
        "base_url":  "https://www.cahockey.org.ar",
        "lang":      "es",
        # Articles at /noticia/<slug>/<id>
        "link_re":        re.compile(r"^https?://(?:www\.)?cahockey\.org\.ar/noticia/.+$", re.I),
        "rel_re":         re.compile(r"^/noticia/.+$", re.I),
        # First img under /media/novedades/ that is NOT the thumbnail subfolder
        "image_selector": "img[src*='/media/novedades/']:not([src*='/thumbnail/'])",
    },
    {
        "key":            "india",
        "name":           "Hockey India",
        # Custom POST API — returns HTML fragment with article links
        "news_url":       "https://www.hockeyindia.org/api/get-posts",
        "base_url":       "https://www.hockeyindia.org",
        "lang":           "en",
        "hi_api":         True,
        "link_re":        re.compile(r"^https?://(?:www\.)?hockeyindia\.org/news/[a-z0-9][a-z0-9\-]+/?$", re.I),
        # Title lives in .content h1 (generic h1 = "Hockey India" is skipped by length)
        "title_selector": ".content h1",
        # Article image has class wp-image-XXXXX
        "image_selector": "img[class*='wp-image']",
    },
    {
        "key":       "germany",
        "name":      "Hockey Germany",
        "news_url":  "https://www.hockey.de/articles/",
        "base_url":  "https://www.hockey.de",
        "lang":      "de",
        # Articles at /articles/<slug>
        "link_re":        re.compile(r"^https?://(?:www\.)?hockey\.de/articles/[a-z0-9][a-z0-9\-]+/?$", re.I),
        "rel_re":         re.compile(r"^/articles/[a-z0-9][a-z0-9\-]+/?$", re.I),
        # og:image is a tiny 400x400 thumb; use the full-size hero image instead
        "image_selector": "img.custom-page__hero-image",
    },
    {
        "key":       "belgium",
        "name":      "Hockey Belgium",
        # Belgium uses WordPress REST API
        "news_url":  "https://hockey.be/wp-json/wp/v2/posts?per_page=4&_fields=link&lang=fr",
        "base_url":  "https://hockey.be",
        "lang":      "fr",
        "wp_api":    True,
    },
]

# ── OpenAI ─────────────────────────────────────────────────────────────────────

REWRITE_SYSTEM = """\
You are a professional field hockey sports journalist and editor writing for an English-language audience.

IMPORTANT RULES:
- This content is ALWAYS about FIELD HOCKEY (played on grass or turf with sticks and a ball).
- Never use the words "ice hockey" or any ice hockey terminology.
- Always say "field hockey", "hockey match", "hockey player", "the pitch", etc.
- Gender: pay close attention to whether people are male or female. Use correct pronouns consistently.
- Preserve all facts, names, scores, and dates exactly as in the original.
- Do not add any information not in the original text.
- Return ONLY the rewritten text — no preamble, no notes, no explanation.
- Rewrite in fresh, polished, publication-ready English sports journalism style.
- If the article has subheadings or section titles, place one of these emojis before each \
(rotate through them): 🚀 🔥 💥 💪 🏑
- For non-English sources, translate fully and naturally to English first, then apply the style.

HEADLINE RULES:
- Write as a natural, flowing English sentence.
- Do NOT use colons (:) or dashes (-) in the headline.
"""

LANG_NAMES = {"en": "English", "nl": "Dutch", "es": "Spanish", "de": "German", "fr": "French"}


def rewrite_article(title: str, text: str, source_lang: str = "en", country_name: str = "") -> tuple[str, str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("  [warn] OPENAI_API_KEY missing — skipping rewrite")
        return title, text

    client = OpenAI(api_key=api_key)
    src_lang = LANG_NAMES.get(source_lang, "English")
    action = "Translate and rewrite" if source_lang != "en" else "Rewrite"

    prompt = f"""{action} this {country_name} field hockey article from {src_lang} into fresh, \
publication-ready English sports journalism. Preserve all facts exactly.

TITLE:
{title}

BODY:
{text}

Reply in exactly this format (keep the ### markers):
### TITLE ###
<rewritten English title>

### BODY ###
<rewritten English body>"""

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
        t_match = re.search(r"### TITLE ###\s*\n(.+?)(?:\n\n### BODY ###|\Z)", output, re.DOTALL)
        b_match = re.search(r"### BODY ###\s*\n(.+)", output, re.DOTALL)
        title_rw = t_match.group(1).strip() if t_match else title
        text_rw  = b_match.group(1).strip() if b_match else text
        return title_rw, text_rw
    except Exception as e:
        print(f"  [warn] Rewrite failed: {e}")
        return title, text


# ── Supabase ───────────────────────────────────────────────────────────────────

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


# ── HTTP fetch ─────────────────────────────────────────────────────────────────

def fetch(url: str, retries: int = 3) -> "requests.Response | None":
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            print(f"  [warn] {url} attempt {attempt + 1}: {e}")
            time.sleep(3)
    return None


# ── Link extraction ────────────────────────────────────────────────────────────

def get_article_links_wp_api(site: dict) -> list[str]:
    """Fetch article URLs via WordPress REST API."""
    r = fetch(site["news_url"])
    if r is None:
        return []
    try:
        posts = r.json()
        return [p["link"].rstrip("/") for p in posts if p.get("link")][:MAX_ARTICLES]
    except Exception as e:
        print(f"  [warn] WP API parse error: {e}")
        return []


def get_article_links_hi_api(site: dict) -> list[str]:
    """Fetch article URLs from hockeyindia.org custom POST API."""
    try:
        r = requests.post(
            site["news_url"],
            json={"type": "news", "page": 1},
            headers={**HEADERS, "Content-Type": "application/json", "Referer": site["base_url"] + "/news"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        html = data.get("top_news", "") or data.get("data", "") or ""
        soup = BeautifulSoup(html, "html.parser")
        link_re = site["link_re"]
        seen: set[str] = set()
        urls: list[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].rstrip("/")
            if link_re.match(href) and href not in seen:
                seen.add(href)
                urls.append(href)
                if len(urls) >= MAX_ARTICLES:
                    break
        return urls
    except Exception as e:
        print(f"  [warn] HI API error: {e}")
        return []


def get_article_links(html: str, site: dict) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    urls: list[str] = []

    # Use CSS selector when provided (e.g. Scotland uses root-level slugs
    # mixed with nav links — restrict to article/heading anchors)
    candidates = (
        soup.select(site["selector"])
        if site.get("selector")
        else soup.find_all("a", href=True)
    )

    for a in candidates:
        href = a.get("href", "").rstrip("/") if hasattr(a, "get") else a["href"].rstrip("/")
        if not href:
            continue

        if site["link_re"].match(href):
            full = href
        elif site["rel_re"].match(href):
            full = site["base_url"] + href
        else:
            continue

        if full not in seen:
            seen.add(full)
            urls.append(full)

        if len(urls) >= MAX_ARTICLES:
            break

    return urls


# ── Boilerplate filter ─────────────────────────────────────────────────────────

_BOILERPLATE_KWS = [
    "cookie", "privacy policy", "terms and conditions", "subscribe",
    "follow us on", "sign up", "newsletter", "©", "all rights reserved",
    "javascript", "enable javascript", "cookies",
]


def _is_boilerplate(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _BOILERPLATE_KWS)


# ── Article detail scraper ─────────────────────────────────────────────────────

def scrape_article(url: str, image_selector: str = "", base_url: str = "", title_selector: str = "") -> dict:
    r = fetch(url)
    if r is None:
        return {}

    soup = BeautifulSoup(r.text, "html.parser")

    # Title — site-specific selector takes priority
    title = ""
    if title_selector:
        el = soup.select_one(title_selector)
        if el:
            title = el.get_text(" ", strip=True)
    # Fallback: scan h1-h3, skip headings ≤15 chars (likely section labels)
    if not title:
        for selector in ["h1", "h2", "h3"]:
            el = soup.find(selector)
            if el:
                t = el.get_text(" ", strip=True)
                if t and len(t) > 15:
                    title = t
                    break
    if not title:
        og = soup.find("meta", property="og:title")
        if og:
            title = og.get("content", "").strip()
    if not title:
        tag = soup.find("title")
        if tag:
            title = tag.get_text(" ", strip=True).split("|")[0].strip()

    # Remove navigation noise
    for tag in soup.find_all(["header", "nav", "footer", "aside", "script", "style"]):
        tag.decompose()

    # Body text — try article/main, then broad content divs
    paragraphs: list[str] = []
    containers = (
        soup.find_all("article")
        or soup.find_all("main")
        or soup.find_all("div", class_=re.compile(
            r"content|body|article|text|post|entry|news|story", re.I
        ))
    )
    for container in containers:
        for p in container.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if txt and len(txt) > 40 and not _is_boilerplate(txt) and txt not in paragraphs:
                paragraphs.append(txt)

    # Fallback: all paragraphs on page
    if not paragraphs:
        for p in soup.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if txt and len(txt) > 60 and not _is_boilerplate(txt) and txt not in paragraphs:
                paragraphs.append(txt)

    text = "\n\n".join(paragraphs)

    # Image — site-specific selector takes priority
    image_url = ""
    if image_selector:
        el = soup.select_one(image_selector)
        if el:
            src = el.get("src", "") or el.get("data-src", "")
            if src:
                image_url = src if src.startswith("http") else base_url.rstrip("/") + src
    if not image_url:
        og_img = soup.find("meta", property="og:image")
        if og_img:
            image_url = og_img.get("content", "").strip()
    if not image_url:
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "")
            if (src and src.startswith("http")
                    and not any(x in src.lower() for x in ["logo", "icon", "avatar", "flag", "sprite"])):
                image_url = src
                break

    # URL-encode any spaces or non-ASCII chars so the URL is valid for embeds/APIs
    if image_url:
        from urllib.parse import urlsplit, urlunsplit, quote
        parts = urlsplit(image_url)
        image_url = urlunsplit(parts._replace(path=quote(parts.path, safe="/%+@=:,!$&'()*;"), query=quote(parts.query, safe="=&%+")))

    return {"title": title, "text": text, "image_url": image_url}


# ── Per-site scraper ───────────────────────────────────────────────────────────

def scrape_site(db: Client, site: dict, existing_urls: set) -> int:
    print(f"\n  [{site['name']}] {site['news_url']}")

    if site.get("wp_api"):
        article_urls = get_article_links_wp_api(site)
    elif site.get("hi_api"):
        article_urls = get_article_links_hi_api(site)
    else:
        r = fetch(site["news_url"])
        if r is None:
            print(f"  [error] Failed to fetch {site['news_url']}")
            return 0
        article_urls = get_article_links(r.text, site)

    if not article_urls:
        print(f"  [warn] No article links found — may need selector tuning")
        return 0

    print(f"  Found {len(article_urls)} article link(s) to check")

    new_count = 0
    for url in article_urls:
        if url in existing_urls:
            print(f"  [skip]  {url.split('/')[-1] or url.split('/')[-2]}")
            continue

        print(f"  [fetch] {url.split('/')[-1] or url}")
        detail = scrape_article(url, image_selector=site.get("image_selector",""), base_url=site.get("base_url",""), title_selector=site.get("title_selector",""))
        time.sleep(1.0)  # polite crawl delay

        if not detail or not detail.get("title"):
            print(f"  [error] Failed to scrape or no title: {url}")
            continue

        if not detail.get("text", "").strip():
            print(f"  [skip]  No body text: {url.split('/')[-1]}")
            continue

        title = detail["title"]
        text  = detail["text"]

        print(f"  [rewrite] {title[:60]}…")
        title_rw, text_rw = rewrite_article(title, text, site["lang"], site["name"])

        row = {
            "url":        url,
            "title":      title,
            "text":       text,
            "title_sk":   title_rw,
            "text_sk":    text_rw,
            "image_url":  detail.get("image_url", ""),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        db.table("articles").insert(row).execute()
        existing_urls.add(url)
        new_count += 1
        print(f"  [saved]  {title_rw[:70]}")

        if new_count >= MAX_ARTICLES:
            break

    return new_count


# ── Main ───────────────────────────────────────────────────────────────────────

def main(sites_filter: list[str] | None = None):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Starting multi-country scraper")

    db = get_supabase()
    existing_urls = load_existing_urls(db)
    print(f"  Total existing articles in DB: {len(existing_urls)}")

    total_new = 0
    for site in SITES:
        if sites_filter and site["key"] not in sites_filter:
            continue
        new = scrape_site(db, site, existing_urls)
        total_new += new

    print(f"\n[done] Total new articles: {total_new}")


if __name__ == "__main__":
    import sys
    filter_keys = sys.argv[1:] or None
    main(filter_keys)
