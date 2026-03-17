"""
Premier League Analytics — Seed tabela `teams`
Lê scraper/config/teams.yaml e faz upsert dos 20 times no Supabase.

Uso:
    python pipeline/scripts/seed_teams.py

Pré-requisito: tabela `teams` criada (001_schema.sql executado).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

import yaml
from loguru import logger
from pipeline.supabase_client import upsert_teams


def main() -> None:
    teams_yaml = ROOT_DIR / "scraper" / "config" / "teams.yaml"
    if not teams_yaml.exists():
        logger.error(f"teams.yaml not found at {teams_yaml}")
        sys.exit(1)

    with teams_yaml.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    teams: list[dict] = data["teams"]
    logger.info(f"Seeding {len(teams)} teams from {teams_yaml.name}...")

    upsert_teams(teams)

    logger.success(f"✓ {len(teams)} teams upserted successfully.")
    logger.info("You can verify in Supabase → Table Editor → teams")


if __name__ == "__main__":
    main()
