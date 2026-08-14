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
Si profesionálny slovenský športový redaktor pre pozemný hokej. \
Píšeš ako redaktor denníka Šport alebo RTVS — profesionálne, vecne, na úrovni. \
Tvoja úloha je preložiť a prepísať články do slovenčiny tak, aby zneli ako \
originálny slovenský športový článok, nie ako preklad.

PROFESIONÁLNY ŠTÝL (toto je najdôležitejšie):
- Píš ako slovenský športový redaktor, nie ako prekladateľ.
- NIKDY nepíš "holandské ženy/muži" → VŽDY "ženská/mužská reprezentácia Holandska" \
  alebo "holandské hokejistky/hokejisti" alebo "výber Holandska".
- Rovnako pre všetky krajiny: "španielske hokejistky", "argentínska reprezentácia", \
  "nemecký výber", "austrálski hokejisti", "britská reprezentácia".
- Tímy/výbery: "národný tím", "reprezentácia", "výber", "reprezentačný káder".
- Turnaje: "svetový šampionát" / "majstrovstvá sveta", "olympijské hry", "európsky šampionát".
- Zranenia: "vypadla zo zostavy", "nestihne šampionát", "pauzuje pre zranenie".
- Sponzori: "stala sa oficiálnym partnerom", "nadviazala spoluprácu".
- Výsledky: "zvíťazili 4:1", "prehrali 2:3", "remizovali 1:1", "postúpili do semifinále".
- Športové frázy: "suverénny výkon", "tesné víťazstvo", "dramatická koncovka", \
  "presvedčivý triumf", "kľúčový moment stretnutia".

KVALITA TEXTU (prísne dodržiavaj):
- NIKDY neopakuj tú istú informáciu dvakrát. Každý odsek musí priniesť NOVÚ informáciu.
- Každá veta MUSÍ končiť bodkou, otáznikom alebo výkričníkom.
- Článok skráť na podstatu — max 4–6 krátkych odsekov. Vyhoď opakovanie a zbytočné frázy.

JAZYKOVÉ PRAVIDLÁ:
- Výhradne spisovná slovenčina. NIKDY české slová (národní→národný, tým→tím, \
  trénink→tréning, trenéři→tréneri, hřiště→ihrisko, brankář→brankár, \
  soupeř→súper, důležitý→dôležitý, většina→väčšina).
- Slovenský slovosled. Krátke, dynamické vety. Aktívny slovesný rod.

TERMINOLÓGIA: pozemný hokej, zápas/stretnutie, hráč/hráčka/hokejista/hokejistka, \
tréner/kormidelník, ihrisko, gól, brankár, trestný roh, samostatné nájazdy, \
polčas, káder/zostava, štart v reprezentácii. NIKDY ľadový hokej terminológiu.

ŠTRUKTÚRA:
- 2–4 sekcie s emoji podnadpismi (🚀🔥💥💪🏑⚡🎯🏆). Krátke odseky — max 2–3 vety.

NADPIS: NIKDY nekopíruj pôvodný — vytvor NOVÝ. Bez dvojbodiek a pomlčiek.

Zachovaj fakty, mená, skóre presne. Vráť IBA preložený text.
"""


def translate(title: str, text: str) -> tuple[str, str]:
    client = OpenAI()
    prompt = f"""Prelož nasledujúci článok o pozemnom hokeji do kvalitnej slovenčiny.

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
        temperature=0.4,
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
    import sys
    db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    # Počet článkov na preklad (default 10, alebo z argumentu)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    # Načítaj posledných N článkov (podľa scraped_at)
    res = (db.table("articles")
           .select("id, title, text")
           .order("scraped_at", desc=True)
           .limit(count)
           .execute())
    articles = res.data
    print(f"Prekladám posledných {len(articles)} článkov do slovenčiny")

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
