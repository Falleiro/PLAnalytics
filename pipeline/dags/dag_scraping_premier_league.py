"""
DAG: dag_scraping_premier_league
Schedule: toda segunda-feira às 06:00 UTC
Trigger manual: passar {"team_name": "Arsenal"} para scraping de um time específico

Flow:
    scrape_teams → upsert_to_supabase → log_success
    (on failure)  → log_failure  (triggered via on_failure_callback)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airflow.decorators import dag, task
from airflow.models.param import Param
from loguru import logger

# ---------------------------------------------------------------------------
# Paths — resolve project root relative to DAG file location
# /opt/airflow/dags/dag_scraping_premier_league.py → /opt/airflow = project root in container
# ---------------------------------------------------------------------------
AIRFLOW_HOME = Path(__file__).parent.parent          # /opt/airflow
SCRAPER_DIR = AIRFLOW_HOME / "scraper"
OUTPUT_DIR = AIRFLOW_HOME / "output"

# Ensure scraper module is importable
if str(AIRFLOW_HOME) not in sys.path:
    sys.path.insert(0, str(AIRFLOW_HOME))

DAG_ID = "dag_scraping_premier_league"

default_args = {
    "owner": "pl-analytics",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
    "email_on_retry": False,
}


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

@dag(
    dag_id=DAG_ID,
    description="Scrape Premier League match data from SofaScore and upsert to Supabase",
    schedule="0 6 * * 1",   # every Monday at 06:00 UTC
    start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    default_args=default_args,
    tags=["scraping", "premier-league", "supabase"],
    params={
        "team_name": Param(
            default="",
            type="string",
            description="If set, scrape only this team (e.g. 'Arsenal'). Leave empty to scrape all 20 teams.",
        ),
        "last_n": Param(
            default=30,
            type="integer",
            description="Number of most recent matches to collect per team.",
        ),
    },
)
def dag_scraping_premier_league():

    # -----------------------------------------------------------------------
    # Task 1: scrape_teams
    # -----------------------------------------------------------------------
    @task(task_id="scrape_teams")
    def scrape_teams(**context) -> dict:
        """
        Run the SofaScore scraper for one team or all 20 teams.
        Writes JSON files to output/{team-slug}/{timestamp}.json.
        Returns a summary dict for downstream tasks.
        """
        # Start virtual display so Playwright can run non-headless in Docker/VPS
        # (SofaScore requires JS execution for cookies — headless mode gets blocked)
        os.system("rm -f /tmp/.X99-lock")
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1280x1024x24", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.environ["DISPLAY"] = ":99"
        os.environ["PLAYWRIGHT_HEADLESS"] = "false"
        time.sleep(1)

        from scraper.scraper import find_team, load_teams, run_all, run_single

        params = context["params"]
        team_name: str = params.get("team_name", "").strip()
        last_n: int = int(params.get("last_n", 30))

        teams = load_teams()

        if team_name:
            logger.info(f"Scraping single team: {team_name} (last {last_n} matches)")
            team = find_team(team_name, teams)
            asyncio.run(run_single(team, last_n))
            scraped_slugs = [team.slug]
        else:
            logger.info(f"Scraping all {len(teams)} teams (last {last_n} matches each)")
            asyncio.run(run_all(teams, last_n))
            scraped_slugs = [t.slug for t in teams]

        return {
            "scraped_slugs": scraped_slugs,
            "last_n": last_n,
            "scraped_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    # -----------------------------------------------------------------------
    # Task 2: upsert_to_supabase
    # -----------------------------------------------------------------------
    @task(task_id="upsert_to_supabase")
    def upsert_to_supabase(scrape_result: dict) -> dict:
        """
        Read the most recent JSON output for each scraped team,
        validate via Pydantic models, and upsert to Supabase.
        Also uploads the raw JSON to the 'raw-data' Storage bucket.
        """
        from scraper.models import TeamScrapeResult
        from supabase_client import (
            log_pipeline_run,
            upload_raw_json,
            upsert_matches,
            upsert_teams,
        )

        scraped_slugs: list[str] = scrape_result["scraped_slugs"]
        run_id = log_pipeline_run(DAG_ID, "running", {"teams": scraped_slugs})

        total_matches = 0
        errors: list[str] = []

        # Upsert team metadata (colors) once — idempotent
        from scraper.scraper import load_teams
        all_teams = load_teams()
        teams_in_run = [t for t in all_teams if t.slug in scraped_slugs]
        upsert_teams([t.model_dump() for t in teams_in_run])

        for slug in scraped_slugs:
            team_output_dir = OUTPUT_DIR / slug
            if not team_output_dir.exists():
                logger.warning(f"[{slug}] No output directory found — skipping")
                errors.append(f"{slug}: output directory missing")
                continue

            # Find the most recent JSON file for this team
            json_files = sorted(team_output_dir.glob("*.json"), reverse=True)
            if not json_files:
                logger.warning(f"[{slug}] No JSON files found — skipping")
                errors.append(f"{slug}: no JSON output files")
                continue

            latest_file = json_files[0]
            raw_bytes = latest_file.read_bytes()

            try:
                # Validate with Pydantic (raises on schema violations)
                result = TeamScrapeResult.model_validate_json(raw_bytes)

                # Upsert to Supabase
                matches_dicts = [m.model_dump(mode="json") for m in result.matches]
                upsert_matches(slug, matches_dicts)

                # Upload raw JSON to Storage
                upload_raw_json(slug, result.scraped_at, raw_bytes)

                total_matches += len(result.matches)
                logger.info(f"[{slug}] {len(result.matches)} matches processed")

            except Exception as e:
                logger.error(f"[{slug}] Failed to process: {e}")
                errors.append(f"{slug}: {e}")

        return {
            "run_id": run_id,
            "total_matches": total_matches,
            "errors": errors,
        }

    # -----------------------------------------------------------------------
    # Task 3: log_success
    # -----------------------------------------------------------------------
    @task(task_id="log_success")
    def log_success(upsert_result: dict) -> None:
        """Mark the pipeline_run as success and log a summary."""
        from supabase_client import update_pipeline_run

        run_id: str = upsert_result["run_id"]
        total: int = upsert_result["total_matches"]
        errors: list = upsert_result["errors"]

        final_status = "success" if not errors else "partial"
        update_pipeline_run(
            run_id,
            status=final_status,
            details={"total_matches_upserted": total, "errors": errors},
        )
        logger.info(
            f"Pipeline finished — status={final_status} "
            f"matches={total} errors={len(errors)}"
        )

    # -----------------------------------------------------------------------
    # Wire up tasks
    # -----------------------------------------------------------------------
    scrape_result = scrape_teams()
    upsert_result = upsert_to_supabase(scrape_result)
    log_success(upsert_result)


dag_scraping_premier_league()
