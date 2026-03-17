"""
Premier League Analytics — Playwright scraper.

Usage:
    python scraper/scraper.py --team Arsenal
    python scraper/scraper.py --team Chelsea --last 10
    python scraper/scraper.py --all
    python scraper/scraper.py --all --last 10
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running as `python scraper/scraper.py` from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from loguru import logger
from patchright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Project root is one level above this file (scraper/)
ROOT_DIR = Path(__file__).parent.parent
TEAMS_YAML = Path(__file__).parent / "config" / "teams.yaml"
OUTPUT_DIR = ROOT_DIR / "output"
LOGS_DIR = ROOT_DIR / "logs"

API_BASE = "https://api.sofascore.com/api/v1"
TEAM_PAGE_BASE = "https://www.sofascore.com/team/football"

# Premier League unique tournament ID on SofaScore
PL_TOURNAMENT_IDS = {17}  # add Cup IDs here if you ever want to include them

# Rate limiting
MATCH_DELAY_MIN = 1.0   # seconds between matches (jittered)
MATCH_DELAY_MAX = 2.5
TEAM_DELAY = 5.0         # seconds between teams (for --all)

# Concurrency
MAX_CONCURRENT_TEAMS = 3

# Headless mode: True by default (required in Docker/CI). Set PLAYWRIGHT_HEADLESS=false to show browser locally.
_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() != "false"


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(debug: bool = False) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    level = "DEBUG" if debug else "INFO"
    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )
    logger.add(
        LOGS_DIR / "scraper_{time}.log",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} — {message}",
    )


# ---------------------------------------------------------------------------
# Teams config
# ---------------------------------------------------------------------------

from scraper.models import Match, MatchLineups, MatchStats, TeamConfig, TeamScrapeResult  # noqa: E402


def load_teams(yaml_path: Path = TEAMS_YAML) -> list[TeamConfig]:
    with yaml_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [TeamConfig(**t) for t in data["teams"]]


def find_team(name: str, teams: list[TeamConfig]) -> TeamConfig:
    name_lower = name.lower()
    for t in teams:
        if t.name.lower() == name_lower or t.slug.lower() == name_lower:
            return t
    available = ", ".join(t.name for t in teams)
    raise ValueError(f"Team '{name}' not found. Available: {available}")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_output(result: TeamScrapeResult) -> Path:
    timestamp = result.scraped_at.strftime("%Y-%m-%d_%H-%M")
    path = OUTPUT_DIR / result.team.slug / f"{timestamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    logger.info(f"Saved {len(result.matches)} matches → {path}")
    return path


# ---------------------------------------------------------------------------
# Core scraper class
# ---------------------------------------------------------------------------

class SofaScoreScraper:
    """Scrapes SofaScore for a single team using Patchright network interception."""

    def __init__(self, page: Page) -> None:
        self.page = page

    # ------------------------------------------------------------------
    # Low-level API call with retry
    # ------------------------------------------------------------------

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(Exception),
        reraise=True,
        before_sleep=lambda rs: logger.warning(
            f"Retry {rs.attempt_number}/3 — {rs.outcome.exception()}"
        ),
    )
    async def _api_call(self, url: str) -> dict[str, Any]:
        response = await self.page.request.get(url)
        if response.status == 429:
            raise RuntimeError(f"Rate limited (429) on {url}")
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status} on {url}")
        return await response.json()

    # ------------------------------------------------------------------
    # Step 1: discover event IDs for a team
    # ------------------------------------------------------------------

    async def get_event_ids(self, team: TeamConfig, last_n: int) -> list[int]:
        """
        Navigate to the team page, intercept the team events API call,
        then paginate with direct API calls until we have `last_n` PL event IDs.
        """
        event_ids: list[int] = []
        captured_pages: dict[int, dict] = {}

        async def handle_response(response):
            for page_num in range(3):  # intercept pages 0-2
                if f"/team/{team.sofascore_id}/events/last/{page_num}" in response.url:
                    try:
                        data = await response.json()
                        captured_pages[page_num] = data
                        logger.debug(f"Intercepted team events page {page_num}")
                    except Exception:
                        pass

        self.page.on("response", handle_response)

        team_url = f"{TEAM_PAGE_BASE}/{team.slug}/{team.sofascore_id}"
        logger.info(f"[{team.name}] Navigating to team page → {team_url}")
        await self.page.goto(team_url, wait_until="domcontentloaded", timeout=60_000)

        # Wait up to 10s for the first page to be captured
        for _ in range(20):
            if 0 in captured_pages:
                break
            await self.page.wait_for_timeout(500)

        if 0 not in captured_pages:
            logger.warning(
                f"[{team.name}] Interception failed (headless mode?) — falling back to direct API call"
            )
            try:
                url = f"{API_BASE}/team/{team.sofascore_id}/events/last/0"
                captured_pages[0] = await self._api_call(url)
            except Exception as e:
                logger.error(f"[{team.name}] Direct API call also failed: {e}")
                return []

        # Collect IDs from page 0 (already captured), then fetch pages 1, 2 via API
        for page_num in range(3):
            if len(event_ids) >= last_n:
                break
            if page_num == 0:
                data = captured_pages[0]
            else:
                url = f"{API_BASE}/team/{team.sofascore_id}/events/last/{page_num}"
                logger.debug(f"[{team.name}] Fetching event page {page_num}")
                try:
                    data = await self._api_call(url)
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                except Exception as e:
                    logger.warning(f"[{team.name}] Could not fetch page {page_num}: {e}")
                    break

            for event in data.get("events", []):
                if len(event_ids) >= last_n:
                    break
                # Filter to Premier League only
                tid = (
                    event.get("tournament", {})
                    .get("uniqueTournament", {})
                    .get("id")
                )
                if tid not in PL_TOURNAMENT_IDS:
                    continue
                # Skip matches that haven't finished yet
                status = event.get("status", {}).get("type", "")
                if status not in ("finished",):
                    continue
                event_ids.append(event["id"])

        self.page.remove_listener("response", handle_response)
        logger.info(f"[{team.name}] Found {len(event_ids)} finished PL events")
        return event_ids

    # ------------------------------------------------------------------
    # Step 2: scrape one team
    # ------------------------------------------------------------------

    async def scrape_team(self, team: TeamConfig, last_n: int = 30) -> TeamScrapeResult:
        event_ids = await self.get_event_ids(team, last_n)
        if not event_ids:
            logger.warning(f"[{team.name}] No events found — returning empty result")
            return TeamScrapeResult(
                team=team,
                scraped_at=datetime.now(tz=timezone.utc),
                matches=[],
            )

        matches: list[Match] = []
        for i, eid in enumerate(event_ids, start=1):
            logger.info(f"[{team.name}] [{i}/{len(event_ids)}] Event {eid}")
            try:
                event_raw = await self._api_call(f"{API_BASE}/event/{eid}")
                stats_raw = await self._api_call(f"{API_BASE}/event/{eid}/statistics")
                incidents_raw = await self._api_call(f"{API_BASE}/event/{eid}/incidents")
                lineups_raw = await self._api_call(f"{API_BASE}/event/{eid}/lineups")

                match = Match.model_validate({
                    "team_name": team.name,
                    "event_raw": event_raw,
                    "stats_raw": stats_raw,
                    "incidents_raw": incidents_raw,
                    "lineups_raw": lineups_raw,
                })
                matches.append(match)
                logger.debug(
                    f"[{team.name}] {match.home_team} {match.score_home}–{match.score_away} "
                    f"{match.away_team} ({match.result})"
                )
            except Exception as e:
                logger.error(f"[{team.name}] Event {eid} failed: {e}")

            # Rate limit between matches
            await asyncio.sleep(random.uniform(MATCH_DELAY_MIN, MATCH_DELAY_MAX))

        return TeamScrapeResult(
            team=team,
            scraped_at=datetime.now(tz=timezone.utc),
            matches=matches,
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def run_single(team: TeamConfig, last_n: int) -> None:
    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=_HEADLESS)
        context: BrowserContext = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()
        scraper = SofaScoreScraper(page)
        result = await scraper.scrape_team(team, last_n)
        save_output(result)
        await browser.close()


async def run_all(teams: list[TeamConfig], last_n: int) -> None:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TEAMS)

    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=_HEADLESS)
        context: BrowserContext = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        async def bounded_scrape(team: TeamConfig) -> None:
            async with semaphore:
                page = await context.new_page()
                scraper = SofaScoreScraper(page)
                try:
                    result = await scraper.scrape_team(team, last_n)
                    save_output(result)
                except Exception as e:
                    logger.error(f"[{team.name}] Failed — skipping: {e}")
                finally:
                    await page.close()
                await asyncio.sleep(TEAM_DELAY)

        tasks = [bounded_scrape(t) for t in teams]
        await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Premier League Analytics — SofaScore scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--team", type=str, metavar="NAME", help="Team name, e.g. Arsenal")
    group.add_argument("--all", action="store_true", help="Scrape all 20 PL teams")
    parser.add_argument(
        "--last", type=int, default=30, metavar="N",
        help="Number of matches to collect per team (default: 30)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    args = parser.parse_args()

    _setup_logging(debug=args.debug)

    teams = load_teams()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.team:
        team = find_team(args.team, teams)
        logger.info(f"Scraping {team.name} — last {args.last} matches")
        asyncio.run(run_single(team, args.last))
    else:
        logger.info(f"Scraping all {len(teams)} teams — last {args.last} matches each")
        asyncio.run(run_all(teams, args.last))


if __name__ == "__main__":
    main()
