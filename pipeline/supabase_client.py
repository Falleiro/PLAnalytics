"""
Supabase integration for Premier League Analytics pipeline.

Functions:
    get_client()             → authenticated Supabase client
    upsert_teams(teams)      → insert/update teams table
    upsert_matches(matches)  → insert/update matches + match_stats + player_match_stats
    upload_raw_json(...)     → upload raw JSON to Storage bucket raw-data
    log_pipeline_run(...)    → insert row in pipeline_runs (returns run_id)
    update_pipeline_run(...) → update status/finished_at of an existing run
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger
from supabase import Client, create_client

# Load .env from the project root (one level above pipeline/)
_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_PATH)


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def get_client() -> Client:
    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ["SUPABASE_SERVICE_KEY"].strip()
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

def upsert_teams(teams: list[dict]) -> None:
    """
    Insert or update rows in public.teams.

    Expected dict keys: name, slug, primary_color, secondary_color
    """
    client = get_client()
    rows = [
        {
            "name": t["name"],
            "slug": t["slug"],
            "primary_color": t.get("primary_color", "#1B3A6B"),
            "secondary_color": t.get("secondary_color", "#FFFFFF"),
        }
        for t in teams
    ]
    client.table("teams").upsert(rows, on_conflict="slug").execute()
    logger.info(f"Upserted {len(rows)} teams")


# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------

def upsert_matches(team_slug: str, matches: list[dict]) -> None:
    """
    Insert or update rows in public.matches and public.match_stats.

    Expects each dict to be the JSON-serialized form of a Match model,
    i.e. the output of TeamScrapeResult.model_dump().
    """
    client = get_client()

    # Resolve team_id from slug
    result = client.table("teams").select("id").eq("slug", team_slug).single().execute()
    team_id: int = result.data["id"]

    if not matches:
        logger.warning(f"[{team_slug}] No matches to upsert — skipping")
        return

    match_rows: list[dict[str, Any]] = []
    stats_map: dict[str, dict] = {}  # sofascore_event_id → stats dict

    for m in matches:
        match_row = {
            "team_id": team_id,
            "sofascore_event_id": m["sofascore_event_id"],
            "match_date": m["match_date"],
            "home_team": m["home_team"],
            "away_team": m["away_team"],
            "score_home": m["score_home"],
            "score_away": m["score_away"],
            "competition": m["competition"],
            "season": m.get("season"),
            "round_number": m.get("round_number"),
            "venue": m.get("venue"),
            "venue_city": m.get("venue_city"),
            "attendance": m.get("attendance"),
            "referee": m.get("referee"),
            "result": m["result"],
        }
        match_rows.append(match_row)

        if m.get("stats"):
            stats_map[m["sofascore_event_id"]] = m["stats"]

    # Upsert matches (unique on team_id + sofascore_event_id)
    client.table("matches").upsert(match_rows, on_conflict="team_id,sofascore_event_id").execute()
    logger.info(f"[{team_slug}] Upserted {len(match_rows)} matches")

    # Build sofascore_event_id → match UUID by querying the DB (handles both insert and update)
    # Filter by team_id too — same event_id exists once per team after constraint fix
    event_ids = [m["sofascore_event_id"] for m in matches]
    fetched = (
        client.table("matches")
        .select("id,sofascore_event_id")
        .eq("team_id", team_id)
        .in_("sofascore_event_id", event_ids)
        .execute()
    )
    match_id_map: dict[int, str] = {
        r["sofascore_event_id"]: r["id"] for r in fetched.data
    }

    # Upsert match_stats linked to the inserted matches
    if stats_map:
        stats_rows = []
        for event_id, stats in stats_map.items():
            match_uuid = match_id_map.get(event_id)
            if match_uuid is None:
                continue
            stats_row = {"match_id": match_uuid, **stats}
            stats_rows.append(stats_row)

        if stats_rows:
            client.table("match_stats").upsert(stats_rows, on_conflict="match_id").execute()
            logger.info(f"[{team_slug}] Upserted {len(stats_rows)} match_stats rows")

    # Upsert player_match_stats from lineups data
    player_rows: list[dict[str, Any]] = []
    for m in matches:
        match_uuid = match_id_map.get(m["sofascore_event_id"])
        if match_uuid is None:
            continue
        lineups = m.get("lineups") or {}
        for side, players in [("home", lineups.get("home_players", [])), ("away", lineups.get("away_players", []))]:
            for p in players:
                player_rows.append({
                    "match_id":            match_uuid,
                    "sofascore_player_id": p["player_id"],
                    "player_name":         p["player_name"],
                    "team_side":           side,
                    "is_starter":          p.get("is_starter", False),
                    "captain":             p.get("captain", False),
                    "shirt_number":        p.get("shirt_number"),
                    "position":            p.get("position"),
                    "minutes_played":      p.get("minutes_played"),
                    "rating":              p.get("rating"),
                    "goals":               p.get("goals"),
                    "assists":             p.get("assists"),
                    "shots_total":         p.get("shots_total"),
                    "shots_on_target":     p.get("shots_on_target"),
                    "passes_total":        p.get("passes_total"),
                    "passes_accurate":     p.get("passes_accurate"),
                    "key_passes":          p.get("key_passes"),
                    "tackles":             p.get("tackles"),
                    "interceptions":       p.get("interceptions"),
                    "dribbles_won":        p.get("dribbles_won"),
                    "aerial_duels_won":    p.get("aerial_duels_won"),
                    "aerial_duels_lost":   p.get("aerial_duels_lost"),
                    "fouls_committed":     p.get("fouls_committed"),
                    "yellow_cards":        p.get("yellow_cards"),
                    "red_cards":           p.get("red_cards"),
                    "saves":               p.get("saves"),
                    "expected_goals":      p.get("expected_goals"),
                    "expected_assists":    p.get("expected_assists"),
                })

    if player_rows:
        client.table("player_match_stats").upsert(
            player_rows, on_conflict="match_id,sofascore_player_id"
        ).execute()
        logger.info(f"[{team_slug}] Upserted {len(player_rows)} player_match_stats rows")


# ---------------------------------------------------------------------------
# Storage — raw JSON
# ---------------------------------------------------------------------------

def upload_raw_json(team_slug: str, scraped_at: datetime, content: bytes) -> None:
    """Upload raw scraper JSON to the 'raw-data' Supabase Storage bucket."""
    client = get_client()
    date_str = scraped_at.strftime("%Y-%m-%d_%H-%M")
    path = f"sofascore/{team_slug}/{date_str}.json"
    client.storage.from_("raw-data").upload(
        path, content, {"upsert": "true", "content-type": "application/json"}
    )
    logger.info(f"Uploaded raw JSON → raw-data/{path}")


# ---------------------------------------------------------------------------
# Pipeline run log
# ---------------------------------------------------------------------------

def log_pipeline_run(
    dag_id: str,
    status: str,
    details: dict | None = None,
) -> str:
    """
    Insert a new row in public.pipeline_runs.

    Returns the UUID of the created row (used to update it later).
    """
    client = get_client()
    result = (
        client.table("pipeline_runs")
        .insert({
            "dag_id": dag_id,
            "status": status,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "details": details or {},
        })
        .execute()
    )
    run_id: str = result.data[0]["id"]
    logger.info(f"Logged pipeline run — dag={dag_id} status={status} id={run_id}")
    return run_id


def update_pipeline_run(
    run_id: str,
    status: str,
    details: dict | None = None,
) -> None:
    """Update status and finished_at of an existing pipeline_runs row."""
    client = get_client()
    client.table("pipeline_runs").update({
        "status": status,
        "finished_at": datetime.now(tz=timezone.utc).isoformat(),
        "details": details or {},
    }).eq("id", run_id).execute()
    logger.info(f"Updated pipeline run {run_id} → {status}")
