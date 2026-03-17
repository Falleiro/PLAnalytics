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
    def test_loads_20_teams(self):
        teams = load_teams(TEAMS_YAML)
        assert len(teams) == 20

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
        team = find_team("Arsenal", teams)
        assert team.name == "Arsenal"

    def test_find_case_insensitive(self):
        teams = load_teams(TEAMS_YAML)
        team = find_team("arsenal", teams)
        assert team.name == "Arsenal"

    def test_find_by_slug(self):
        teams = load_teams(TEAMS_YAML)
        team = find_team("manchester-city", teams)
        assert team.name == "Manchester City"

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

    def _make_mock_page(self, team_events_data: dict, event_data: dict,
                        stats_data: dict, incidents_data: dict, lineups_data: dict):
        """Build a mock Page that returns predetermined API responses."""
        page = MagicMock()

        # page.on() should register but not do anything by default
        page.on = MagicMock()
        page.remove_listener = MagicMock()

        # page.goto() is async
        page.goto = AsyncMock(return_value=None)
        page.wait_for_timeout = AsyncMock(return_value=None)

        # page.request.get() returns a mock response
        async def mock_api_call(url: str):
            resp = MagicMock()
            resp.status = 200
            if "/events/last/" in url:
                resp.json = AsyncMock(return_value=team_events_data)
            elif url.endswith("/statistics"):
                resp.json = AsyncMock(return_value=stats_data)
            elif url.endswith("/incidents"):
                resp.json = AsyncMock(return_value=incidents_data)
            elif url.endswith("/lineups"):
                resp.json = AsyncMock(return_value=lineups_data)
            else:
                resp.json = AsyncMock(return_value=event_data)
            return resp

        page.request = MagicMock()
        page.request.get = mock_api_call
        return page

    @pytest.mark.asyncio
    async def test_scrape_team_returns_valid_result(self):
        from scraper.tests.test_models import (
            _make_event_raw, _make_stats_raw,
            _make_incidents_raw, _make_lineups_raw,
        )
        from scraper.scraper import SofaScoreScraper

        team = TeamConfig(name="Arsenal", slug="arsenal", sofascore_id=42)

        # Mock team events page 0 with one PL event
        event_id = 9999
        team_events_data = {
            "events": [
                {
                    "id": event_id,
                    "tournament": {
                        "uniqueTournament": {"id": 17},
                        "slug": "premier-league",
                    },
                    "status": {"type": "finished"},
                }
            ]
        }
        event_data = _make_event_raw(event_id=event_id)
        stats_data = _make_stats_raw()
        incidents_data = _make_incidents_raw()
        lineups_data = _make_lineups_raw()

        page = self._make_mock_page(
            team_events_data, event_data, stats_data, incidents_data, lineups_data
        )

        # Simulate the response listener being triggered (page 0 captured)
        captured = {}

        def mock_on(event_name, handler):
            if event_name == "response":
                # Simulate immediate invocation with a fake response
                pass

        page.on = mock_on

        scraper = SofaScoreScraper(page)

        # Manually inject the captured events so get_event_ids returns our event
        with patch.object(scraper, "get_event_ids", AsyncMock(return_value=[event_id])):
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
    async def test_scrape_arsenal_5_matches(self):
        """
        End-to-end test: scrapes 5 real Arsenal matches from SofaScore.
        Requires internet connection and Playwright browser.
        """
        from playwright.async_api import async_playwright
        from scraper.scraper import SofaScoreScraper

        teams = load_teams(TEAMS_YAML)
        arsenal = find_team("Arsenal", teams)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            scraper = SofaScoreScraper(page)
            result = await scraper.scrape_team(arsenal, last_n=5)
            await browser.close()

        assert isinstance(result, TeamScrapeResult)
        assert len(result.matches) >= 1, "Expected at least 1 match"
        for match in result.matches:
            assert match.result in ("W", "D", "L")
            assert match.home_team
            assert match.away_team
            assert match.sofascore_event_id > 0
