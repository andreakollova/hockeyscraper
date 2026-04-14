#!/usr/bin/env python3
"""Run all hockey scrapers sequentially, then send push notification if new articles found."""
import subprocess
import sys
import os
import requests
from pathlib import Path

# Load .env
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

SCRIPTS = [
    "scraper.py",
    "gb_scraper.py",
    "multi_scraper.py",
    "fih_video_scraper.py",
]

base = Path(__file__).parent
total_new = 0

for script in SCRIPTS:
    print(f"\n=== Running {script} ===", flush=True)
    result = subprocess.run(
        [sys.executable, str(base / script)],
        cwd=str(base),
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"WARNING: {script} exited with code {result.returncode}", flush=True)

print("\n=== All scrapers done ===", flush=True)

# Send push notification if there are new articles
site_url = os.environ.get("SITE_URL", "https://hockeyrefresh.com")
api_key  = os.environ.get("PUBLISH_API_KEY", "")

if api_key:
    try:
        resp = requests.post(
            f"{site_url}/api/push/send",
            json={
                "title": "Hockey Refresh",
                "body": "New field hockey articles just dropped!",
                "url": site_url,
            },
            headers={"x-api-key": api_key},
            timeout=15,
        )
        print(f"Push notification sent: {resp.json()}", flush=True)
    except Exception as e:
        print(f"Push notification failed (non-critical): {e}", flush=True)
