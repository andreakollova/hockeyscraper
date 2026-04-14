#!/usr/bin/env python3
"""Run all hockey scrapers sequentially."""
import subprocess
import sys
import os
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
