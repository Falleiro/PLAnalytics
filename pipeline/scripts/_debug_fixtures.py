"""Debug: ver o que o SofaScore retorna para a agenda de uma data."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
from patchright.async_api import async_playwright
from scraper.scraper import _HEADLESS
from pipeline.scripts.predict_match import CSV_PATH

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-06-23"


TOURNAMENT_URL = "https://www.sofascore.com/pt/football/tournament/world/world-championship/16#id:58210"


async def main():
    all_events: dict[int, dict] = {}

    async def on_resp(response):
        if "/season/58210/events/" in response.url:
            try:
                data = await response.json()
                for e in data.get("events", []):
                    all_events[e["id"]] = e
            except Exception:
                pass

    async with async_playwright() as p:
        b = await p.chromium.launch(headless=_HEADLESS)
        ctx = await b.new_context(user_agent=UA)
        page = await ctx.new_page()
        page.on("response", on_resp)
        await page.goto(TOURNAMENT_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        await b.close()

    events = list(all_events.values())
    if not events:
        print("Nenhum evento da temporada 58210 interceptado."); return

    print(f"--- {len(events)} eventos do Mundial capturados; jogos em {DATE}: ---")
    import datetime as dt
    for e in sorted(events, key=lambda x: x.get("startTimestamp", 0)):
        ts = e.get("startTimestamp", 0)
        day = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%d") if ts else "?"
        if day != DATE:
            continue
        h = e.get("homeTeam", {}).get("name", "?")
        a = e.get("awayTeam", {}).get("name", "?")
        st = e.get("status", {}).get("type", "?")
        print(f"  [{st}] {h} x {a}")
    print("---")
    print(f"Total de eventos de futebol em {DATE}: {len(events)}")

    # Competicoes que contem 'world' ou 'cup'
    comps = {}
    for e in events:
        c = e.get("tournament", {}).get("name", "")
        comps[c] = comps.get(c, 0) + 1
    wc = {k: v for k, v in comps.items() if "world" in k.lower() or "cup" in k.lower()}
    print("\nCompeticoes com 'world'/'cup':")
    for k, v in sorted(wc.items(), key=lambda x: -x[1]):
        print(f"  {v:>3}  {k}")

    # Jogos entre times da nossa base
    df = pd.read_csv(CSV_PATH)
    known = set(df["home_team"]).union(df["away_team"])
    print("\nJogos entre times da NOSSA BASE (qualquer competicao):")
    found = False
    for e in events:
        h = e.get("homeTeam", {}).get("name", "")
        a = e.get("awayTeam", {}).get("name", "")
        c = e.get("tournament", {}).get("name", "")
        if h in known and a in known:
            found = True
            print(f"  {h} x {a}  ({c})")
    if not found:
        print("  (nenhum)")


if __name__ == "__main__":
    asyncio.run(main())
