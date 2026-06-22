"""
World Cup Analytics — Monta o DataFrame único (1 linha por partida)

Lê matches + match_stats + player_match_stats do Supabase, agrega os jogadores
ao nível de time-na-partida (mean / max / top-3 / std / soma / por posição,
conforme Docs/02_agregacao_jogadores.md) e exporta um único arquivo.

Uso:
    python pipeline/scripts/build_dataset.py                 # -> output/worldcup_dataset.xlsx
    python pipeline/scripts/build_dataset.py --csv           # também salva .csv

Granularidade do resultado: 1 linha por partida.
- Colunas de time (match_stats) referem-se ao MANDANTE (limitação: o scraper
  salvou só o lado da casa) → prefixo `home_`.
- Agregados de jogadores existem para os dois lados → prefixos `home_` / `away_`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
from loguru import logger

from pipeline.supabase_client import get_client

OUTPUT_DIR = ROOT_DIR / "output"


def _fetch_all(table: str, columns: str = "*") -> list[dict]:
    """Busca todas as linhas de uma tabela (paginado — PostgREST limita a 1000)."""
    client = get_client()
    rows: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        page = (
            client.table(table)
            .select(columns)
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


def _aggregate_players(players: pd.DataFrame) -> pd.DataFrame:
    """Agrega jogadores por (match_id, team_side) → 1 linha por lado."""
    # Considera apenas quem atuou (rating preenchido)
    played = players[players["rating"].notna()].copy()

    def agg_group(g: pd.DataFrame) -> pd.Series:
        r = g["rating"]
        def role_mean(pos: str) -> float:
            return g.loc[g["position"] == pos, "rating"].mean()
        return pd.Series({
            "n_players":        len(g),
            "rating_mean":      r.mean(),
            "rating_max":       r.max(),
            "rating_top3_mean": r.nlargest(3).mean(),
            "rating_std":       r.std(),
            "xg_sum":           g["expected_goals"].sum(min_count=1),
            "xg_max":           g["expected_goals"].max(),
            "xa_sum":           g["expected_assists"].sum(min_count=1),
            "goals_sum":        g["goals"].sum(),
            "assists_sum":      g["assists"].sum(),
            "key_passes_sum":   g["key_passes"].sum(min_count=1),
            "shots_sum":        g["shots_total"].sum(min_count=1),
            # duelos/dribles — vêm dos jogadores (match_stats estava vazio).
            # min_count=1: se nenhum jogador tem o dado, fica NaN (não falso 0).
            "dribbles_won_sum": g["dribbles_won"].sum(min_count=1),
            "aerial_won_sum":   g["aerial_duels_won"].sum(min_count=1),
            "aerial_total_sum": (g["aerial_duels_won"] + g["aerial_duels_lost"]).sum(min_count=1),
            "att_rating_mean":  role_mean("F"),
            "mid_rating_mean":  role_mean("M"),
            "def_rating_mean":  role_mean("D"),
            "gk_rating":        g.loc[g["position"] == "G", "rating"].max(),
        })

    agg = (
        played.groupby(["match_id", "team_side"], group_keys=True)
        .apply(agg_group, include_groups=False)
        .reset_index()
    )

    # Pivot: 1 linha por match_id, colunas home_* / away_*
    wide = agg.pivot(index="match_id", columns="team_side")
    wide.columns = [f"{side}_{feat}" for feat, side in wide.columns]
    return wide.reset_index()


def build(include_players: bool = True) -> pd.DataFrame:
    logger.info("Buscando dados do Supabase...")
    matches = pd.DataFrame(_fetch_all("matches"))
    match_stats = pd.DataFrame(_fetch_all("match_stats"))
    logger.info(f"matches={len(matches)} match_stats={len(match_stats)}")

    # --- match_stats (time mandante) → prefixo home_ ---
    ms = match_stats.drop(columns=["id"]).copy()
    # Recupera pass_accuracy_pct (vinha vazia) a partir de passes certos/total
    ms["pass_accuracy_pct"] = (ms["passes_accurate"] / ms["passes_total"] * 100).round(1)
    # Remove colunas 100% vazias sem fonte para preencher:
    # - long_balls_accurate, ground_duels_* : não há equivalente nos jogadores
    # - dribbles_succeeded, aerial_duels_*  : substituídas por agregados de jogadores
    #   (dribbles_won_sum, aerial_won_sum, aerial_total_sum) dos DOIS lados
    EMPTY_DROP = [
        "long_balls_accurate", "ground_duels_total", "ground_duels_won",
        "dribbles_succeeded", "aerial_duels_total", "aerial_duels_won",
    ]
    ms = ms.drop(columns=[c for c in EMPTY_DROP if c in ms.columns])
    ms = ms.rename(columns={c: f"home_{c}" for c in ms.columns if c != "match_id"})

    # --- montagem do df (1 linha por partida) ---
    keep = [
        "id", "sofascore_event_id", "match_date", "competition", "season",
        "round_number", "home_team", "away_team", "venue", "venue_city",
        "attendance", "referee", "score_home", "score_away",
        "score_home_ht", "score_away_ht", "result",
    ]
    df = matches[keep].rename(columns={"id": "match_id"})
    df = df.merge(ms, on="match_id", how="left")

    if include_players:
        logger.info("Agregando jogadores por (match_id, team_side)...")
        players = pd.DataFrame(_fetch_all("player_match_stats"))
        logger.info(f"player_match_stats={len(players)}")
        player_feats = _aggregate_players(players)
        df = df.merge(player_feats, on="match_id", how="left")

    # match_id interno (uuid) fora; sofascore_event_id é a chave pública
    df = df.drop(columns=["match_id"])
    df = df.sort_values("match_date").reset_index(drop=True)
    logger.success(f"DataFrame final: {df.shape[0]} partidas × {df.shape[1]} colunas")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Monta o dataset único de partidas")
    parser.add_argument("--csv", action="store_true", help="Também salvar .csv")
    parser.add_argument(
        "--matches-only", action="store_true",
        help="Gerar só matches + match_stats (sem agregados de jogadores)",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df = build(include_players=not args.matches_only)

    stem = "worldcup_matches" if args.matches_only else "worldcup_dataset"
    sheet = "matches"

    xlsx_path = OUTPUT_DIR / f"{stem}.xlsx"
    df.to_excel(xlsx_path, index=False, sheet_name=sheet)
    logger.success(f"Salvo → {xlsx_path}")

    if args.csv:
        csv_path = OUTPUT_DIR / f"{stem}.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.success(f"Salvo → {csv_path}")


if __name__ == "__main__":
    main()
