"""
World Cup Analytics — Setup Supabase Storage
Cria o bucket 'raw-data' para armazenar os JSONs brutos do scraper.

Uso:
    python pipeline/scripts/setup_storage.py

Pré-requisito: projeto Supabase criado e .env preenchido.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from loguru import logger
from pipeline.supabase_client import get_client

BUCKET_NAME = "raw-data"


def main() -> None:
    client = get_client()

    # Verificar se o bucket já existe
    existing = client.storage.list_buckets()
    existing_names = [b.name for b in existing]

    if BUCKET_NAME in existing_names:
        logger.info(f"Bucket '{BUCKET_NAME}' already exists — nothing to do.")
        return

    # Criar o bucket como privado (apenas SERVICE_KEY pode escrever)
    client.storage.create_bucket(
        BUCKET_NAME,
        options={
            "public": False,
            "allowed_mime_types": ["application/json"],
            "file_size_limit": 52428800,  # 50 MB
        },
    )
    logger.success(f"✓ Bucket '{BUCKET_NAME}' created successfully.")
    logger.info(
        "Files will be stored at: raw-data/sofascore/{team-slug}/{date}.json"
    )


if __name__ == "__main__":
    main()
