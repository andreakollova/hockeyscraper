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
  - EuroHockey (eurohockey.org)
  - FIH       (fih.hockey)

All articles are rewritten / translated into polished English.
Stored in the same Supabase `articles` table as the NL and GB scrapers.
Respects robots.txt conventions: polite crawl delays, only public news pages.
"""

import json
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

MAX_ARTICLES = 5  # scrape only the last 5 articles per site

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
        # WordPress featured image (cloudfront CDN); falls back to og:image then generic
        "image_selector":   "img.wp-post-image, .post-thumbnail img, .entry-content img[src*='cloudfront']",
        # Used when no image is found on the article page
        "fallback_image":   "https://pozemak.sk/scotish-hockey.png",
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
        # /news/ shows more recent articles than /articles/
        "news_url":  "https://www.hockey.de/news/",
        "base_url":  "https://www.hockey.de",
        "lang":      "de",
        # Articles at /articles/<slug>
        "link_re":        re.compile(r"^https?://(?:www\.)?hockey\.de/articles/[a-z0-9][a-z0-9\-]+", re.I),
        "rel_re":         re.compile(r"^/articles/[a-z0-9][a-z0-9\-]+", re.I),
        # Title is in a dedicated element (h1/h2/h3 on the page are unrelated articles)
        "title_selector": "div.custom-page__headline",
        # og:image is a tiny 400x400 thumb; use the full-size hero image only (no fallback)
        "image_selector": "img.custom-page__hero-image",
        "no_og_image":    True,  # skip og:image fallback — no image is better than thumbnail
        # article body lives here; avoids scraping the "More Articles" section below
        "text_selector":  "div.custom-page__article",
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
    {
        "key":       "eurohockey",
        "name":      "EuroHockey",
        # WP backend lives at admin.eurohockey.org; articles served at eurohockey.org/<slug>/
        # Category 74 = general News
        "news_url":  "https://admin.eurohockey.org/wp-json/wp/v2/posts?per_page=5&categories=74&_fields=link",
        "base_url":  "https://eurohockey.org",
        "lang":      "en",
        "wp_api":    True,
    },
    {
        "key":       "fih",
        "name":      "FIH Hockey",
        # Next.js site. Try __NEXT_DATA__ first, then HTML link extraction.
        "news_url":  "https://www.fih.hockey/news",
        "base_url":  "https://www.fih.hockey",
        "lang":      "en",
        "next_data": True,
        "link_re":   re.compile(r"^https?://(?:www\.)?fih\.hockey/news/[a-z0-9][a-z0-9\-/]+[a-z0-9]/?$", re.I),
        "rel_re":    re.compile(r"^/news/[a-z0-9][a-z0-9\-/]+[a-z0-9]/?$", re.I),
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
- ALWAYS divide the article body into 2–4 sections with subheadings.
- Every subheading must be on its own line, start with one of these emojis (rotate through them): \
🚀 🔥 💥 💪 🏑 ⚡ 🎯 🏆
- Format: emoji + space + short subheading text (max 6 words, no period at end). Example: 🔥 Clinical second half
- Place each subheading on its own paragraph, followed by the section text.
- Never skip this — every article must have subheadings.
- For non-English sources, translate fully and naturally to English first, then apply the style.

HEADLINE RULES:
- NEVER translate the original headline directly — always craft a NEW, original headline.
- The headline must capture the story's key angle but use completely different wording.
- Write as a natural, flowing English sentence. Think like a sports editor, not a translator.
- Use sentence case: capitalise only the first word and proper nouns/abbreviations.
- Do NOT capitalise every word.
- Do NOT use colons (:) or dashes (-) in the headline.
- Vary the sentence structure: sometimes lead with the subject, sometimes with the result or action.

CAPITALISATION RULES (strictly enforced — a single lowercase club name or abbreviation is a critical error):
- Club/team names: ALWAYS written exactly as they are officially known — NEVER in all-lowercase.
  Dutch clubs: Den Bosch (NEVER "den bosch"), Oranje-Rood (NEVER "oranje-rood"), HC Rotterdam, \
Amsterdam, Kampong, Bloemendaal, Hurley, Pinoké, SCHC, Tilburg, HGC, Klein Zwitserland, \
HDM, Cartouche, Laren, Dames, Heren.
  Belgian clubs: Racing Club de Bruxelles, Léopold, Beerschot, Watducks, Royal Leopold Club.
  Spanish clubs: Club de Campo, Junior FC, Atlètic Terrassa, Polo, Egara, CE Manresa.
  German clubs: Rot-Weiss Köln, Uhlenhorst Mülheim, Club an der Alster, Düsseldorfer HC.
  Argentine clubs: Club Atlético San Martín, Los Leones, Racing Club.
  International: GB, Great Britain, England Hockey, Hockey Australia, Hockey India, FIH.
- Abbreviations: ALWAYS fully capitalised — NEVER lowercase. \
Examples: SCHC (not "schc"), EHL (not "ehl"), FIH, HNL, KB, GB, NL, HC, RC.
- Country/city names: ALWAYS capitalised. Examples: Netherlands, Amsterdam, London, Belgium, Argentina.
- Player names: ALWAYS correctly capitalised as proper nouns.
- If you are unsure of the exact capitalisation of a team name or abbreviation, \
preserve the capitalisation from the original source text exactly. When in doubt, capitalise.
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

def get_article_links_next_data(site: dict) -> list[str]:
    """Extract article URLs from Next.js __NEXT_DATA__ Apollo cache, then fall back to HTML links."""
    r = fetch(site["news_url"])
    if r is None:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    link_re = site["link_re"]
    base_url = site["base_url"]

    # Primary: parse __NEXT_DATA__ JSON (Next.js server-side props)
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            data = json.loads(script.string)
            # Walk entire structure looking for URI/link fields that match article pattern
            urls: list[str] = []
            seen: set[str] = set()

            def _walk(obj: object) -> None:
                if len(urls) >= MAX_ARTICLES:
                    return
                if isinstance(obj, dict):
                    for key in ("uri", "link", "href", "url", "slug"):
                        val = obj.get(key, "")
                        if not isinstance(val, str) or not val:
                            continue
                        full = val if val.startswith("http") else base_url.rstrip("/") + val
                        if link_re.match(full) and full not in seen:
                            seen.add(full)
                            urls.append(full)
                    for v in obj.values():
                        _walk(v)
                elif isinstance(obj, list):
                    for item in obj:
                        _walk(item)

            _walk(data)
            if urls:
                print(f"  [next_data] found {len(urls)} URLs")
                return urls
        except Exception as e:
            print(f"  [warn] __NEXT_DATA__ parse error: {e}")

    # Fallback: parse <a href> links from HTML
    print("  [next_data] falling back to HTML link extraction")
    return get_article_links(r.text, site)


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

def scrape_article(url: str, image_selector: str = "", base_url: str = "", title_selector: str = "", text_selector: str = "", no_og_image: bool = False) -> dict:
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

    # Body text — site-specific selector takes priority
    paragraphs: list[str] = []
    if text_selector:
        container = soup.select_one(text_selector)
        if container:
            for p in container.find_all("p"):
                txt = p.get_text(" ", strip=True)
                if txt and len(txt) > 40 and not _is_boilerplate(txt) and txt not in paragraphs:
                    paragraphs.append(txt)

    if not paragraphs:
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
    if not image_url and not no_og_image:
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
    elif site.get("next_data"):
        article_urls = get_article_links_next_data(site)
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
        detail = scrape_article(url, image_selector=site.get("image_selector",""), base_url=site.get("base_url",""), title_selector=site.get("title_selector",""), text_selector=site.get("text_selector",""), no_og_image=site.get("no_og_image", False))
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
        # Ensure title always starts with a capital letter
        if title_rw and title_rw[0].islower():
            title_rw = title_rw[0].upper() + title_rw[1:]

        image_url = detail.get("image_url", "") or site.get("fallback_image", "")
        row = {
            "url":        url,
            "title":      title,
            "text":       text,
            "title_sk":   title_rw,
            "text_sk":    text_rw,
            "image_url":  image_url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "published":  False,
        }
        try:
            db.table("articles").insert(row).execute()
        except Exception as e:
            if "duplicate" in str(e).lower() or "23505" in str(e):
                existing_urls.add(url)
                print(f"  [dup]    already in DB (skipping): {url.split('/')[-1]}")
                continue
            raise
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
