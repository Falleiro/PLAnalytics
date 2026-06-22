"""
Unit and integration tests for scraper/scraper.py.

Unit tests run without a browser using mocked Playwright.
Integration tests (marked with @pytest.mark.integration) require a real
browser and internet connection — they are skipped by default.

Run only unit tests (default / CI):
    pytest scraper/tests/test_scraper.py -m "not integration"

Run integration tests:
    pytest scraper/tests/test_scraper.py -m integration -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from scraper.scraper import find_team, load_teams, save_output
from scraper.models import TeamConfig, TeamScrapeResult
from datetime import datetime, timezone


TEAMS_YAML = Path(__file__).parent.parent / "config" / "teams.yaml"


# ---------------------------------------------------------------------------
# load_teams / find_team
# ---------------------------------------------------------------------------

class TestLoadTeams:
    def test_loads_48_teams(self):
        teams = load_teams(TEAMS_YAML)
        assert len(teams) == 48

    def test_all_teams_have_required_fields(self):
        teams = load_teams(TEAMS_YAML)
        for t in teams:
            assert t.name, f"Empty name for {t}"
            assert t.slug, f"Empty slug for {t.name}"
            assert t.sofascore_id > 0, f"Invalid ID for {t.name}"
            assert t.primary_color.startswith("#"), f"Bad primary_color for {t.name}"

    def test_no_duplicate_slugs(self):
        teams = load_teams(TEAMS_YAML)
        slugs = [t.slug for t in teams]
        assert len(slugs) == len(set(slugs)), "Duplicate slugs found"

    def test_no_duplicate_sofascore_ids(self):
        teams = load_teams(TEAMS_YAML)
        ids = [t.sofascore_id for t in teams]
        assert len(ids) == len(set(ids)), "Duplicate sofascore_ids found"


class TestFindTeam:
    def test_find_by_name(self):
        teams = load_teams(TEAMS_YAML)
        team = find_team("Brazil", teams)
        assert team.name == "Brazil"

    def test_find_case_insensitive(self):
        teams = load_teams(TEAMS_YAML)
        team = find_team("brazil", teams)
        assert team.name == "Brazil"

    def test_find_by_slug(self):
        teams = load_teams(TEAMS_YAML)
        team = find_team("south-korea", teams)
        assert team.name == "South Korea"

    def test_raises_on_unknown_team(self):
        teams = load_teams(TEAMS_YAML)
        with pytest.raises(ValueError, match="not found"):
            find_team("Vasco da Gama", teams)


# ---------------------------------------------------------------------------
# save_output
# ---------------------------------------------------------------------------

class TestSaveOutput:
    def test_output_path_format(self, tmp_path):
        with patch("scraper.scraper.OUTPUT_DIR", tmp_path):
            team = TeamConfig(name="Arsenal", slug="arsenal", sofascore_id=42)
            result = TeamScrapeResult(
                team=team,
                scraped_at=datetime(2026, 3, 15, 14, 30, tzinfo=timezone.utc),
                matches=[],
            )
            path = save_output(result)

        assert path == tmp_path / "arsenal" / "2026-03-15_14-30.json"
        assert path.exists()

    def test_output_is_valid_json(self, tmp_path):
        with patch("scraper.scraper.OUTPUT_DIR", tmp_path):
            team = TeamConfig(name="Arsenal", slug="arsenal", sofascore_id=42)
            result = TeamScrapeResult(
                team=team,
                scraped_at=datetime(2026, 3, 15, 14, 30, tzinfo=timezone.utc),
                matches=[],
            )
            path = save_output(result)

        data = json.loads(path.read_text())
        assert data["team"]["name"] == "Arsenal"
        assert data["matches"] == []


# ---------------------------------------------------------------------------
# SofaScoreScraper unit tests (mocked)
# ---------------------------------------------------------------------------

class TestSofaScraperMocked:
    """Test SofaScoreScraper methods using a mocked Playwright Page."""

    @pytest.mark.asyncio
    async def test_scrape_team_returns_valid_result(self):
        """scrape_team should turn intercepted event details into a Match."""
        from scraper.tests.test_models import (
            _make_event_raw, _make_stats_raw,
            _make_incidents_raw, _make_lineups_raw,
        )
        from scraper.scraper import SofaScoreScraper

        team = TeamConfig(name="Brazil", slug="brazil", sofascore_id=4748)

        event_id = 9999
        event_stub = {
            "id": event_id,
            "customId": "abc",
            "homeTeam": {"slug": "brazil"},
            "awayTeam": {"slug": "argentina"},
            "status": {"type": "finished"},
        }
        details = {
            "event_raw": _make_event_raw(event_id=event_id),
            "stats_raw": _make_stats_raw(),
            "incidents_raw": _make_incidents_raw(),
            "lineups_raw": _make_lineups_raw(),
        }

        page = MagicMock()
        page.on = MagicMock()
        page.remove_listener = MagicMock()
        page.goto = AsyncMock(return_value=None)
        page.wait_for_timeout = AsyncMock(return_value=None)

        scraper = SofaScoreScraper(page)

        # get_events discovers the event; _fetch_event_details returns its detail
        with patch.object(scraper, "get_events", AsyncMock(return_value=[event_stub])), \
             patch.object(scraper, "_fetch_event_details", AsyncMock(return_value=details)):
            result = await scraper.scrape_team(team, last_n=1)

        assert isinstance(result, TeamScrapeResult)
        assert len(result.matches) == 1
        assert result.matches[0].result in ("W", "D", "L")

    @pytest.mark.asyncio
    async def test_api_call_retries_on_429(self):
        """SofaScoreScraper._api_call should retry on 429 and succeed on 3rd attempt."""
        from scraper.scraper import SofaScoreScraper

        call_count = 0

        async def flaky_get(url: str):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count < 3:
                resp.status = 429
                resp.json = AsyncMock(return_value={})
            else:
                resp.status = 200
                resp.json = AsyncMock(return_value={"data": "ok"})
            return resp

        page = MagicMock()
        page.request = MagicMock()
        page.request.get = flaky_get

        scraper = SofaScoreScraper(page)
        result = await scraper._api_call("https://api.sofascore.com/api/v1/event/1")
        assert result == {"data": "ok"}
        assert call_count == 3


# ---------------------------------------------------------------------------
# Integration tests (real browser, skipped by default)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLiveScrape:
    @pytest.mark.asyncio
    async def test_scrape_brazil_5_matches(self):
        """
        End-to-end test: scrapes 5 real Brazil matches from SofaScore.
        Requires internet and the Patchright browser. SofaScore blocks headless
        mode, so this runs with headless=False.
        """
        from patchright.async_api import async_playwright
        from scraper.scraper import SofaScoreScraper

        teams = load_teams(TEAMS_YAML)
        brazil = find_team("Brazil", teams)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()
            scraper = SofaScoreScraper(page)
            result = await scraper.scrape_team(brazil, last_n=5)
            await browser.close()

        assert isinstance(result, TeamScrapeResult)
        assert len(result.matches) >= 1, "Expected at least 1 match"
        for match in result.matches:
            assert match.result in ("W", "D", "L")
            assert match.home_team
            assert match.away_team
            assert match.sofascore_event_id > 0
