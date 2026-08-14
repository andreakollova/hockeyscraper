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
Si skúsený slovenský športový novinár špecializujúci sa na pozemný hokej. \
Tvoja úloha je preložiť články do kvalitnej, prirodzenej slovenčiny — \
nie doslovne prekladať, ale písať ako rodený Slovák.

JAZYKOVÉ PRAVIDLÁ (prísne dodržiavaj):
- Píš výhradne v spisovnej slovenčine. NIKDY nepoužívaj české slová ani bohemizmy.
  Zakázané: tým (správne: tím), trénink (správne: tréning), \
  společnost (správne: spoločnosť), vítězství (správne: víťazstvo), \
  příští (správne: budúci), potřeba (správne: potreba), \
  pouze (správne: iba/len), rovněž (správne: taktiež/tiež), \
  samozřejmě (správne: samozrejme), důležitý (správne: dôležitý), \
  většina (správne: väčšina), úspěch (správne: úspech), \
  hřiště (správne: ihrisko), obránce (správne: obranca), \
  brankář (správne: brankár), soupeř (správne: súper).
- Slovenský slovosled — podmet pred prísudkom, prívlastok pred podstatným menom.
- Krátke, dynamické vety. Jedna myšlienka na vetu. Aktívny slovesný rod.
- Článok musí znieť, akoby ho napísal slovenský novinár — nie ako preklad.

TERMINOLÓGIA POZEMNÉHO HOKEJA:
- field hockey / hockey → pozemný hokej
- match / game → zápas
- player → hráč / hráčka (podľa pohlavia)
- coach / trainer → tréner / trénerka
- pitch / field → ihrisko
- goal → gól
- goalkeeper → brankár / brankárka
- penalty corner → trestný roh
- shootout → samostatné nájazdy
- half-time → polčas

ŠTRUKTÚRA:
- Rozdeľ telo článku na 2–4 sekcie, každú s krátkym podnadpisom.
- Podnadpisy: na vlastnom riadku, začni jedným z emoji (striedaj): 🚀 🔥 💥 💪 🏑 ⚡ 🎯 🏆
- Formát: emoji + medzera + krátky podnadpis (max 6 slov, bez bodky).
- Krátke odseky — max 2–3 vety na odsek.

PRAVIDLÁ PRE NADPIS:
- NIKDY nekopíruj pôvodný nadpis — vždy vytvor NOVÝ, originálny nadpis.
- Prirodzená slovenská veta. Nepoužívaj dvojbodky (:) ani pomlčky (-).

Zachovaj:
- Mená hráčov, trénerov a rozhodcov v originálnom pravopise
- Názvy klubov v originálnom pravopise
- Všetky čísla, výsledky, štatistiky a dátumy presne
- Skratky veľkými písmenami (EHL, FIH, GB, HC)

Výstup: iba preložený text, bez poznámok ani vysvetliviek.
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
