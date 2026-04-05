#!/usr/bin/env python3
"""Preloží existujúce články v DB ktoré ešte nemajú slovenský preklad."""

import os
import re
from pathlib import Path

_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from openai import OpenAI
from supabase import create_client

TRANSLATE_SYSTEM = """\
Si skúsený slovenský športový novinár a prekladateľ. Prekladáš správy o pozemnom hokeji z holandčiny do slovenčiny.

Jazykové požiadavky:
- Píš spisovnou slovenčinou, používaj správne gramatické tvary a skloňovanie
- Používaj prirodzené slovenské športové výrazy (nie doslovy z holandčiny)
- Vety formuluj ako slovenský novinár — dynamicky, stručne, zrozumiteľne
- Správne skloňuj: "pozemného hokeja", "v pozemnom hokeji", "hráč pozemného hokeja"

Terminológia — vždy nahraď:
- hockey / veldhockey → pozemný hokej
- hockeyclub / club → klub pozemného hokeja (alebo len "klub")
- hockeyster / speelster → hráčka pozemného hokeja
- hockeyspeler → hráč pozemného hokeja
- hockeywedstrijd → zápas pozemného hokeja
- hockeyseizoen → sezóna pozemného hokeja
- hoofdklasse → najvyššia liga
- promotieklasse → druhá liga
- goud / gouden → zlatý/á
- zilver / zilveren → strieborný/á
- finale → finále
- halve finale → semifinále
- trainer / coach → tréner

Zachovaj:
- Mená hráčov, trénerov a rozhodcov v originálnom pravopise
- Názvy klubov v originálnom pravopise (napr. Bloemendaal, Kampong, Den Bosch)
- Všetky čísla, výsledky, štatistiky a dátumy presne

Výstup: iba preložený text, bez poznámok ani vysvetliviek.
"""

def translate(title: str, text: str) -> tuple[str, str]:
    client = OpenAI()
    prompt = f"""Prelož nasledujúci článok o pozemnom hokeji z holandčiny do slovenčiny.

NADPIS:
{title}

TEXT:
{text}

Odpovedz presne v tomto formáte:
### NADPIS ###
<preložený nadpis>

### TEXT ###
<preložený text>"""

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=4096,
        messages=[
            {"role": "system", "content": TRANSLATE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    )
    output = response.choices[0].message.content.strip()

    title_sk = title
    text_sk = text
    title_match = re.search(r"### NADPIS ###\s*\n(.+?)(?:\n\n### TEXT ###|\Z)", output, re.DOTALL)
    text_match = re.search(r"### TEXT ###\s*\n(.+)", output, re.DOTALL)
    if title_match:
        title_sk = title_match.group(1).strip()
    if text_match:
        text_sk = text_match.group(1).strip()

    return title_sk, text_sk


def main():
    db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    # Načítaj články bez prekladu
    res = db.table("articles").select("id, title, text").is_("title_sk", "null").execute()
    articles = res.data
    print(f"Článkov na preklad: {len(articles)}")

    for i, article in enumerate(articles, 1):
        print(f"  [{i}/{len(articles)}] {article['title'][:60]}…")
        try:
            title_sk, text_sk = translate(article["title"], article["text"] or "")
            db.table("articles").update({
                "title_sk": title_sk,
                "text_sk": text_sk,
            }).eq("id", article["id"]).execute()
            print(f"    ✓ Preložené")
        except Exception as e:
            print(f"    ✗ Chyba: {e}")

    print("\nHotovo!")

if __name__ == "__main__":
    main()
