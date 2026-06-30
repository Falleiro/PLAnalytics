"""
World Cup Analytics — Previsão de TODOS os jogos de Copa de uma data

Descobre os jogos do dia no SofaScore (endpoint scheduled-events, via fetch no
contexto da página — herda o token anti-bot), filtra os jogos da Copa do Mundo
entre seleções que temos na base, treina o melhor modelo UMA vez e prevê todos.

Uso (da raiz do projeto, com browser visível):
    $env:PLAYWRIGHT_HEADLESS="false"; uv run pipeline/scripts/predict_today.py
    $env:PLAYWRIGHT_HEADLESS="false"; uv run pipeline/scripts/predict_today.py --date 2026-06-23
    # incluir tambem amistosos/qualificatorias entre times da base:
    $env:PLAYWRIGHT_HEADLESS="false"; uv run pipeline/scripts/predict_today.py --all-comps
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
from loguru import logger
from patchright.async_api import async_playwright

from scraper.scraper import _HEADLESS
from pipeline.scripts.predict_match import (
    CSV_PATH, build_training_table, train_best_model, predict_one,
)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
# Página do Mundial 2026 no SofaScore (uniqueTournament 16 / season 58210).
# Um request DIRETO à API dá 403; já um fetch no CONTEXTO da página (mesma
# origem www.sofascore.com) herda o token anti-bot e retorna 200 — é o mesmo
# truque do scraper. As páginas events/next/0 e events/last/0 da temporada
# retornam 200 confiavelmente (~30 jogos cada, cobrindo vários dias em volta de
# agora); páginas mais fundas (next/1, last/1, ...) tomam 403, mas next/0+last/0
# já bastam para prever os jogos de hoje/amanhã.
WC_TOURNAMENT_URL = "https://www.sofascore.com/pt/football/tournament/world/world-championship/16#id:58210"
WC_SEASON_EVENTS = "/season/58210/events/"  # trecho usado p/ interceptar (fallback)
WC_SEASON_EVENTS_PATH = "/unique-tournament/16/season/58210/events"
WC_UNIQUE_TOURNAMENT_ID = 16  # uniqueTournament da Copa do Mundo no SofaScore
API_PAGE_BASE = "https://www.sofascore.com/api/v1"

# Fuso de Brasília (UTC−3). Offset fixo: o Brasil não usa mais horário de verão
# desde 2019, então não precisamos de tzdata. A data do dia e os horários
# exibidos seguem o horário de início da partida neste fuso.
BRT = timezone(timedelta(hours=-3))

# "Dia de jogo": a noite de uma rodada se estende pela madrugada. Jogos que
# começam antes deste horário (BRT) contam como o dia ANTERIOR — ex.: um jogo
# 00:00 do dia 27 pertence à rodada do dia 26. Os jogos da Copa vão de ~13h às
# ~00h BRT, então um corte às 06:00 separa as noites sem risco de pegar jogos
# diurnos.
DAY_START_HOUR = 6


async def _page_fetch_json(page, url: str) -> dict | None:
    """Fetch de uma URL da API no contexto da página (herda o token anti-bot)."""
    js = """async (u) => {
        try { const r = await fetch(u); if (r.ok) return await r.json(); }
        catch (e) { /* ignore */ }
        return null;
    }"""
    try:
        return await page.evaluate(js, url)
    except Exception as e:
        logger.warning(f"page fetch falhou ({url}): {e}")
        return None


async def fetch_wc_events() -> list[dict]:
    """Busca os eventos do Mundial em volta de agora (próximos + recentes).

    Combina duas fontes para robustez:
      - fetch determinístico de events/next/0 e events/last/0 no contexto da
        página (cada um ~30 jogos → cobre vários dias para cada lado);
      - interceptação dos eventos que a própria página dispara (fallback).
    """
    if _HEADLESS:
        logger.warning("PLAYWRIGHT_HEADLESS != false — SofaScore bloqueia headless.")
    all_events: dict[int, dict] = {}

    async def on_resp(response):
        if WC_SEASON_EVENTS in response.url:
            try:
                data = await response.json()
                for e in data.get("events", []):
                    all_events[e["id"]] = e
            except Exception:
                pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=_HEADLESS)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        page.on("response", on_resp)
        await page.goto(WC_TOURNAMENT_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(4000)  # deixa a página carregar / disparar as chamadas

        # Fonte principal: próximos e últimos jogos da temporada, via fetch na
        # própria página. next/0 = ~30 jogos a partir de agora; last/0 = ~30 já
        # terminados. Juntos cobrem hoje, amanhã e os dias recentes.
        for kind in ("next", "last"):
            data = await _page_fetch_json(page, f"{API_PAGE_BASE}{WC_SEASON_EVENTS_PATH}/{kind}/0")
            for e in (data or {}).get("events", []):
                all_events[e["id"]] = e
            await page.wait_for_timeout(1200)  # evita 403 de rate-limit no 2º fetch

        await browser.close()

    logger.info(f"{len(all_events)} eventos capturados do SofaScore")
    return list(all_events.values())


STATUS_PT = {"finished": "terminado", "inprogress": "em andamento", "notstarted": "agendado"}


def _match_day(ko: datetime) -> str:
    """Dia de jogo (BRT) ao qual o kickoff pertence, tratando a madrugada como a
    noite anterior (ver DAY_START_HOUR). Ex.: 00:00 do dia 27 → '2026-06-26'."""
    return (ko - timedelta(hours=DAY_START_HOUR)).strftime("%Y-%m-%d")


def filter_fixtures(events: list[dict], date_str: str, known_teams: set[str]) -> list[dict]:
    """Filtra os eventos do Mundial para o dia de jogo pedido e times da base."""
    out = []
    for e in events:
        # mantém só a Copa do Mundo (next/last da temporada já são só dela, mas
        # a interceptação pode trazer outros torneios)
        tid = e.get("tournament", {}).get("uniqueTournament", {}).get("id")
        if tid != WC_UNIQUE_TOURNAMENT_ID:
            continue
        ts = e.get("startTimestamp", 0)
        if not ts:
            continue
        ko = datetime.fromtimestamp(ts, tz=BRT)  # início da partida em horário de Brasília
        if _match_day(ko) != date_str:           # agrupa a madrugada com a noite anterior
            continue
        home = e.get("homeTeam", {}).get("name", "")
        away = e.get("awayTeam", {}).get("name", "")
        if home not in known_teams or away not in known_teams:
            continue
        out.append({"home": home, "away": away, "competition": "FIFA World Cup",
                    "kickoff": ko, "status": e.get("status", {}).get("type", "?")})
    out.sort(key=lambda x: x["kickoff"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Previsão de todos os jogos do Mundial de uma data")
    ap.add_argument("--date", default=None, help="Data YYYY-MM-DD (default: hoje, horário de Brasília)")
    args = ap.parse_args()

    # "Hoje" = dia de jogo atual: às 02:00 BRT ainda estamos na noite do dia
    # anterior, então aplicamos o mesmo offset do _match_day.
    date_str = args.date or _match_day(datetime.now(BRT))
    match_date = pd.Timestamp(date_str, tz="UTC")

    # 1) base + times conhecidos
    print(f"Carregando base: {CSV_PATH.name}")
    df0 = pd.read_csv(CSV_PATH, parse_dates=["match_date"])
    known_teams = set(df0["home_team"]).union(df0["away_team"])

    # 2) jogos do dia (próximos + recentes da temporada, via fetch na página)
    print(f"Buscando jogos do Mundial para {date_str} (Brasília) no SofaScore...")
    events = asyncio.run(fetch_wc_events())
    fixtures = filter_fixtures(events, date_str, known_teams)
    if not fixtures:
        print(f"Nenhum jogo do Mundial encontrado para {date_str} entre times da base.")
        return
    print(f"{len(fixtures)} jogo(s) encontrado(s).")

    # 3) treina o modelo UMA vez
    print("Treinando o melhor modelo (Random Forest calibrado) em toda a base...")
    df, tm, L, elo = build_training_table(df0)
    model, prep, le, cls = train_best_model(L)

    # 4) preve cada jogo
    print("\n" + "=" * 74)
    print(f"  PREVISOES DO MUNDIAL — {date_str}")
    print("=" * 74)
    print(f"  {'Jogo':<34} {'V':>6} {'E':>6} {'D':>6}  {'horario/status':>16}")
    print("-" * 74)
    for fx in fixtures:
        pV, pE, pD = predict_one(model, prep, cls, tm, df, L, elo,
                                 fx["home"], fx["away"], match_date, fx["competition"])
        ko = fx["kickoff"].strftime("%H:%M")
        st = STATUS_PT.get(fx["status"], fx["status"])
        confronto = f"{fx['home']} x {fx['away']}"
        print(f"  {confronto:<34} {pV*100:5.1f}% {pE*100:5.1f}% {pD*100:5.1f}%  {ko+' '+st:>16}")
    print("=" * 74)
    print("  V = vitoria do 1o time | E = empate | D = vitoria do 2o time | horario de Brasilia")


if __name__ == "__main__":
    main()
