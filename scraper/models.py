"""
Pydantic v2 models for World Cup Analytics scraper.

Hierarchy:
    TeamScrapeResult
      └── team: TeamConfig
      └── matches: list[Match]
            ├── stats: MatchStats      (from /event/{id}/statistics)
            ├── incidents: list[Incident]  (from /event/{id}/incidents)
            └── lineups: MatchLineups  (from /event/{id}/lineups)

All models use extra="ignore" so unknown SofaScore fields are silently dropped
instead of raising ValidationError — SofaScore's API evolves and we should be
resilient to new or renamed keys.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Config / team reference
# ---------------------------------------------------------------------------

class TeamConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    slug: str
    sofascore_id: int
    primary_color: str = "#1B3A6B"
    secondary_color: str = "#FFFFFF"


# ---------------------------------------------------------------------------
# Lineups
# ---------------------------------------------------------------------------

class Player(BaseModel):
    model_config = ConfigDict(extra="ignore")

    player_id: int
    player_name: str
    shirt_number: Optional[int] = None
    position: Optional[str] = None      # "G", "D", "M", "F"
    is_starter: bool = False
    captain: bool = False

    # SofaScore rating (0-10)
    rating: Optional[float] = None

    # Per-match statistics (from player.statistics in /lineups response)
    minutes_played: Optional[int] = None
    goals: Optional[int] = None
    assists: Optional[int] = None
    shots_total: Optional[int] = None
    shots_on_target: Optional[int] = None
    passes_total: Optional[int] = None
    passes_accurate: Optional[int] = None
    key_passes: Optional[int] = None
    tackles: Optional[int] = None
    interceptions: Optional[int] = None
    dribbles_won: Optional[int] = None
    aerial_duels_won: Optional[int] = None
    aerial_duels_lost: Optional[int] = None
    fouls_committed: Optional[int] = None
    yellow_cards: Optional[int] = None
    red_cards: Optional[int] = None
    saves: Optional[int] = None
    expected_goals: Optional[float] = None
    expected_assists: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _parse_sofascore_player(cls, v: Any) -> Any:
        """Accept SofaScore player objects (nested player/statistics dicts)."""
        if not isinstance(v, dict):
            return v
        # Already flat (from JSON deserialization) — pass through
        if "player_id" in v:
            return v
        player_info = v.get("player", v)
        stats = v.get("statistics", {}) or {}
        return {
            "player_id": player_info.get("id", 0),
            "player_name": player_info.get("name", ""),
            "shirt_number": v.get("shirtNumber") or player_info.get("shirtNumber"),
            "position": v.get("position") or player_info.get("position"),
            "is_starter": v.get("substitute") is False or v.get("isStarter", False),
            "captain": v.get("captain", False),
            "rating":            stats.get("rating"),
            "minutes_played":    stats.get("minutesPlayed"),
            "goals":             stats.get("goals"),
            "assists":           stats.get("assists"),
            "shots_total":       stats.get("totalShots"),
            "shots_on_target":   stats.get("onTargetScoringAttempt"),
            "passes_total":      stats.get("totalPass"),
            "passes_accurate":   stats.get("accuratePass"),
            "key_passes":        stats.get("keyPass"),
            "tackles":           stats.get("totalTackle"),
            "interceptions":     stats.get("totalInterceptionWon"),
            "dribbles_won":      stats.get("wonContest"),
            "aerial_duels_won":  stats.get("aerialWon"),
            "aerial_duels_lost": stats.get("aerialLost"),
            "fouls_committed":   stats.get("foulsCommited"),
            "yellow_cards":      stats.get("yellowCard"),
            "red_cards":         stats.get("redCard"),
            "saves":             stats.get("saves"),
            "expected_goals":    stats.get("expectedGoals"),
            "expected_assists":  stats.get("expectedAssists"),
        }


class MatchLineups(BaseModel):
    model_config = ConfigDict(extra="ignore")

    home_formation: Optional[str] = None
    away_formation: Optional[str] = None
    home_players: list[Player] = Field(default_factory=list)
    away_players: list[Player] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _parse_sofascore_lineups(cls, v: Any) -> Any:
        """Parse the SofaScore /lineups response into flat structure."""
        if not isinstance(v, dict):
            return v
        # Already flat (from JSON deserialization) — pass through
        if "home_players" in v or "away_players" in v:
            return v
        home = v.get("home", {}) or {}
        away = v.get("away", {}) or {}
        return {
            "home_formation": home.get("formation"),
            "away_formation": away.get("formation"),
            "home_players": home.get("players", []),
            "away_players": away.get("players", []),
        }


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

IncidentType = Literal["goal", "card", "substitution", "var", "period", "injury_time", "other"]
CardType = Literal["yellow", "red", "yellow_red"]
GoalType = Literal["regular", "penalty", "own_goal", "free_kick"]


class Incident(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = "other"
    minute: Optional[int] = None
    extra_minute: Optional[int] = None    # e.g. 90+3 → minute=90, extra=3
    team_side: Optional[Literal["home", "away"]] = None

    # Goal fields
    scorer_name: Optional[str] = None
    scorer_id: Optional[int] = None
    assist_name: Optional[str] = None
    assist_id: Optional[int] = None
    goal_type: Optional[GoalType] = None

    # Card fields
    player_name: Optional[str] = None
    player_id: Optional[int] = None
    card_type: Optional[CardType] = None

    # Substitution fields
    player_in_name: Optional[str] = None
    player_in_id: Optional[int] = None
    player_out_name: Optional[str] = None
    player_out_id: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _parse_sofascore_incident(cls, v: Any) -> Any:
        """Normalize the SofaScore incident object into our flat model."""
        if not isinstance(v, dict):
            return v

        incident_type = v.get("incidentType", "other")
        is_home = v.get("isHome")
        team_side = ("home" if is_home else "away") if is_home is not None else None

        base: dict[str, Any] = {
            "type": incident_type,
            "minute": v.get("time"),
            "extra_minute": v.get("addedTime"),
            "team_side": team_side,
        }

        if incident_type == "goal":
            raw_type = v.get("incidentClass", "regular")
            type_map = {
                "regular": "regular",
                "penalty": "penalty",
                "ownGoal": "own_goal",
                "freekick": "free_kick",
            }
            scorer = v.get("player") or {}
            assist = v.get("assist1") or {}
            base.update({
                "goal_type": type_map.get(raw_type, "regular"),
                "scorer_name": scorer.get("name"),
                "scorer_id": scorer.get("id"),
                "assist_name": assist.get("name"),
                "assist_id": assist.get("id"),
            })

        elif incident_type == "card":
            raw_card = v.get("incidentClass", "yellow")
            card_map = {
                "yellow": "yellow",
                "red": "red",
                "yellowRed": "yellow_red",
            }
            player = v.get("player") or {}
            base.update({
                "card_type": card_map.get(raw_card, "yellow"),
                "player_name": player.get("name"),
                "player_id": player.get("id"),
            })

        elif incident_type == "substitution":
            player_in = v.get("playerIn") or {}
            player_out = v.get("playerOut") or {}
            base.update({
                "player_in_name": player_in.get("name"),
                "player_in_id": player_in.get("id"),
                "player_out_name": player_out.get("name"),
                "player_out_id": player_out.get("id"),
            })

        return base


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

# Maps SofaScore statisticsItem names to our model field names.
# Source: SofaScore /event/{id}/statistics response (period="ALL")
_STATS_KEY_MAP: dict[str, str] = {
    # Possession
    "Ball possession": "possession_pct",
    # Shooting
    "Total shots": "shots_total",
    "Shots on target": "shots_on_target",
    "Shots off target": "shots_off_target",
    "Blocked shots": "shots_blocked",
    "Big chances": "big_chances",
    "Big chances missed": "big_chances_missed",
    # Passing
    "Passes": "passes_total",
    "Accurate passes": "passes_accurate",
    "Accurate passes %": "pass_accuracy_pct",
    "Long balls": "long_balls_total",
    "Accurate long balls": "long_balls_accurate",
    # Defending
    "Tackles": "tackles",
    "Interceptions": "interceptions",
    "Clearances": "clearances",
    "Goalkeeper saves": "goalkeeper_saves",
    # Duels
    "Dribbles": "dribbles_attempted",
    "Dribbles succeeded": "dribbles_succeeded",
    "Total duels": "ground_duels_total",
    "Duels won": "ground_duels_won",
    "Aerials won": "aerial_duels_won",
    "Total aerial duels": "aerial_duels_total",
    # Set pieces & fouls
    "Corner kicks": "corners",
    "Free kicks": "free_kicks",
    "Goal kicks": "goal_kicks",
    "Throw-ins": "throw_ins",
    "Offsides": "offsides",
    "Fouls": "fouls",
    "Yellow cards": "yellow_cards",
    "Red cards": "red_cards",
}

NonNegInt = Annotated[int, Field(ge=0)]
NonNegFloat = Annotated[float, Field(ge=0)]


class MatchStats(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Possession
    possession_pct: Optional[NonNegFloat] = None
    # Shooting
    shots_total: Optional[NonNegInt] = None
    shots_on_target: Optional[NonNegInt] = None
    shots_off_target: Optional[NonNegInt] = None
    shots_blocked: Optional[NonNegInt] = None
    big_chances: Optional[NonNegInt] = None
    big_chances_missed: Optional[NonNegInt] = None
    # Passing
    passes_total: Optional[NonNegInt] = None
    passes_accurate: Optional[NonNegInt] = None
    pass_accuracy_pct: Optional[NonNegFloat] = None
    long_balls_total: Optional[NonNegInt] = None
    long_balls_accurate: Optional[NonNegInt] = None
    # Defending
    tackles: Optional[NonNegInt] = None
    interceptions: Optional[NonNegInt] = None
    clearances: Optional[NonNegInt] = None
    goalkeeper_saves: Optional[NonNegInt] = None
    # Duels
    dribbles_attempted: Optional[NonNegInt] = None
    dribbles_succeeded: Optional[NonNegInt] = None
    ground_duels_total: Optional[NonNegInt] = None
    ground_duels_won: Optional[NonNegInt] = None
    aerial_duels_total: Optional[NonNegInt] = None
    aerial_duels_won: Optional[NonNegInt] = None
    # Set pieces & fouls
    corners: Optional[NonNegInt] = None
    free_kicks: Optional[NonNegInt] = None
    goal_kicks: Optional[NonNegInt] = None
    throw_ins: Optional[NonNegInt] = None
    offsides: Optional[NonNegInt] = None
    fouls: Optional[NonNegInt] = None
    yellow_cards: Optional[NonNegInt] = None
    red_cards: Optional[NonNegInt] = None

    @model_validator(mode="before")
    @classmethod
    def _parse_sofascore_statistics(cls, v: Any) -> Any:
        """
        Accept either:
        - A raw SofaScore /statistics response dict (has a "statistics" list)
        - An already-flat dict (used in tests or direct construction)

        SofaScore structure:
        {
          "statistics": [
            {
              "period": "ALL",
              "groups": [
                {
                  "groupName": "...",
                  "statisticsItems": [
                    {"name": "Ball possession", "home": "55%", "homeValue": 55,
                     "away": "45%", "awayValue": 45, "compareCode": 1}
                  ]
                }
              ]
            }
          ]
        }
        The "home" key contains the stats for the home team (display string).
        We store the value for BOTH sides and let Match decide which side to use.
        """
        if not isinstance(v, dict):
            return v

        raw_periods = v.get("statistics")
        if raw_periods is None:
            # Already flat — pass through
            return v

        # Find the "ALL" period (full match), fall back to first period available
        all_period = next(
            (p for p in raw_periods if p.get("period") == "ALL"),
            raw_periods[0] if raw_periods else None,
        )
        if all_period is None:
            return {}

        flat: dict[str, Any] = {}
        for group in all_period.get("groups", []):
            for item in group.get("statisticsItems", []):
                name = item.get("name", "")
                field = _STATS_KEY_MAP.get(name)
                if field is None:
                    # Unknown stat — log at debug so we can extend the map later
                    logger.debug(f"Unknown statistics key: '{name}'")
                    continue

                # SofaScore stores percentage values as strings ("55%") or raw ints.
                # homeValue / awayValue are the numeric form when present.
                home_val = item.get("homeValue")
                if home_val is None:
                    raw_str = item.get("home", "")
                    home_val = _parse_stat_value(raw_str)

                flat[field] = home_val

        return flat


def _parse_stat_value(raw: Any) -> Optional[float]:
    """Convert SofaScore stat strings like '55%', '1/5', '231' to float/int."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return raw
    s = str(raw).strip()
    # Remove % sign
    s = s.replace("%", "")
    # Handle fractions like "12/18"
    if "/" in s:
        parts = s.split("/")
        try:
            return float(parts[0])
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Match (fact record)
# ---------------------------------------------------------------------------

class Match(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Identifiers
    sofascore_event_id: int
    team_name: str                  # which team we are tracking

    # Match info
    match_date: datetime
    home_team: str
    away_team: str
    home_team_id: int
    away_team_id: int
    score_home: Annotated[int, Field(ge=0)]
    score_away: Annotated[int, Field(ge=0)]
    score_home_ht: Optional[NonNegInt] = None
    score_away_ht: Optional[NonNegInt] = None

    # Context
    competition: str
    season: Optional[str] = None
    round_number: Optional[int] = None
    venue: Optional[str] = None
    venue_city: Optional[str] = None
    attendance: Optional[int] = None
    referee: Optional[str] = None

    # Result from the tracked team's perspective
    result: Literal["W", "D", "L"]

    # Related data
    stats: Optional[MatchStats] = None
    incidents: list[Incident] = Field(default_factory=list)
    lineups: Optional[MatchLineups] = None

    @model_validator(mode="before")
    @classmethod
    def _parse_sofascore_event(cls, v: Any) -> Any:
        """
        Accept a raw dict with the following keys from the scraper:
          - event_raw: /event/{id} response
          - stats_raw: /statistics response (optional)
          - incidents_raw: /incidents response (optional)
          - lineups_raw: /lineups response (optional)
          - team_name: str

        OR an already-flat dict (for tests).
        """
        if not isinstance(v, dict):
            return v
        if "event_raw" not in v:
            # Already flat — pass through
            return v

        event = v["event_raw"].get("event", v["event_raw"])
        team_name = v.get("team_name", "")

        home_team = event.get("homeTeam", {})
        away_team = event.get("awayTeam", {})
        home_score_obj = event.get("homeScore", {})
        away_score_obj = event.get("awayScore", {})

        score_home = home_score_obj.get("current", 0)
        score_away = away_score_obj.get("current", 0)

        # Half-time scores are in period1
        score_home_ht = home_score_obj.get("period1")
        score_away_ht = away_score_obj.get("period1")

        # timestamp → UTC datetime
        ts = event.get("startTimestamp", 0)
        match_date = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(tz=timezone.utc)

        tournament = event.get("tournament", {})
        season_obj = event.get("season", {})
        round_info = event.get("roundInfo", {})
        venue_obj = event.get("venue", {})
        referee_obj = event.get("referee", {})

        # Auto-compute result from the team's perspective
        tracked_is_home = home_team.get("name", "").lower() == team_name.lower() or \
                          home_team.get("slug", "").lower() == team_name.lower()
        result = _compute_result(score_home, score_away, tracked_is_home)

        flat: dict[str, Any] = {
            "sofascore_event_id": event.get("id"),
            "team_name": team_name,
            "match_date": match_date,
            "home_team": home_team.get("name", ""),
            "away_team": away_team.get("name", ""),
            "home_team_id": home_team.get("id", 0),
            "away_team_id": away_team.get("id", 0),
            "score_home": score_home,
            "score_away": score_away,
            "score_home_ht": score_home_ht,
            "score_away_ht": score_away_ht,
            "competition": tournament.get("name", ""),
            "season": season_obj.get("name"),
            "round_number": round_info.get("round"),
            "venue": venue_obj.get("name") if venue_obj else None,
            "venue_city": venue_obj.get("city", {}).get("name") if venue_obj else None,
            "attendance": event.get("attendance"),
            "referee": referee_obj.get("name") if referee_obj else None,
            "result": result,
        }

        # Attach sub-endpoint data
        if "stats_raw" in v and v["stats_raw"]:
            flat["stats"] = v["stats_raw"]
        if "incidents_raw" in v and v["incidents_raw"]:
            flat["incidents"] = v["incidents_raw"].get("incidents", [])
        if "lineups_raw" in v and v["lineups_raw"]:
            flat["lineups"] = v["lineups_raw"]

        return flat

    @field_validator("result", mode="before")
    @classmethod
    def _validate_result(cls, v: Any) -> Any:
        if isinstance(v, str) and v.upper() in {"W", "D", "L"}:
            return v.upper()
        return v


def _compute_result(
    score_home: int,
    score_away: int,
    tracked_is_home: bool,
) -> Literal["W", "D", "L"]:
    if score_home == score_away:
        return "D"
    home_won = score_home > score_away
    if tracked_is_home:
        return "W" if home_won else "L"
    else:
        return "L" if home_won else "W"


# ---------------------------------------------------------------------------
# Output root
# ---------------------------------------------------------------------------

class TeamScrapeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    team: TeamConfig
    scraped_at: datetime
    matches: list[Match] = Field(default_factory=list)
