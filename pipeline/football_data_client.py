"""
Cliente da football-data.org — usado SOMENTE para descobrir quais jogos da
Copa do Mundo acontecem hoje/nos próximos dias (fixtures: seleções + data).

Não substitui o SofaScore como fonte de estatísticas/forma recente — isso
continua vindo da base já coletada (output/worldcup_dataset_clean.csv). Este
cliente serve um único propósito: alimentar /predict/today com a lista de
confrontos, sem depender de scraping (Patchright/browser headed) por request.

Free tier: 10 requests/minuto, competição "FIFA World Cup" (code=WC, id=2000)
incluída gratuitamente. Requer API key grátis (X-Auth-Token) — ver
https://www.football-data.org/client/register.

Mapeamento de nomes: os nomes de seleção da football-data.org nem sempre
batem com os nomes usados na nossa base (que vêm do SofaScore). Comparei as
48 seleções classificadas para a Copa 2026 contra a nossa base e 6 divergem
(as outras 42 batem exato):
    Bosnia-Herzegovina   -> Bosnia & Herzegovina
    Cape Verde Islands   -> Cabo Verde
    Congo DR             -> DR Congo
    Ivory Coast          -> Côte d'Ivoire
    Turkey               -> Türkiye
    United States        -> USA
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv
from loguru import logger

_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_PATH)

API_BASE = "https://api.football-data.org/v4"
WC_COMPETITION_ID = 2000  # FIFA World Cup (code "WC")

# nome da football-data.org -> nome usado na nossa base (SofaScore)
TEAM_NAME_MAP = {
    "Bosnia-Herzegovina": "Bosnia & Herzegovina",
    "Cape Verde Islands": "Cabo Verde",
    "Congo DR": "DR Congo",
    "Ivory Coast": "Côte d'Ivoire",
    "Turkey": "Türkiye",
    "United States": "USA",
}


def _api_key() -> str:
    key = os.getenv("FOOTBALL_DATA_API_KEY")
    if not key:
        raise RuntimeError(
            "FOOTBALL_DATA_API_KEY não definida. Cadastre-se grátis em "
            "https://www.football-data.org/client/register e adicione a chave ao .env."
        )
    return key


def normalize_team_name(name: str) -> str:
    """Mapeia o nome da football-data.org para o nome equivalente na nossa base."""
    return TEAM_NAME_MAP.get(name, name)


def fetch_wc_fixtures(date_from: date, date_to: date | None = None) -> list[dict]:
    """Busca os jogos da Copa do Mundo entre date_from e date_to (inclusive).

    Retorna uma lista de dicts: {home, away, utc_date, status}, com os nomes
    de seleção já normalizados para o padrão da nossa base.
    """
    date_to = date_to or date_from
    resp = requests.get(
        f"{API_BASE}/competitions/{WC_COMPETITION_ID}/matches",
        headers={"X-Auth-Token": _api_key()},
        params={"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()},
        timeout=15,
    )
    remaining = resp.headers.get("X-Requests-Available-Minute")
    if remaining is not None:
        logger.debug(f"football-data.org: {remaining} requests restantes neste minuto")
    resp.raise_for_status()

    fixtures = []
    for m in resp.json().get("matches", []):
        fixtures.append(
            {
                "home": normalize_team_name(m["homeTeam"]["name"]),
                "away": normalize_team_name(m["awayTeam"]["name"]),
                "utc_date": m["utcDate"],
                "status": m["status"],
            }
        )
    return fixtures
