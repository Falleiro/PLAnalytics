"""
Testes da API (api/main.py).

Usa o TestClient como context manager para disparar o lifespan (treina o
modelo uma vez, reaproveitado por todos os testes do módulo).
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["teams_loaded"] > 0
    assert body["detail"] is None


def test_model_info(client):
    resp = client.get("/model-info")
    assert resp.status_code == 200
    body = resp.json()
    assert "Random Forest" in body["model_name"]
    assert body["form_window"] == 10
    assert body["n_features"] == len(body["features"])
    assert body["n_matches"] > 0
    assert body["n_teams"] > 0
    assert body["base_date_from"] <= body["base_date_to"]


def test_predict_happy_path(client):
    resp = client.get(
        "/predict",
        params={"home": "Brazil", "away": "Argentina", "competition": "FIFA World Cup"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["home"] == "Brazil"
    assert body["away"] == "Argentina"
    probs = [body["prob_home_win"], body["prob_draw"], body["prob_away_win"]]
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert pytest.approx(sum(probs), abs=1e-3) == 1.0
    assert body["favorite"] in ("Brazil", "Argentina", "Empate")
    assert body["favorite"] in body["summary"]


def test_predict_with_explicit_date(client):
    resp = client.get(
        "/predict",
        params={"home": "France", "away": "Germany", "date": "2026-06-20"},
    )
    assert resp.status_code == 200
    assert resp.json()["match_date"] == "2026-06-20"


def test_predict_unknown_team_returns_404(client):
    resp = client.get("/predict", params={"home": "Brazil", "away": "Wakanda"})
    assert resp.status_code == 404


def test_predict_same_team_returns_422(client):
    resp = client.get("/predict", params={"home": "Brazil", "away": "Brazil"})
    assert resp.status_code == 422


def test_predict_invalid_date_returns_422(client):
    resp = client.get(
        "/predict", params={"home": "Brazil", "away": "Argentina", "date": "not-a-date"}
    )
    assert resp.status_code == 422


def test_predict_today_success(client, mocker):
    mocker.patch(
        "api.main.fetch_wc_fixtures",
        return_value=[
            {"home": "Brazil", "away": "Argentina", "utc_date": "2026-07-03T22:00:00Z", "status": "TIMED"},
            {"home": "Atlantis", "away": "Narnia", "utc_date": "2026-07-03T22:00:00Z", "status": "TIMED"},
        ],
    )
    resp = client.get("/predict/today", params={"date": "2026-07-03"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-03"
    assert len(body["fixtures"]) == 1
    assert body["fixtures"][0]["home"] == "Brazil"
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["home"] == "Atlantis"


def test_predict_today_invalid_date_returns_422(client):
    resp = client.get("/predict/today", params={"date": "not-a-date"})
    assert resp.status_code == 422


def test_predict_today_missing_api_key_returns_500(client, mocker):
    mocker.patch("api.main.fetch_wc_fixtures", side_effect=RuntimeError("FOOTBALL_DATA_API_KEY não definida."))
    resp = client.get("/predict/today")
    assert resp.status_code == 500


def test_teams_list(client):
    resp = client.get("/teams")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == len(body["teams"])
    assert body["teams"] == sorted(body["teams"])
    assert "Brazil" in body["teams"]


def test_teams_search_filter(client):
    resp = client.get("/teams", params={"search": "braz"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["teams"] == ["Brazil"]


def test_load_state_degrades_when_base_missing(mocker):
    """Se a base não carrega, load_state() não crasha: marca load_error e
    is_ready fica False. Usa um ModelState novo para não tocar no estado
    compartilhado do `client`."""
    from api import model_loader

    fresh = model_loader.ModelState()
    mocker.patch.object(model_loader, "state", fresh)
    mocker.patch.object(model_loader.pd, "read_csv", side_effect=FileNotFoundError("sem base"))

    model_loader.load_state()

    assert fresh.is_ready is False
    assert fresh.load_error is not None


def test_health_reports_degraded(client, mocker):
    from api import main

    not_ready = main.state.__class__()  # model=None → is_ready False
    not_ready.load_error = "base ausente"
    mocker.patch.object(main, "state", not_ready)

    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["model_loaded"] is False
    assert body["detail"] == "base ausente"


def test_predict_returns_503_when_not_ready(client, mocker):
    from api import main

    mocker.patch.object(main, "state", main.state.__class__())  # modelo não carregado
    resp = client.get("/predict", params={"home": "Brazil", "away": "Argentina"})
    assert resp.status_code == 503
