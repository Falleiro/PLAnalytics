"""
Unit tests for scraper/models.py.

These tests use inline fixture dicts that mirror the SofaScore API JSON shape.
After running capture_fixtures.py, you can also load real fixtures from
scraper/tests/fixtures/*.json for more realistic validation.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from scraper.models import (
    Incident,
    Match,
    MatchLineups,
    MatchStats,
    Player,
    TeamConfig,
    TeamScrapeResult,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Inline fixture helpers
# ---------------------------------------------------------------------------

def _make_event_raw(
    event_id: int = 1001,
    home_name: str = "Arsenal",
    away_name: str = "Chelsea",
    home_id: int = 42,
    away_id: int = 38,
    score_home: int = 2,
    score_away: int = 1,
    score_home_ht: int = 1,
    score_away_ht: int = 0,
    tournament_id: int = 17,
) -> dict:
    return {
        "event": {
            "id": event_id,
            "startTimestamp": 1700000000,
            "homeTeam": {"id": home_id, "name": home_name, "slug": home_name.lower()},
            "awayTeam": {"id": away_id, "name": away_name, "slug": away_name.lower()},
            "homeScore": {"current": score_home, "period1": score_home_ht, "period2": score_home - score_home_ht},
            "awayScore": {"current": score_away, "period1": score_away_ht, "period2": score_away - score_away_ht},
            "status": {"type": "finished"},
            "tournament": {
                "name": "Premier League",
                "slug": "premier-league",
                "uniqueTournament": {"id": tournament_id},
            },
            "season": {"name": "Premier League 24/25"},
            "roundInfo": {"round": 12},
            "venue": {"name": "Emirates Stadium", "city": {"name": "London"}},
            "referee": {"name": "Michael Oliver"},
            "attendance": 60000,
        }
    }


def _make_stats_raw(possession_home: float = 58.0, shots_home: int = 15) -> dict:
    return {
        "statistics": [
            {
                "period": "ALL",
                "groups": [
                    {
                        "groupName": "Possession",
                        "statisticsItems": [
                            {
                                "name": "Ball possession",
                                "home": f"{possession_home}%",
                                "homeValue": possession_home,
                                "away": f"{100 - possession_home}%",
                                "awayValue": 100 - possession_home,
                            }
                        ],
                    },
                    {
                        "groupName": "Shots",
                        "statisticsItems": [
                            {
                                "name": "Total shots",
                                "home": str(shots_home),
                                "homeValue": shots_home,
                                "away": "8",
                                "awayValue": 8,
                            },
                            {
                                "name": "Shots on target",
                                "home": "6",
                                "homeValue": 6,
                                "away": "3",
                                "awayValue": 3,
                            },
                        ],
                    },
                    {
                        "groupName": "Passes",
                        "statisticsItems": [
                            {
                                "name": "Passes",
                                "home": "550",
                                "homeValue": 550,
                                "away": "380",
                                "awayValue": 380,
                            },
                            {
                                "name": "Accurate passes %",
                                "home": "89%",
                                "homeValue": 89,
                                "away": "81%",
                                "awayValue": 81,
                            },
                        ],
                    },
                    {
                        "groupName": "Fouls",
                        "statisticsItems": [
                            {"name": "Corner kicks", "homeValue": 7, "awayValue": 3},
                            {"name": "Fouls", "homeValue": 10, "awayValue": 13},
                            {"name": "Yellow cards", "homeValue": 1, "awayValue": 2},
                            {"name": "Red cards", "homeValue": 0, "awayValue": 0},
                        ],
                    },
                ],
            }
        ]
    }


def _make_incidents_raw() -> dict:
    return {
        "incidents": [
            {
                "incidentType": "goal",
                "time": 23,
                "isHome": True,
                "incidentClass": "regular",
                "player": {"id": 501, "name": "Bukayo Saka"},
                "assist1": {"id": 502, "name": "Martin Ødegaard"},
            },
            {
                "incidentType": "goal",
                "time": 67,
                "isHome": True,
                "incidentClass": "penalty",
                "player": {"id": 503, "name": "Leandro Trossard"},
                "assist1": None,
            },
            {
                "incidentType": "goal",
                "time": 80,
                "isHome": False,
                "incidentClass": "regular",
                "player": {"id": 601, "name": "Cole Palmer"},
            },
            {
                "incidentType": "card",
                "time": 45,
                "isHome": False,
                "incidentClass": "yellow",
                "player": {"id": 602, "name": "Reece James"},
            },
            {
                "incidentType": "substitution",
                "time": 70,
                "isHome": True,
                "playerIn": {"id": 504, "name": "Gabriel Martinelli"},
                "playerOut": {"id": 503, "name": "Leandro Trossard"},
            },
        ]
    }


def _make_lineups_raw() -> dict:
    def player(pid: int, name: str, pos: str, num: int, starter: bool = True) -> dict:
        return {
            "player": {"id": pid, "name": name},
            "position": pos,
            "shirtNumber": num,
            "substitute": not starter,
            "captain": False,
            "statistics": {"rating": 7.2},
        }

    return {
        "home": {
            "formation": "4-3-3",
            "players": [
                player(510, "David Raya", "G", 22),
                player(511, "Ben White", "D", 4),
                player(512, "William Saliba", "D", 12),
                player(513, "Gabriel Magalhães", "D", 6),
                player(514, "Oleksandr Zinchenko", "D", 35),
            ],
        },
        "away": {
            "formation": "4-2-3-1",
            "players": [
                player(610, "Robert Sanchez", "G", 1),
                player(611, "Reece James", "D", 24),
            ],
        },
    }


# ---------------------------------------------------------------------------
# TeamConfig
# ---------------------------------------------------------------------------

class TestTeamConfig:
    def test_basic(self):
        t = TeamConfig(name="Arsenal", slug="arsenal", sofascore_id=42,
                       primary_color="#EF0107", secondary_color="#FFFFFF")
        assert t.name == "Arsenal"
        assert t.sofascore_id == 42

    def test_extra_fields_ignored(self):
        t = TeamConfig(name="Arsenal", slug="arsenal", sofascore_id=42, unknown_field="x")
        assert not hasattr(t, "unknown_field")

    def test_default_colors(self):
        t = TeamConfig(name="Arsenal", slug="arsenal", sofascore_id=42)
        assert t.primary_color == "#1B3A6B"


# ---------------------------------------------------------------------------
# MatchStats
# ---------------------------------------------------------------------------

class TestMatchStats:
    def test_parse_sofascore_stats_list(self):
        stats = MatchStats.model_validate(_make_stats_raw())
        assert stats.possession_pct == 58.0
        assert stats.shots_total == 15
        assert stats.shots_on_target == 6
        assert stats.passes_total == 550
        assert stats.pass_accuracy_pct == 89.0
        assert stats.corners == 7
        assert stats.fouls == 10
        assert stats.yellow_cards == 1
        assert stats.red_cards == 0

    def test_accepts_flat_dict(self):
        stats = MatchStats.model_validate({
            "possession_pct": 55.0,
            "shots_total": 12,
            "yellow_cards": 1,
        })
        assert stats.possession_pct == 55.0
        assert stats.shots_total == 12
        assert stats.yellow_cards == 1

    def test_missing_fields_are_none(self):
        stats = MatchStats.model_validate({"possession_pct": 50.0})
        assert stats.shots_total is None
        assert stats.corners is None

    def test_negative_shots_rejected(self):
        with pytest.raises(ValidationError):
            MatchStats.model_validate({"shots_total": -1})

    def test_extra_unknown_stat_key_ignored(self):
        raw = _make_stats_raw()
        # inject an unknown stat key
        raw["statistics"][0]["groups"][0]["statisticsItems"].append(
            {"name": "Some Future Metric", "homeValue": 99}
        )
        stats = MatchStats.model_validate(raw)
        assert stats.possession_pct == 58.0  # still parsed correctly


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------

class TestIncident:
    def test_goal_incident(self):
        inc = Incident.model_validate({
            "incidentType": "goal",
            "time": 23,
            "isHome": True,
            "incidentClass": "regular",
            "player": {"id": 501, "name": "Bukayo Saka"},
            "assist1": {"id": 502, "name": "Martin Ødegaard"},
        })
        assert inc.type == "goal"
        assert inc.minute == 23
        assert inc.goal_type == "regular"
        assert inc.scorer_name == "Bukayo Saka"
        assert inc.assist_name == "Martin Ødegaard"
        assert inc.team_side == "home"

    def test_penalty_goal(self):
        inc = Incident.model_validate({
            "incidentType": "goal",
            "time": 45,
            "isHome": False,
            "incidentClass": "penalty",
            "player": {"id": 601, "name": "Cole Palmer"},
        })
        assert inc.goal_type == "penalty"
        assert inc.team_side == "away"

    def test_own_goal(self):
        inc = Incident.model_validate({
            "incidentType": "goal",
            "time": 88,
            "isHome": False,
            "incidentClass": "ownGoal",
            "player": {"id": 510, "name": "David Raya"},
        })
        assert inc.goal_type == "own_goal"

    def test_yellow_card(self):
        inc = Incident.model_validate({
            "incidentType": "card",
            "time": 55,
            "isHome": False,
            "incidentClass": "yellow",
            "player": {"id": 602, "name": "Reece James"},
        })
        assert inc.type == "card"
        assert inc.card_type == "yellow"
        assert inc.player_name == "Reece James"

    def test_substitution(self):
        inc = Incident.model_validate({
            "incidentType": "substitution",
            "time": 70,
            "isHome": True,
            "playerIn": {"id": 504, "name": "Gabriel Martinelli"},
            "playerOut": {"id": 503, "name": "Leandro Trossard"},
        })
        assert inc.type == "substitution"
        assert inc.player_in_name == "Gabriel Martinelli"
        assert inc.player_out_name == "Leandro Trossard"

    def test_extra_fields_ignored(self):
        inc = Incident.model_validate({
            "incidentType": "goal",
            "time": 10,
            "isHome": True,
            "incidentClass": "regular",
            "player": {"id": 1, "name": "Player"},
            "completely_unknown_key": "value",
        })
        assert inc.type == "goal"


# ---------------------------------------------------------------------------
# Player / MatchLineups
# ---------------------------------------------------------------------------

class TestPlayer:
    def test_parse_sofascore_player(self):
        raw = {
            "player": {"id": 510, "name": "David Raya"},
            "position": "G",
            "shirtNumber": 22,
            "substitute": False,
            "captain": False,
            "statistics": {"rating": 7.5},
        }
        p = Player.model_validate(raw)
        assert p.player_id == 510
        assert p.player_name == "David Raya"
        assert p.is_starter is True
        assert p.rating == 7.5


class TestMatchLineups:
    def test_parse_sofascore_lineups(self):
        lineups = MatchLineups.model_validate(_make_lineups_raw())
        assert lineups.home_formation == "4-3-3"
        assert lineups.away_formation == "4-2-3-1"
        assert len(lineups.home_players) == 5
        assert lineups.home_players[0].player_name == "David Raya"


# ---------------------------------------------------------------------------
# Match
# ---------------------------------------------------------------------------

class TestMatch:
    def _make_match(self, team_name="Arsenal", score_home=2, score_away=1,
                    home_name="Arsenal") -> Match:
        return Match.model_validate({
            "team_name": team_name,
            "event_raw": _make_event_raw(
                home_name=home_name,
                score_home=score_home,
                score_away=score_away,
            ),
            "stats_raw": _make_stats_raw(),
            "incidents_raw": _make_incidents_raw(),
            "lineups_raw": _make_lineups_raw(),
        })

    def test_win_as_home_team(self):
        match = self._make_match(team_name="Arsenal", score_home=2, score_away=1)
        assert match.result == "W"

    def test_draw(self):
        match = self._make_match(score_home=1, score_away=1)
        assert match.result == "D"

    def test_loss_as_home_team(self):
        match = self._make_match(team_name="Arsenal", score_home=0, score_away=3)
        assert match.result == "L"

    def test_win_as_away_team(self):
        # Arsenal is away team, wins 3-1
        match = Match.model_validate({
            "team_name": "Arsenal",
            "event_raw": _make_event_raw(
                home_name="Chelsea",
                away_name="Arsenal",
                score_home=1,
                score_away=3,
            ),
            "stats_raw": None,
            "incidents_raw": None,
            "lineups_raw": None,
        })
        assert match.result == "W"

    def test_loss_as_away_team(self):
        match = Match.model_validate({
            "team_name": "Arsenal",
            "event_raw": _make_event_raw(
                home_name="Chelsea",
                away_name="Arsenal",
                score_home=2,
                score_away=0,
            ),
            "stats_raw": None,
            "incidents_raw": None,
            "lineups_raw": None,
        })
        assert match.result == "L"

    def test_match_date_parsed(self):
        match = self._make_match()
        assert isinstance(match.match_date, datetime)
        assert match.match_date.tzinfo is not None

    def test_half_time_scores(self):
        match = self._make_match(score_home=2, score_away=1)
        assert match.score_home_ht == 1
        assert match.score_away_ht == 0

    def test_stats_populated(self):
        match = self._make_match()
        assert match.stats is not None
        assert match.stats.possession_pct == 58.0

    def test_incidents_populated(self):
        match = self._make_match()
        goals = [i for i in match.incidents if i.type == "goal"]
        cards = [i for i in match.incidents if i.type == "card"]
        subs = [i for i in match.incidents if i.type == "substitution"]
        assert len(goals) == 3
        assert len(cards) == 1
        assert len(subs) == 1

    def test_lineups_populated(self):
        match = self._make_match()
        assert match.lineups is not None
        assert match.lineups.home_formation == "4-3-3"

    def test_missing_optional_fields_are_none(self):
        match = Match.model_validate({
            "team_name": "Arsenal",
            "event_raw": _make_event_raw(),
            "stats_raw": None,
            "incidents_raw": None,
            "lineups_raw": None,
        })
        assert match.stats is None
        assert match.incidents == []
        assert match.lineups is None


# ---------------------------------------------------------------------------
# TeamScrapeResult
# ---------------------------------------------------------------------------

class TestTeamScrapeResult:
    def test_serialization_roundtrip(self):
        team = TeamConfig(name="Arsenal", slug="arsenal", sofascore_id=42)
        match = Match.model_validate({
            "team_name": "Arsenal",
            "event_raw": _make_event_raw(),
            "stats_raw": _make_stats_raw(),
            "incidents_raw": _make_incidents_raw(),
            "lineups_raw": _make_lineups_raw(),
        })
        result = TeamScrapeResult(
            team=team,
            scraped_at=datetime.now(tz=timezone.utc),
            matches=[match],
        )
        json_str = result.model_dump_json(indent=2)
        restored = TeamScrapeResult.model_validate_json(json_str)
        assert restored.team.name == "Arsenal"
        assert len(restored.matches) == 1
        assert restored.matches[0].result == "W"


# ---------------------------------------------------------------------------
# Fixtures-based tests (only run when fixtures exist)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (FIXTURES_DIR / "event.json").exists(),
    reason="Real fixtures not captured yet — run: python scraper/capture_fixtures.py",
)
class TestRealFixtures:
    def test_event_parses(self):
        event_raw = json.loads((FIXTURES_DIR / "event.json").read_text())
        stats_raw = json.loads((FIXTURES_DIR / "statistics.json").read_text()) \
            if (FIXTURES_DIR / "statistics.json").exists() else None
        incidents_raw = json.loads((FIXTURES_DIR / "incidents.json").read_text()) \
            if (FIXTURES_DIR / "incidents.json").exists() else None
        lineups_raw = json.loads((FIXTURES_DIR / "lineups.json").read_text()) \
            if (FIXTURES_DIR / "lineups.json").exists() else None

        match = Match.model_validate({
            "team_name": "Arsenal",
            "event_raw": event_raw,
            "stats_raw": stats_raw,
            "incidents_raw": incidents_raw,
            "lineups_raw": lineups_raw,
        })
        assert match.sofascore_event_id > 0
        assert match.result in ("W", "D", "L")
