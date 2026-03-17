"""
Premier League Analytics — Migration runner
Executa os arquivos SQL de data/sql/ em ordem no PostgreSQL do Supabase.

Uso:
    python pipeline/scripts/run_migrations.py

Requer:
    pip install psycopg2-binary python-dotenv
"""

from __future__ import annotations

import sys
from pathlib import Path

# Resolve project root (two levels above this file: pipeline/scripts/ → root)
ROOT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
import os
import psycopg2
from loguru import logger

load_dotenv(ROOT_DIR / ".env")

SQL_DIR = ROOT_DIR / "data" / "sql"

MIGRATIONS = [
    "001_schema.sql",
    "002_rls_policies.sql",
    "003_powerbi_role.sql",
]


def get_connection() -> psycopg2.extensions.connection:
    host = os.environ["DB_HOST"].strip()
    port = int(os.environ.get("DB_PORT", "5432"))
    user = os.environ.get("DB_USER", "postgres").strip()
    password = os.environ["DB_PASSWORD"].strip()
    dbname = os.environ.get("DB_NAME", "postgres").strip()

    logger.info(f"Connecting to {host}:{port}/{dbname} as {user}")
    return psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname,
        sslmode="require",
    )


def run_migrations(skip: list[str] | None = None) -> None:
    """
    Execute SQL migration files in order.

    Args:
        skip: list of filenames to skip (e.g. ["003_powerbi_role.sql"] if you
              want to set the password manually in the Supabase dashboard).
    """
    skip = skip or []
    conn = get_connection()
    conn.autocommit = True  # DDL doesn't work well inside transactions

    try:
        with conn.cursor() as cur:
            for filename in MIGRATIONS:
                if filename in skip:
                    logger.warning(f"Skipping {filename} (in skip list)")
                    continue

                sql_path = SQL_DIR / filename
                if not sql_path.exists():
                    logger.error(f"SQL file not found: {sql_path}")
                    sys.exit(1)

                sql = sql_path.read_text(encoding="utf-8")
                logger.info(f"Running {filename} ...")
                try:
                    cur.execute(sql)
                    logger.success(f"✓ {filename} executed successfully")
                except psycopg2.Error as e:
                    logger.error(f"✗ {filename} failed: {e}")
                    raise
    finally:
        conn.close()
        logger.info("Connection closed")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run Supabase SQL migrations for Premier League Analytics"
    )
    parser.add_argument(
        "--skip",
        nargs="*",
        default=[],
        metavar="FILE",
        help=(
            "SQL files to skip. Example: --skip 003_powerbi_role.sql "
            "(use this if you prefer to set the powerbi_reader password manually)"
        ),
    )
    args = parser.parse_args()

    logger.info("Starting migrations...")
    run_migrations(skip=args.skip)
    logger.success("All migrations completed.")
