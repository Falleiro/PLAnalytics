"""
World Cup Analytics — Scrape + Load (execução manual, concorrente)

Faz o scraping do SofaScore para uma ou todas as seleções e carrega os dados
no Supabase (matches + match_stats + player_match_stats). Também salva o JSON
bruto em output/ como backup.

Otimizações:
- Concorrência: processa N seleções em paralelo (--workers, default 2).
  Benchmark nesta máquina (Core 7 150U, 4 seleções × 6 jogos):
  workers=1 → 2m45s | workers=2 → 1m50s | workers=4 → 2m46s.
  workers=2 é o ponto ótimo; acima disso a CPU satura e o ganho some.
- Deduplicação: um set global de event_id evita raspar o mesmo jogo duas vezes
  (ex.: Brasil×Argentina aparece nas listas das duas seleções).
- Upsert no Supabase roda em thread (não bloqueia o event loop).

IMPORTANTE: o SofaScore bloqueia modo headless. Rode com:
    PLAYWRIGHT_HEADLESS=false python pipeline/scripts/scrape_and_load.py --team Brazil
    PLAYWRIGHT_HEADLESS=false python pipeline/scripts/scrape_and_load.py --all --last 20

No Windows (PowerShell):
    $env:PLAYWRIGHT_HEADLESS="false"; python pipeline/scripts/scrape_and_load.py --all --workers 4
"""

from __future__ import annotations

import argparse
import asyncio
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
    find_team,
    load_teams,
    save_output,
)
from scraper.models import TeamConfig
from pipeline.supabase_client import get_client, upsert_matches


def _existing_event_ids() -> set[int]:
    """Todos os sofascore_event_id já presentes em matches (paginado)."""
    client = get_client()
    ids: set[int] = set()
    page_size = 1000
    offset = 0
    while True:
        rows = (
            client.table("matches")
            .select("sofascore_event_id")
            .range(offset, offset + page_size - 1)
            .execute()
            .data
        )
        if not rows:
            break
        ids.update(r["sofascore_event_id"] for r in rows if r.get("sofascore_event_id"))
        if len(rows) < page_size:
            break
        offset += page_size
    return ids


def _rated_match_uuids() -> set[str]:
    """match_id (uuid) de partidas que já têm ≥1 jogador com rating preenchido."""
    client = get_client()
    uuids: set[str] = set()
    page_size = 1000
    offset = 0
    while True:
        rows = (
            client.table("player_match_stats")
            .select("match_id")
            .not_.is_("rating", "null")
            .range(offset, offset + page_size - 1)
            .execute()
            .data
        )
        if not rows:
            break
        uuids.update(r["match_id"] for r in rows if r.get("match_id"))
        if len(rows) < page_size:
            break
        offset += page_size
    return uuids


def _refetch_targets(teams: list[TeamConfig]) -> tuple[set[int], list[TeamConfig]]:
    """Para o modo --refetch-missing.

    Retorna:
      - complete_event_ids: sofascore_event_id que JÁ têm stats de jogador (com
        rating). Pré-carregados no dedup → o scraper PULA esses e re-busca só os
        que faltam.
      - teams_to_run: seleções que possuem ≥1 evento incompleto (sem rating),
        para não navegar páginas de times que já estão 100% completos.
    """
    client = get_client()
    rated_uuids = _rated_match_uuids()

    # matches: id(uuid) → (event_id, team_id)
    matches = _fetch_matches_min()
    slug_by_team_id = {t["id"]: t["slug"] for t in client.table("teams").select("id,slug").execute().data}

    complete_event_ids: set[int] = set()
    incomplete_team_slugs: set[str] = set()
    for m in matches:
        eid = m.get("sofascore_event_id")
        if eid is None:
            continue
        if m["id"] in rated_uuids:
            complete_event_ids.add(eid)
        else:
            slug = slug_by_team_id.get(m.get("team_id"))
            if slug:
                incomplete_team_slugs.add(slug)

    teams_to_run = [t for t in teams if t.slug in incomplete_team_slugs]
    return complete_event_ids, teams_to_run


def _fetch_matches_min() -> list[dict]:
    """Todas as linhas de matches (id, sofascore_event_id, team_id), paginado."""
    client = get_client()
    rows: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        page = (
            client.table("matches")
            .select("id,sofascore_event_id,team_id")
            .range(offset, offset + page_size - 1)
            .execute()
            .data
        )
        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def _teams_without_matches(teams: list[TeamConfig]) -> list[TeamConfig]:
    """Seleções que ainda não têm nenhuma linha em matches (team_id)."""
    client = get_client()
    db_teams = client.table("teams").select("id,slug").execute().data
    slug_by_id = {t["id"]: t["slug"] for t in db_teams}
    rows = client.table("matches").select("team_id").execute().data
    slugs_with_matches = {slug_by_id.get(r["team_id"]) for r in rows}
    return [t for t in teams if t.slug not in slugs_with_matches]


async def run(
    teams: list[TeamConfig],
    last_n: int,
    workers: int,
    seen_preload: set[int] | None = None,
) -> None:
    if _HEADLESS:
        logger.warning(
            "PLAYWRIGHT_HEADLESS não está 'false' — SofaScore bloqueia headless. "
            "Rode com PLAYWRIGHT_HEADLESS=false."
        )

    # Pré-carrega o dedup. Default: tudo que já está no banco (reexecuções pulam
    # jogos já coletados). No modo --refetch-missing, recebe só os eventos JÁ
    # COMPLETOS → os incompletos NÃO estão no set e portanto são re-buscados.
    seen_event_ids: set[int] = seen_preload if seen_preload is not None else _existing_event_ids()
    if seen_event_ids:
        logger.info(f"Dedup pré-carregado com {len(seen_event_ids)} jogos (serão pulados)")
    sem = asyncio.Semaphore(workers)
    totals: dict[str, int] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=_HEADLESS)
        context = await browser.new_context()

        async def worker(idx: int, team: TeamConfig) -> None:
            async with sem:
                logger.info(f"=== [{idx}/{len(teams)}] {team.name} (iniciando) ===")
                page = await context.new_page()
                scraper = SofaScoreScraper(page)
                try:
                    result = await scraper.scrape_team(
                        team, last_n, seen_event_ids=seen_event_ids
                    )
                    save_output(result)
                    matches = [m.model_dump(mode="json") for m in result.matches]
                    if matches:
                        # upsert é bloqueante (HTTP) → roda em thread
                        await asyncio.to_thread(upsert_matches, team.slug, matches)
                    totals[team.slug] = len(matches)
                    logger.success(f"[{team.name}] {len(matches)} jogos carregados")
                except Exception as e:
                    logger.error(f"[{team.name}] falhou — pulando: {e}")
                    totals[team.slug] = 0
                finally:
                    await page.close()

        await asyncio.gather(
            *(worker(i, t) for i, t in enumerate(teams, start=1))
        )
        await browser.close()

    loaded = sum(totals.values())
    logger.success(
        f"Concluído — {loaded} jogos carregados de {len(teams)} seleções "
        f"({len(seen_event_ids)} eventos únicos)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="World Cup Analytics — scrape SofaScore e carrega no Supabase",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--team", type=str, metavar="NAME", help="Seleção, ex: Brazil")
    group.add_argument("--all", action="store_true", help="Todas as seleções do teams.yaml")
    group.add_argument(
        "--missing", action="store_true",
        help="Só as seleções que ainda não têm jogos no banco (recuperar gaps)",
    )
    group.add_argument(
        "--refetch-missing", action="store_true",
        help="Re-busca só os eventos que estão SEM stats de jogador (rating). "
             "Pula os já completos. Use --last alto (ex.: 50) p/ alcançar jogos antigos.",
    )
    parser.add_argument(
        "--last", type=int, default=30, metavar="N",
        help="Número de jogos por seleção (default: 30)",
    )
    parser.add_argument(
        "--workers", type=int, default=2, metavar="N",
        help="Seleções processadas em paralelo (default: 2 — ponto ótimo nesta máquina)",
    )
    parser.add_argument("--debug", action="store_true", help="Logging DEBUG")
    args = parser.parse_args()

    _setup_logging(debug=args.debug)
    teams = load_teams()

    seen_preload: set[int] | None = None
    if args.team:
        selected = [find_team(args.team, teams)]
    elif args.missing:
        selected = _teams_without_matches(teams)
        if not selected:
            logger.success("Nenhuma seleção faltando — base completa.")
            return
        logger.info(f"Seleções faltando: {', '.join(t.name for t in selected)}")
    elif args.refetch_missing:
        seen_preload, selected = _refetch_targets(teams)
        if not selected:
            logger.success("Nenhum evento sem stats de jogador — base completa.")
            return
        logger.info(
            f"Re-fetch de eventos incompletos — {len(selected)} seleção(ões) "
            f"afetada(s); {len(seen_preload)} eventos já completos serão pulados"
        )
    else:
        selected = teams

    workers = max(1, min(args.workers, len(selected)))
    logger.info(
        f"Carregando {len(selected)} seleção(ões) — últimos {args.last} jogos "
        f"— {workers} worker(s) em paralelo"
    )
    asyncio.run(run(selected, args.last, workers, seen_preload=seen_preload))


if __name__ == "__main__":
    main()
