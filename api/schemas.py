"""
Schemas Pydantic (contratos de request/response) da API.

Separados de main.py para deixar o contrato da API legível num só lugar e
manter os endpoints (main.py) focados na lógica.
"""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Healthcheck — reporta se o modelo carregou (para deploy/monitoramento)."""

    status: str  # "ok" | "degraded"
    model_loaded: bool
    teams_loaded: int
    detail: str | None = None


class ModelInfoResponse(BaseModel):
    """Metadados do modelo servido (transparência/reprodutibilidade)."""

    model_name: str
    calibration: str
    form_window: int
    n_features: int
    features: list[str]
    n_matches: int
    n_teams: int
    base_file: str
    base_date_from: str
    base_date_to: str


class PredictResponse(BaseModel):
    home: str
    away: str
    competition: str
    match_date: str
    is_neutral: bool
    elo_home: float
    elo_away: float
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    favorite: str
    summary: str


class FixturePrediction(BaseModel):
    home: str
    away: str
    utc_date: str
    status: str
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    favorite: str
    summary: str


class TodayResponse(BaseModel):
    date: str
    fixtures: list[FixturePrediction]
    skipped: list[dict]


class TeamsResponse(BaseModel):
    count: int
    teams: list[str]
