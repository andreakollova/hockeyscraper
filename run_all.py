#!/usr/bin/env python3
"""Run all hockey scrapers sequentially."""
import subprocess
import sys
import os
from pathlib import Path

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
    )
    if result.returncode != 0:
        print(f"WARNING: {script} exited with code {result.returncode}", flush=True)

print("\n=== All scrapers done ===", flush=True)
