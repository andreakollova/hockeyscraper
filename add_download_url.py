#!/usr/bin/env python3
"""
Adds download_url column to the videos table in Supabase.
Requires SUPABASE_URL and SUPABASE_KEY in .env
"""

import os
import requests
from pathlib import Path

for line in (Path(__file__).parent / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# Extract project ref from URL: https://xxxx.supabase.co → xxxx
project_ref = SUPABASE_URL.replace("https://", "").split(".")[0]

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

sql = "ALTER TABLE videos ADD COLUMN IF NOT EXISTS download_url text;"

resp = requests.post(
    f"{SUPABASE_URL}/rest/v1/rpc/exec_sql",
    json={"sql": sql},
    headers=headers,
)

if resp.status_code == 200:
    print("✅ Stĺpec download_url pridaný.")
else:
    # exec_sql function doesn't exist — print SQL to paste manually
    print("❌ Automatické pridanie nefungovalo.")
    print()
    print("Skopíruj tento SQL a vlož ho do:")
    print(f"https://supabase.com/dashboard/project/{project_ref}/sql/new")
    print()
    print("─" * 50)
    print(sql)
    print("─" * 50)
