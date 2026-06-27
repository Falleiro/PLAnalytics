"""
World Cup Analytics — Re-fetch de stats de jogador POR ID DE EVENTO

Recupera partidas que estão **sem stats de jogador** (sem rating) navegando
direto pela URL curta `https://www.sofascore.com/event/{id}` — que o SofaScore
redireciona para a página da partida. Diferente do `scrape_and_load.py`, NÃO
depende da lista de eventos da página do time (que só expõe os ~30 jogos mais
recentes), então alcança **jogos antigos** fora dessa janela.

Fluxo:
  1. Lê do Supabase os eventos cujo `match` não tem nenhum jogador com rating.
  2. Para cada um, navega por id, busca detalhes (com retry de lineups) e
     re-faz o upsert (idempotente) sob a seleção dona da linha.
  3. Eventos que voltarem ainda sem rating são lacuna real da fonte — apenas
     logados.

Uso (Windows / PowerShell):
    $env:PLAYWRIGHT_HEADLESS="false"; python pipeline/scripts/refetch_missing_by_id.py
    $env:PLAYWRIGHT_HEADLESS="false"; python pipeline/scripts/refetch_missing_by_id.py --event 10752579
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from loguru import logger
from patchright.async_api import async_playwright

from scraper.scraper import (
    SofaScoreScraper,
    _HEADLESS,
    _setup_logging,
    load_teams,
)
from scraper.models import Match
from pipeline.supabase_client import get_client, upsert_matches

# Delays conservadores — o backfill faz MUITAS navegações seguidas e o SofaScore
# bloqueia o IP se for agressivo demais. Levemente reduzidos (com workers=2 por
# padrão, o ganho de throughput vem do paralelismo, não de pausas curtas demais).
BACKFILL_DELAY_MIN = 2.0
BACKFILL_DELAY_MAX = 4.0
# Falhas consecutivas que indicam um provável BLOCK do site → aborta de forma
# limpa (o progresso já está salvo, pois o upsert é incremental).
BLOCK_THRESHOLD = 8


def _incomplete_events() -> list[dict]:
    """Eventos sem nenhum jogador com rating → [{event_id, team_slug, team_name}]."""
    client = get_client()

    # match_id (uuid) que JÁ têm rating
    rated: set[str] = set()
    offset = 0
    while True:
        rows = (
            client.table("player_match_stats")
            .select("match_id")
            .not_.is_("rating", "null")
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not rows:
            break
        rated.update(r["match_id"] for r in rows if r.get("match_id"))
        if len(rows) < 1000:
            break
        offset += 1000

    teams = {t["id"]: t for t in client.table("teams").select("id,slug,name").execute().data}

    # matches sem irmao com rating
    matches: list[dict] = []
    offset = 0
    while True:
        rows = (
            client.table("matches")
            .select("id,sofascore_event_id,team_id")
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not rows:
            break
        matches.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000

    out: list[dict] = []
    for m in matches:
        if m["id"] in rated:
            continue
        t = teams.get(m["team_id"])
        if not t or m.get("sofascore_event_id") is None:
            continue
        out.append({
            "event_id": m["sofascore_event_id"],
            "team_slug": t["slug"],
            "team_name": t["name"],
        })
    return out


def _all_events() -> list[dict]:
    """TODOS os eventos (um por sofascore_event_id) → para backfill completo das
    novas features (xG, stats do visitante, ranking). Idempotente."""
    client = get_client()
    teams = {t["id"]: t for t in client.table("teams").select("id,slug,name").execute().data}
    seen: set[int] = set()
    out: list[dict] = []
    offset = 0
    while True:
        rows = (
            client.table("matches")
            .select("sofascore_event_id,team_id")
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not rows:
            break
        for m in rows:
            eid = m.get("sofascore_event_id")
            t = teams.get(m["team_id"])
            if eid is None or eid in seen or not t:
                continue
            seen.add(eid)
            out.append({"event_id": eid, "team_slug": t["slug"], "team_name": t["name"]})
        if len(rows) < 1000:
            break
        offset += 1000
    return out


def _backfilled_event_ids() -> set[int]:
    """Eventos JÁ backfillados nesta etapa — detectados pelo ranking preenchido
    (coluna que o código antigo nunca gravava). Usado para retomar de onde parou."""
    client = get_client()
    done: set[int] = set()
    offset = 0
    while True:
        rows = (
            client.table("matches")
            .select("sofascore_event_id")
            .not_.is_("home_team_ranking", "null")
            .range(offset, offset + 999)
            .execute()
            .data
        )
        if not rows:
            break
        done.update(r["sofascore_event_id"] for r in rows if r.get("sofascore_event_id"))
        if len(rows) < 1000:
            break
        offset += 1000
    return done


def _targets_from_event_ids(ids: list[int]) -> list[dict]:
    """Alvos para event ids específicos.

    Para cada id, gera um alvo por seleção (mandante e visitante) que já tenha
    linha em `matches` — assim o re-fetch preenche as duas perspectivas. Se o
    evento ainda NÃO está em `matches` (ex.: jogo que foi pulado na carga), o
    alvo fica com team_slug=None e as seleções são resolvidas no momento do fetch.
    """
    client = get_client()
    teams = {t["id"]: t for t in client.table("teams").select("id,slug,name").execute().data}
    out: list[dict] = []
    for eid in ids:
        rows = (
            client.table("matches")
            .select("team_id")
            .eq("sofascore_event_id", eid)
            .execute()
            .data
        )
        if rows:
            for r in rows:
                t = teams.get(r["team_id"])
                if t:
                    out.append({"event_id": eid, "team_slug": t["slug"], "team_name": t["name"]})
        else:
            out.append({"event_id": eid, "team_slug": None, "team_name": None})
    return out


def _resolve_perspectives(details: dict, team_by_id: dict) -> list[tuple[str, str]]:
    """(slug, name) das seleções do evento que existem no teams.yaml (mandante/visitante)."""
    ev = details.get("event_raw") or {}
    ev = ev.get("event", ev)
    out: list[tuple[str, str]] = []
    for side in ("homeTeam", "awayTeam"):
        tid = ev.get(side, {}).get("id")
        t = team_by_id.get(tid)
        if t:
            out.append((t.slug, t.name))
    return out


async def run(targets: list[dict], workers: int, team_by_id: dict | None = None) -> None:
    if _HEADLESS:
        logger.warning("PLAYWRIGHT_HEADLESS não está 'false' — SofaScore bloqueia headless.")

    sem = asyncio.Semaphore(workers)
    # Estado compartilhado (mutável p/ os workers)
    st = {"upserted": 0, "rated": 0, "no_rating": 0, "failed": 0,
          "consec_fail": 0, "blocked": False}
    total = len(targets)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=_HEADLESS)
        context = await browser.new_context(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"))

        async def worker(idx: int, tgt: dict) -> None:
            if st["blocked"]:
                return
            async with sem:
                if st["blocked"]:
                    return
                eid = tgt["event_id"]
                page = await context.new_page()
                scr = SofaScoreScraper(page)
                try:
                    # warm-up p/ herdar o token anti-bot
                    await page.goto("https://www.sofascore.com/",
                                    wait_until="domcontentloaded", timeout=60_000)
                    await page.wait_for_timeout(800)
                    details = await scr._fetch_event_details({"id": eid})
                    if not details:
                        st["failed"] += 1
                        st["consec_fail"] += 1
                        logger.warning(f"[{idx}/{total}] Event {eid}: details não capturados "
                                       f"(falhas seguidas: {st['consec_fail']})")
                        if st["consec_fail"] >= BLOCK_THRESHOLD:
                            st["blocked"] = True
                            logger.error(
                                f"{BLOCK_THRESHOLD} falhas seguidas — provável BLOCK do site. "
                                f"Abortando. Progresso salvo (upsert incremental); use o mesmo "
                                f"comando depois para RETOMAR de onde parou."
                            )
                        return

                    # Perspectivas a gravar: a seleção dona da linha, ou — quando
                    # o evento ainda não está em matches — ambas as seleções do
                    # confronto resolvidas pelo teams.yaml.
                    if tgt.get("team_slug"):
                        perspectives = [(tgt["team_slug"], tgt["team_name"])]
                    else:
                        perspectives = _resolve_perspectives(details, team_by_id or {})
                        if not perspectives:
                            st["failed"] += 1
                            logger.warning(f"[{idx}/{total}] Event {eid}: seleções não "
                                           f"encontradas no teams.yaml — pulando")
                            return

                    # UPSERT INCREMENTAL: grava ESTE evento já — se cair/bloquear
                    # depois, nada do que foi processado se perde (é resumível).
                    for slug, name in perspectives:
                        m = Match.model_validate({"team_name": name, **details})
                        await asyncio.to_thread(upsert_matches, slug, [m.model_dump(mode="json")])
                    st["upserted"] += 1
                    st["consec_fail"] = 0

                    lp = (m.lineups.home_players + m.lineups.away_players) if m.lineups else []
                    rated = sum(1 for pl in lp if pl.rating is not None)
                    xg = m.stats.expected_goals if m.stats else None
                    if rated:
                        st["rated"] += 1
                        logger.success(f"[{idx}/{total}] {m.home_team} x {m.away_team}: "
                                       f"{rated} c/rating, xG={xg} [ok={st['upserted']}]")
                    else:
                        st["no_rating"] += 1
                        logger.info(f"[{idx}/{total}] {m.home_team} x {m.away_team}: "
                                    f"sem rating, xG={xg} [ok={st['upserted']}]")
                except Exception as e:
                    st["failed"] += 1
                    st["consec_fail"] += 1
                    logger.error(f"[{idx}/{total}] Event {eid} falhou: {e}")
                    if st["consec_fail"] >= BLOCK_THRESHOLD:
                        st["blocked"] = True
                        logger.error("Muitas falhas seguidas — abortando (resumível).")
                finally:
                    await page.close()
                    await asyncio.sleep(random.uniform(BACKFILL_DELAY_MIN, BACKFILL_DELAY_MAX))

        await asyncio.gather(*(worker(i, t) for i, t in enumerate(targets, start=1)))
        await browser.close()

    status = "ABORTADO (block)" if st["blocked"] else "Concluído"
    logger.success(
        f"{status} — {st['upserted']} gravados ({st['rated']} c/rating, "
        f"{st['no_rating']} sem rating), {st['failed']} falhas, de {total} eventos."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-fetch de stats de jogador por id de evento")
    parser.add_argument("--event", type=int, metavar="ID",
                        help="Re-busca um único evento (debug).")
    parser.add_argument("--events", type=str, metavar="IDS",
                        help="Lista de event ids (separados por vírgula) p/ re-fetch alvo; "
                             "funciona mesmo que ainda não estejam em matches.")
    parser.add_argument("--all-events", action="store_true",
                        help="Backfill: re-busca TODOS os eventos (p/ novas features de time/ranking).")
    parser.add_argument("--workers", type=int, default=2, metavar="N",
                        help="Páginas em paralelo (default 2; use 1 se tomar block)")
    parser.add_argument("--limit", type=int, default=0, metavar="N", help="Limita a N eventos (0 = todos)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    _setup_logging(debug=args.debug)

    team_by_id: dict | None = None

    if args.events:
        ids = [int(x) for x in args.events.split(",") if x.strip()]
        targets = _targets_from_event_ids(ids)
        if any(t["team_slug"] is None for t in targets):
            # resolve seleções de eventos ainda fora de matches
            team_by_id = {t.sofascore_id: t for t in load_teams()}
    elif args.event:
        client = get_client()
        m = (
            client.table("matches")
            .select("sofascore_event_id,team_id")
            .eq("sofascore_event_id", args.event)
            .limit(1)
            .execute()
            .data
        )
        if not m:
            logger.error(f"Evento {args.event} não está em matches.")
            return
        t = client.table("teams").select("slug,name").eq("id", m[0]["team_id"]).single().execute().data
        targets = [{"event_id": args.event, "team_slug": t["slug"], "team_name": t["name"]}]
    elif args.all_events:
        targets = _all_events()
        # RETOMADA: pula eventos já backfillados (ranking preenchido)
        done = _backfilled_event_ids()
        before = len(targets)
        targets = [t for t in targets if t["event_id"] not in done]
        logger.info(f"Backfill: {before} eventos totais, {len(done)} já feitos → "
                    f"{len(targets)} a processar (retomada automática)")
        if args.limit:
            targets = targets[: args.limit]
    else:
        targets = _incomplete_events()
        if args.limit:
            targets = targets[: args.limit]

    if not targets:
        logger.success("Nada a processar — tudo já backfillado.")
        return

    workers = max(1, min(args.workers, len(targets)))
    logger.info(f"Re-fetch por id — {len(targets)} alvo(s), {workers} worker(s)")
    asyncio.run(run(targets, workers, team_by_id))


if __name__ == "__main__":
    main()
