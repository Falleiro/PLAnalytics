"""
Carga do modelo em memória (estado da API).

Reúne, num só lugar, o estado que os endpoints consultam: base, ABT longa,
Elo atual, o modelo treinado e o pré-processador. O carregamento é feito uma
vez no startup (lifespan) e é TOLERANTE A FALHA: se a base não existir ou o
treino quebrar, a API sobe mesmo assim e `/health` reporta "degraded" em vez
de derrubar o processo inteiro — os endpoints de previsão respondem 503.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from pipeline.scripts.predict_match import (
    CSV_PATH,
    FEATURES,
    N,
    build_training_table,
    train_best_model,
)


class ModelState:
    """Estado compartilhado, populado por load_state() no startup."""

    df: pd.DataFrame | None = None
    tm: pd.DataFrame | None = None
    L: pd.DataFrame | None = None
    elo: dict = {}
    model = None
    prep = None
    cls: list[str] = []
    known_teams: set[str] = set()
    load_error: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.model is not None and self.load_error is None


state = ModelState()


def load_state() -> None:
    """Carrega a base e treina o modelo, guardando tudo em `state`.

    Não relança exceção: falhas ficam em `state.load_error` para que a API
    suba em modo degradado e os endpoints tratem o 503.
    """
    try:
        df0 = pd.read_csv(CSV_PATH, parse_dates=["match_date"])
        state.known_teams = set(df0["home_team"]).union(df0["away_team"])
        state.df, state.tm, state.L, state.elo = build_training_table(df0)
        state.model, state.prep, _, state.cls = train_best_model(state.L)
        state.load_error = None
        logger.info(f"Modelo treinado: {len(state.L)} linhas, {len(state.known_teams)} seleções")
    except Exception as exc:  # noqa: BLE001 — queremos degradar, não crashar
        state.load_error = str(exc)
        logger.error(f"Falha ao carregar/treinar o modelo: {exc}")


def model_metadata() -> dict:
    """Metadados do modelo servido (para o endpoint /model-info)."""
    dates = state.df["match_date"]
    return {
        "model_name": "Random Forest calibrado (CalibratedClassifierCV + RandomForestClassifier)",
        "calibration": "sigmoid (Platt), CV temporal TimeSeriesSplit(3)",
        "form_window": N,
        "n_features": len(FEATURES),
        "features": list(FEATURES),
        "n_matches": int(len(state.df)),
        "n_teams": len(state.known_teams),
        "base_file": CSV_PATH.name,
        "base_date_from": str(dates.min().date()),
        "base_date_to": str(dates.max().date()),
    }
