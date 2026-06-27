"""
World Cup Analytics — Atualização incremental dos jogos da Copa do Mundo

Descobre a data do jogo mais recente já carregado em `matches` e, em vez de varrer
seleção por seleção, busca diretamente na página do torneio da Copa do Mundo os
jogos JÁ FINALIZADOS que aconteceram daquela data em diante e que ainda NÃO estão
na base. Mantém o banco em dia sem reprocessar tudo.

Como funciona:
  1. Lê max(match_date) em `matches` → recua para o início (00:00 UTC) daquele
     dia, para não perder outros jogos do mesmo dia ainda não capturados.
  2. Intercepta os eventos da temporada do Mundial na página do torneio (mesmo
     truque do predict_today: o endpoint direto dá 403).
  3. Filtra: finalizados, data >= o corte, ainda não no banco.
  4. Para cada jogo, abre a página da partida, captura stats/lineups/incidents e
     faz upsert no Supabase — uma linha por seleção (mandante e visitante), na
     perspectiva de cada uma, como o scraper por seleção já fazia.

IMPORTANTE: o SofaScore bloqueia modo headless. Rode com browser visível.

Uso (da raiz do projeto):
    $env:PLAYWRIGHT_HEADLESS="false"; uv run pipeline/scripts/update_from_last_date.py
    # forçar uma data de corte específica:
    $env:PLAYWRIGHT_HEADLESS="false"; uv run pipeline/scripts/update_from_last_date.py --since 2026-06-20
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import datetime, time, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from loguru import logger
from patchright.async_api import async_playwright

from scraper.scraper import (
    MATCH_DELAY_MAX,
    MATCH_DELAY_MIN,
    SofaScoreScraper,
    _HEADLESS,
    _setup_logging,
    load_teams,
)
from scraper.models import Match, TeamConfig
from pipeline.scripts.scrape_and_load import _existing_event_ids
from pipeline.supabase_client import get_client, upsert_matches

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
# Página do Mundial 2026 no SofaScore (uniqueTournament 16 / season 58210).
# O endpoint scheduled-events/{date} dá 403; a página do torneio carrega os
# eventos da temporada e nós os interceptamos (mesmo padrão do scraper).
WC_TOURNAMENT_URL = "https://www.sofascore.com/pt/football/tournament/world/world-championship/16#id:58210"
WC_SEASON_EVENTS = "/season/58210/events/"


def latest_match_date() -> datetime | None:
    """Maior match_date em `matches` (datetime tz-aware), ou None se vazia."""
    client = get_client()
    rows = (
        client.table("matches")
        .select("match_date")
        .order("match_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    return datetime.fromisoformat(rows[0]["match_date"])


async def fetch_wc_events(page) -> list[dict]:
    """Intercepta todos os eventos da temporada do Mundial na página do torneio.

    Reaproveita a `page` já aberta para, em seguida, buscar os detalhes de cada
    jogo sem precisar reabrir o browser.
    """
    all_events: dict[int, dict] = {}

    async def on_resp(response):
        if WC_SEASON_EVENTS in response.url:
            try:
                data = await response.json()
                for e in data.get("events", []):
                    all_events[e["id"]] = e
            except Exception:
                pass

    page.on("response", on_resp)
    logger.info("Buscando jogos da Copa na página do torneio...")
    await page.goto(WC_TOURNAMENT_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(5000)  # deixa carregar os blocos de rodadas
    page.remove_listener("response", on_resp)
    return list(all_events.values())


def select_new_finished(
    events: list[dict],
    since: datetime | None,
    seen: set[int],
) -> list[dict]:
    """Filtra eventos finalizados, a partir do corte e ainda não no banco."""
    since_ts = since.timestamp() if since is not None else None
    out = []
    for e in events:
        if e.get("status", {}).get("type") != "finished":
            continue
        ts = e.get("startTimestamp", 0)
        if since_ts is not None and ts < since_ts:
            continue
        if e.get("id") in seen:
            continue
        out.append(e)
    out.sort(key=lambda e: e.get("startTimestamp", 0))
    return out


async def run(since: datetime | None) -> None:
    if _HEADLESS:
        logger.warning(
            "PLAYWRIGHT_HEADLESS não está 'false' — SofaScore bloqueia headless. "
            "Rode com PLAYWRIGHT_HEADLESS=false."
        )

    teams = load_teams()
    team_by_sofascore_id: dict[int, TeamConfig] = {t.sofascore_id: t for t in teams}
    seen = _existing_event_ids()
    logger.info(f"{len(seen)} jogos já no banco (serão pulados)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=_HEADLESS)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        events = await fetch_wc_events(page)
        targets = select_new_finished(events, since, seen)
        if not targets:
            logger.success("Nenhum jogo novo da Copa para carregar — base em dia.")
            await browser.close()
            return
        logger.info(f"{len(targets)} jogo(s) novo(s) da Copa para carregar")

        scraper = SofaScoreScraper(page)
        # slug da seleção → lista de jogos (dict) para upsert em lote no fim
        by_slug: dict[str, list[dict]] = {}

        for i, event in enumerate(targets, start=1):
            eid = event["id"]
            home = event.get("homeTeam", {})
            away = event.get("awayTeam", {})
            confronto = f"{home.get('name', '?')} x {away.get('name', '?')}"
            logger.info(f"[{i}/{len(targets)}] Event {eid} — {confronto}")
            try:
                raw = await scraper._fetch_event_details(event)
                if raw is None:
                    logger.warning(f"Event {eid}: detalhes não capturados — pulando")
                    continue
                # Uma linha por seleção conhecida (mandante e visitante)
                for side_team in (home, away):
                    tcfg = team_by_sofascore_id.get(side_team.get("id"))
                    if tcfg is None:
                        continue
                    match = Match.model_validate({"team_name": tcfg.name, **raw})
                    by_slug.setdefault(tcfg.slug, []).append(match.model_dump(mode="json"))
            except Exception as e:
                logger.error(f"Event {eid} falhou: {e}")
            await asyncio.sleep(random.uniform(MATCH_DELAY_MIN, MATCH_DELAY_MAX))

        await browser.close()

    # Upsert por seleção
    total = 0
    for slug, matches in by_slug.items():
        upsert_matches(slug, matches)
        total += len(matches)
    logger.success(
        f"Concluído — {len(targets)} jogo(s) novo(s), {total} linha(s) "
        f"carregada(s) em {len(by_slug)} seleção(ões)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Atualiza o banco com os jogos da Copa finalizados desde a última data carregada",
    )
    parser.add_argument(
        "--since", type=str, default=None, metavar="YYYY-MM-DD",
        help="Força a data de corte (default: dia do último jogo no banco)",
    )
    parser.add_argument("--debug", action="store_true", help="Logging DEBUG")
    args = parser.parse_args()

    _setup_logging(debug=args.debug)

    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        logger.info(f"Data de corte (forçada): {since.date()}")
    else:
        last = latest_match_date()
        if last is None:
            since = None
            logger.warning(
                "Tabela `matches` vazia — sem data de referência; "
                "carregando todos os jogos finalizados da Copa."
            )
        else:
            # Recua para 00:00 UTC do dia do último jogo, para pegar outros jogos
            # do mesmo dia que ainda não capturamos.
            since = datetime.combine(
                last.astimezone(timezone.utc).date(), time.min, tzinfo=timezone.utc
            )
            logger.info(f"Último jogo no banco: {last} → corte a partir de {since.date()}")

    asyncio.run(run(since))


if __name__ == "__main__":
    main()
