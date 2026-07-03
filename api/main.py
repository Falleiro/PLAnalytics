"""
World Cup Analytics — API REST de previsão (Fase 5 CRISP-DM: Deployment)

Expõe via FastAPI o mesmo pipeline de previsão do predict_match.py (CLI):
treina o Random Forest calibrado UMA VEZ no startup (em memória, igual ao
CLI) e serve previsões de confrontos explícitos (home/away) a partir daí.

Organização (pacote api/):
    main.py          — app FastAPI + endpoints (este arquivo)
    schemas.py       — contratos Pydantic de request/response
    model_loader.py  — estado em memória (base + modelo), carga tolerante a falha

Endpoints: /predict (confronto explícito), /predict/today (jogos do dia via
football-data.org, ver pipeline/football_data_client.py), /teams (nomes de
seleção aceitos), /model-info (metadados do modelo) e /health.

Para atualizar o modelo após um novo scraping/build_dataset, reinicie o
processo (mesma filosofia de execução manual do resto do projeto).

Uso (da raiz do projeto):
    uv run uvicorn api.main:app --reload
    # depois: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query, status

from api.model_loader import load_state, model_metadata, state
from api.schemas import (
    FixturePrediction,
    HealthResponse,
    ModelInfoResponse,
    PredictResponse,
    TeamsResponse,
    TodayResponse,
)
from pipeline.football_data_client import fetch_wc_fixtures
from pipeline.scripts.predict_match import BASE, is_neutral_venue, predict_one


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_state()  # tolerante a falha: em erro, sobe degradado (ver /health)
    yield


app = FastAPI(
    title="World Cup Analytics API",
    description="Previsão de probabilidades (Vitória/Empate/Derrota) de confrontos da Copa do Mundo 2026.",
    version="0.1.0",
    lifespan=lifespan,
)


# ── helpers ───────────────────────────────────────────────────────────────
def _require_ready() -> None:
    """503 se o modelo não carregou (base ausente/erro no treino no startup)."""
    if not state.is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Modelo indisponível: {state.load_error or 'não carregado'}",
        )


def _parse_date_param(date_str: str | None) -> pd.Timestamp:
    try:
        return pd.Timestamp(date_str, tz="UTC") if date_str else pd.Timestamp(datetime.now(timezone.utc))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Data inválida: '{date_str}'. Use YYYY-MM-DD.")


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _favorite_and_summary(home: str, away: str, pV: float, pE: float, pD: float) -> tuple[str, str]:
    """Lado com maior probabilidade ('Empate' se for o caso) + frase legível."""
    probs = {home: pV, "Empate": pE, away: pD}
    favorite = max(probs, key=probs.get)
    summary = (
        f"{home} {_pct(pV)}  |  Empate {_pct(pE)}  |  {away} {_pct(pD)}"
        f"  —  favorito: {favorite} ({_pct(probs[favorite])})"
    )
    return favorite, summary


# ── endpoints ─────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok" if state.is_ready else "degraded",
        model_loaded=state.is_ready,
        teams_loaded=len(state.known_teams),
        detail=state.load_error,
    )


@app.get("/model-info", response_model=ModelInfoResponse)
def model_info() -> ModelInfoResponse:
    """Metadados do modelo servido: algoritmo, calibração, features e a base
    de treino (arquivo, nº de jogos, janela temporal)."""
    _require_ready()
    return ModelInfoResponse(**model_metadata())


@app.get("/predict", response_model=PredictResponse)
def predict(
    home: str = Query(..., description="Seleção mandante (nome exato da base)"),
    away: str = Query(..., description="Seleção visitante (nome exato da base)"),
    competition: str = Query(
        "FIFA World Cup", description="Competição (define peso e campo neutro)"
    ),
    date: str | None = Query(None, description="Data do jogo YYYY-MM-DD (default: hoje)"),
) -> PredictResponse:
    _require_ready()
    for label, team in (("home", home), ("away", away)):
        if team not in state.known_teams:
            raise HTTPException(
                status_code=404, detail=f"Seleção '{team}' ({label}) sem histórico na base."
            )
    if home == away:
        raise HTTPException(status_code=422, detail="home e away devem ser seleções diferentes.")

    match_date = _parse_date_param(date)

    pV, pE, pD = predict_one(
        state.model,
        state.prep,
        state.cls,
        state.tm,
        state.df,
        state.L,
        state.elo,
        home,
        away,
        match_date,
        competition,
    )
    favorite, summary = _favorite_and_summary(home, away, pV, pE, pD)

    return PredictResponse(
        home=home,
        away=away,
        competition=competition,
        match_date=str(match_date.date()),
        is_neutral=bool(is_neutral_venue(competition)),
        elo_home=round(state.elo.get(home, BASE), 1),
        elo_away=round(state.elo.get(away, BASE), 1),
        prob_home_win=round(pV, 4),
        prob_draw=round(pE, 4),
        prob_away_win=round(pD, 4),
        favorite=favorite,
        summary=summary,
    )


@app.get("/teams", response_model=TeamsResponse)
def teams(
    search: str | None = Query(None, description="Filtra por substring (case-insensitive)"),
) -> TeamsResponse:
    """Lista as seleções disponíveis na base — os nomes exatos aceitos por
    home/away em /predict."""
    _require_ready()
    names = state.known_teams
    if search:
        s = search.lower()
        names = {n for n in names if s in n.lower()}
    ordered = sorted(names)
    return TeamsResponse(count=len(ordered), teams=ordered)


@app.get("/predict/today", response_model=TodayResponse)
def predict_today(
    date: str | None = Query(None, description="Data YYYY-MM-DD (default: hoje, UTC)"),
) -> TodayResponse:
    """Jogos da Copa do Mundo da data pedida (fixtures via football-data.org)
    com a previsão de cada um, usando o mesmo modelo/base do /predict."""
    _require_ready()
    match_date = _parse_date_param(date)
    target_date = match_date.date()

    try:
        fixtures = fetch_wc_fixtures(target_date)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"football-data.org: {e}")

    results, skipped = [], []
    for fx in fixtures:
        if fx["home"] not in state.known_teams or fx["away"] not in state.known_teams:
            skipped.append(fx)
            continue
        pV, pE, pD = predict_one(
            state.model,
            state.prep,
            state.cls,
            state.tm,
            state.df,
            state.L,
            state.elo,
            fx["home"],
            fx["away"],
            match_date,
            "FIFA World Cup",
        )
        favorite, summary = _favorite_and_summary(fx["home"], fx["away"], pV, pE, pD)
        results.append(
            FixturePrediction(
                home=fx["home"],
                away=fx["away"],
                utc_date=fx["utc_date"],
                status=fx["status"],
                prob_home_win=round(pV, 4),
                prob_draw=round(pE, 4),
                prob_away_win=round(pD, 4),
                favorite=favorite,
                summary=summary,
            )
        )

    results.sort(key=lambda f: f.utc_date)
    return TodayResponse(date=target_date.isoformat(), fixtures=results, skipped=skipped)
