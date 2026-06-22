"""
One-off script to capture real SofaScore API responses and save them as test fixtures.

Run ONCE to generate scraper/tests/fixtures/*.json, then the models can be built
against the real JSON shape.

Usage:
    python scraper/capture_fixtures.py

It will open a browser, navigate to the team page below, grab ~10 recent event IDs,
then capture all 5 endpoints for the first event and save them to fixtures/.

Change TEAM_SLUG / TEAM_ID to the national team you want to sample from.
"""

import asyncio
import json
import sys
from pathlib import Path

from patchright.async_api import async_playwright

TEAM_SLUG = "arsenal"
TEAM_ID = 42
FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"
API_BASE = "https://api.sofascore.com/api/v1"


async def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        # --- Step 1: capture team events (last page 0) ---
        team_events_data: dict | None = None

        async def handle_response(response):
            nonlocal team_events_data
            if f"/team/{TEAM_ID}/events/last/0" in response.url:
                try:
                    team_events_data = await response.json()
                    print(f"✅ Captured team events: {response.url}")
                except Exception as e:
                    print(f"❌ Failed to parse team events: {e}")

        page.on("response", handle_response)

        team_url = f"https://www.sofascore.com/team/football/{TEAM_SLUG}/{TEAM_ID}"
        print(f"Navigating to {team_url} ...")
        await page.goto(team_url, wait_until="networkidle", timeout=60000)

        # wait up to 10s for the team events response
        for _ in range(20):
            if team_events_data is not None:
                break
            await page.wait_for_timeout(500)

        if team_events_data is None:
            print("❌ Failed to capture team events. Exiting.")
            await browser.close()
            sys.exit(1)

        # save fixture
        _save(FIXTURES_DIR / "team_events.json", team_events_data)

        # extract first event ID (most recent finished match)
        events = team_events_data.get("events", [])
        if not events:
            print("❌ No events found in team events response.")
            await browser.close()
            sys.exit(1)

        event_id = events[0]["id"]
        print(f"\nUsing event_id={event_id} for sub-endpoint fixtures\n")

        # --- Step 2: call each sub-endpoint via page.request.get ---
        endpoints = {
            "event": f"{API_BASE}/event/{event_id}",
            "statistics": f"{API_BASE}/event/{event_id}/statistics",
            "incidents": f"{API_BASE}/event/{event_id}/incidents",
            "lineups": f"{API_BASE}/event/{event_id}/lineups",
        }

        for name, url in endpoints.items():
            print(f"Fetching {name}: {url}")
            try:
                resp = await page.request.get(url)
                if resp.status == 200:
                    data = await resp.json()
                    _save(FIXTURES_DIR / f"{name}.json", data)
                    print(f"✅ Saved {name}.json ({resp.status})")
                else:
                    print(f"❌ HTTP {resp.status} for {name}")
            except Exception as e:
                print(f"❌ Error fetching {name}: {e}")

            await asyncio.sleep(1)

        await browser.close()

    print(f"\n✅ All fixtures saved to {FIXTURES_DIR.resolve()}")
    print("You can now run: pytest scraper/tests/test_models.py")


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
