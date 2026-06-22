"""
World Cup Analytics — Playwright scraper.

Usage:
    python scraper/scraper.py --team Brazil
    python scraper/scraper.py --team Argentina --last 10
    python scraper/scraper.py --all
    python scraper/scraper.py --all --last 10
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
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
# The website calls its API under the www host; a same-origin page fetch to this
# base inherits the anti-bot token (a direct request to api.sofascore.com → 403).
API_PAGE_BASE = "https://www.sofascore.com/api/v1"
TEAM_PAGE_BASE = "https://www.sofascore.com/team/football"
MATCH_PAGE_BASE = "https://www.sofascore.com/football/match"

# Per-event detail fetching (navigate to match page + intercept the stats call)
EVENT_DETAIL_WAIT_S = 8  # max seconds to wait for the statistics response

# Tournament filter on SofaScore (uniqueTournament IDs).
# None = collect matches from ANY tournament (World Cup, qualifiers, friendlies,
# Copa América, Euro, Nations League, etc.) — the goal is the full recent form of
# each national team. Set to a set of IDs (e.g. {16}) to restrict to specific
# tournaments. The competition name is stored per match, so filtering can still be
# done later when building features.
TOURNAMENT_IDS: set[int] | None = None

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

    async def get_events(self, team: TeamConfig, last_n: int) -> list[dict[str, Any]]:
        """
        Navigate to the team page and intercept the team events API response(s).
        Returns the raw event objects for the most recent `last_n` finished
        matches (optionally filtered by TOURNAMENT_IDS), newest first.

        SofaScore blocks direct API calls (403 "challenge"), so we rely on
        intercepting the responses the page makes itself.
        """
        captured_pages: dict[int, dict] = {}

        async def handle_response(response):
            for page_num in range(3):  # intercept pages 0-2 if the page loads them
                if f"/team/{team.sofascore_id}/events/last/{page_num}" in response.url:
                    try:
                        captured_pages[page_num] = await response.json()
                        logger.debug(f"Intercepted team events page {page_num}")
                    except Exception:
                        pass

        self.page.on("response", handle_response)

        team_url = f"{TEAM_PAGE_BASE}/{team.slug}/{team.sofascore_id}"
        logger.info(f"[{team.name}] Navigating to team page → {team_url}")
        await self.page.goto(team_url, wait_until="domcontentloaded", timeout=60_000)

        # Wait up to 10s for page 0 to be captured
        for _ in range(20):
            if 0 in captured_pages:
                break
            await self.page.wait_for_timeout(500)

        self.page.remove_listener("response", handle_response)

        if 0 not in captured_pages:
            logger.error(
                f"[{team.name}] Could not capture team events. SofaScore blocks "
                f"headless mode — run with PLAYWRIGHT_HEADLESS=false."
            )
            return []

        # Collect finished events from all captured pages
        events: list[dict[str, Any]] = []
        for page_num in sorted(captured_pages):
            for event in captured_pages[page_num].get("events", []):
                if TOURNAMENT_IDS is not None:
                    tid = (
                        event.get("tournament", {})
                        .get("uniqueTournament", {})
                        .get("id")
                    )
                    if tid not in TOURNAMENT_IDS:
                        continue
                if event.get("status", {}).get("type") != "finished":
                    continue
                events.append(event)

        # Most recent first, capped at last_n
        events.sort(key=lambda e: e.get("startTimestamp", 0), reverse=True)
        events = events[:last_n]
        logger.info(f"[{team.name}] Found {len(events)} finished events")
        return events

    # ------------------------------------------------------------------
    # Step 2: fetch detail of one event (via match-page interception)
    # ------------------------------------------------------------------

    @staticmethod
    def _match_url(event: dict[str, Any]) -> str | None:
        """Build the SofaScore match-page URL for an event."""
        eid = event.get("id")
        custom_id = event.get("customId")
        home_slug = event.get("homeTeam", {}).get("slug", "")
        away_slug = event.get("awayTeam", {}).get("slug", "")
        if not eid or not custom_id:
            return None
        return f"{MATCH_PAGE_BASE}/{home_slug}-{away_slug}/{custom_id}#id:{eid}"

    async def _page_fetch_json(self, paths: dict[str, str]) -> dict[str, Any]:
        """
        Fetch JSON endpoints from inside the page's JS context. A same-origin
        fetch on www.sofascore.com inherits the anti-bot token, so /event,
        /lineups and /incidents return 200 (unlike a direct request → 403).
        """
        js = """async (paths) => {
            const out = {};
            for (const [k, u] of Object.entries(paths)) {
                try { const r = await fetch(u); if (r.ok) out[k] = await r.json(); }
                catch (e) { /* ignore */ }
            }
            return out;
        }"""
        try:
            return await self.page.evaluate(js, paths)
        except Exception as e:
            logger.warning(f"page.evaluate fetch failed: {e}")
            return {}

    async def _click_statistics_tab(self) -> None:
        """Force the lazy-loaded Statistics tab to fetch /statistics."""
        for sel in ('a:has-text("Statistics")', 'button:has-text("Statistics")'):
            try:
                el = self.page.locator(sel).first
                if await el.count() and await el.is_visible():
                    await el.click(timeout=3000)
                    return
            except Exception:
                pass

    async def _fetch_event_details(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """
        Fetch all detail for one event from its match page.

        - event / lineups / incidents: fetched directly from the page context.
        - statistics: a direct fetch returns 403, so we intercept the call the
          page makes when the Statistics tab loads (clicking it to force it).

        Returns event_raw / stats_raw / incidents_raw / lineups_raw, or None if
        the base /event payload could not be retrieved.
        """
        eid = event["id"]
        match_url = self._match_url(event)
        if match_url is None:
            logger.warning(f"Event {eid}: missing customId/slug — cannot build match URL")
            return None

        # Intercept the page's own statistics response (direct fetch is 403)
        stats_holder: dict[str, Any] = {}
        pat_stats = re.compile(rf"/api/v1/event/{eid}/statistics")

        async def on_stats(response):
            if (
                response.status == 200
                and "data" not in stats_holder
                and pat_stats.search(response.url)
            ):
                try:
                    stats_holder["data"] = await response.json()
                except Exception:
                    pass

        self.page.on("response", on_stats)
        try:
            await self.page.goto(match_url, wait_until="domcontentloaded", timeout=60_000)
            await self.page.wait_for_timeout(600)

            # Force the Statistics tab if it hasn't auto-loaded, then wait for it
            if "data" not in stats_holder:
                await self._click_statistics_tab()
            for _ in range(EVENT_DETAIL_WAIT_S * 2):
                if "data" in stats_holder:
                    break
                await self.page.wait_for_timeout(500)

            # The remaining endpoints are reliable via a same-origin page fetch
            fetched = await self._page_fetch_json({
                "event": f"{API_PAGE_BASE}/event/{eid}",
                "lineups": f"{API_PAGE_BASE}/event/{eid}/lineups",
                "incidents": f"{API_PAGE_BASE}/event/{eid}/incidents",
            })
        finally:
            self.page.remove_listener("response", on_stats)

        if not fetched.get("event"):
            return None
        return {
            "event_raw": fetched.get("event"),
            "stats_raw": stats_holder.get("data"),
            "incidents_raw": fetched.get("incidents"),
            "lineups_raw": fetched.get("lineups"),
        }

    # ------------------------------------------------------------------
    # Step 3: scrape one team
    # ------------------------------------------------------------------

    async def scrape_team(
        self,
        team: TeamConfig,
        last_n: int = 30,
        seen_event_ids: set[int] | None = None,
    ) -> TeamScrapeResult:
        """
        Scrape a team's recent matches.

        If `seen_event_ids` is given, events whose id is already in the set are
        skipped (and newly processed ids are added). This deduplicates matches
        between two tracked teams (e.g. a Brazil×Argentina game appears in both
        teams' event lists but is fetched only once) when scraping in bulk.
        """
        events = await self.get_events(team, last_n)
        if not events:
            logger.warning(f"[{team.name}] No events found — returning empty result")
            return TeamScrapeResult(
                team=team,
                scraped_at=datetime.now(tz=timezone.utc),
                matches=[],
            )

        matches: list[Match] = []
        skipped = 0
        for i, event in enumerate(events, start=1):
            eid = event["id"]

            # Global dedup: claim the id before any await so concurrent workers
            # never fetch the same match twice.
            if seen_event_ids is not None:
                if eid in seen_event_ids:
                    skipped += 1
                    continue
                seen_event_ids.add(eid)

            logger.info(f"[{team.name}] [{i}/{len(events)}] Event {eid}")
            try:
                raw = await self._fetch_event_details(event)
                if raw is None:
                    logger.warning(f"[{team.name}] Event {eid}: details not captured — skipping")
                else:
                    match = Match.model_validate({"team_name": team.name, **raw})
                    matches.append(match)
                    logger.debug(
                        f"[{team.name}] {match.home_team} {match.score_home}–{match.score_away} "
                        f"{match.away_team} ({match.result})"
                    )
            except Exception as e:
                logger.error(f"[{team.name}] Event {eid} failed: {e}")

            # Rate limit between matches
            await asyncio.sleep(random.uniform(MATCH_DELAY_MIN, MATCH_DELAY_MAX))

        dedup_note = f" ({skipped} já vistos pulados)" if skipped else ""
        logger.info(f"[{team.name}] Scraped {len(matches)}/{len(events)} matches{dedup_note}")
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
        description="World Cup Analytics — SofaScore scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--team", type=str, metavar="NAME", help="National team name, e.g. Brazil")
    group.add_argument("--all", action="store_true", help="Scrape all teams in teams.yaml")
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
