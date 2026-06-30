"""
World Cup Analytics — Previsão de um confronto futuro (probabilidades V/E/D)

Replica a engenharia de features da Fase 4 (notebooks/04_modelagem.ipynb):
forma recente N=10 (shift implícito = só passado), Elo dinâmico pré-jogo (mando
zerado em campo neutro), ranking, descanso e Head-to-Head. Treina o melhor modelo
(Random Forest calibrado) em TODA a base e prevê o confronto pedido.

Como a base é a saída da Fase 3, a previsão é PRÉ-JOGO: usa só o histórico das
duas seleções (não precisa raspar a partida que ainda não aconteceu).

A probabilidade final é SIMETRIZADA: prevê pela ótica de cada seleção e faz a
média (P(A vence) = média de P_A(W) e P_B(L)), eliminando o viés de orientação.

Uso (da raiz do projeto):
    uv run pipeline/scripts/predict_match.py --home Portugal --away Uzbekistan
    uv run pipeline/scripts/predict_match.py --home Portugal --away Uzbekistan \
        --competition "FIFA World Cup" --date 2026-06-23
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import LabelEncoder, StandardScaler

CSV_PATH = ROOT_DIR / "output" / "worldcup_dataset_clean.csv"
ROLL = ["goals_for", "goals_against", "xg_for", "xg_against", "poss", "rating", "points", "win"]
N = 10
K, BASE, HADV_STD = 30, 1500.0, 40.0


# ── contexto da competição (idêntico ao notebook) ────────────────────────────
def competition_weight(comp: str) -> float:
    c = str(comp).lower()
    if "world cup" in c and "qualif" not in c: return 3.0
    if "qualif" in c or "eliminat" in c:       return 2.0
    if any(x in c for x in ["copa am", "european championship",
                            "africa cup", "asian cup", "gold cup"]): return 1.5
    if "nations league" in c:                  return 1.5
    if "friendly" in c or "amistoso" in c:     return 0.5
    return 1.0


def is_neutral_venue(comp: str) -> int:
    c = str(comp).lower()
    return int(any(x in c for x in ["world cup", "copa am", "european championship",
                                    "africa cup", "asian cup", "gold cup"]))


def perspective(d: pd.DataFrame, side: str) -> pd.DataFrame:
    opp = "away" if side == "home" else "home"
    o = pd.DataFrame({
        "event_id": d["sofascore_event_id"], "date": d["match_date"],
        "team": d[f"{side}_team"], "opponent": d[f"{opp}_team"],
        "is_home": 1 if side == "home" else 0,
        "goals_for": d[f"score_{side}"], "goals_against": d[f"score_{opp}"],
        "xg_for": d.get(f"{side}_expected_goals"), "xg_against": d.get(f"{opp}_expected_goals"),
        "poss": d.get(f"{side}_possession_pct"), "rating": d.get(f"{side}_rating_mean"),
    })
    gd = o["goals_for"] - o["goals_against"]
    o["points"] = np.where(gd > 0, 3, np.where(gd == 0, 1, 0))
    o["win"] = (gd > 0).astype(int)
    return o


FEATURES = ([f"diff_form_{c}" for c in ROLL] +
            ["elo_diff", "rank_diff", "rest_diff", "elo", "opp_elo",
             "is_home", "is_neutral", "match_importance",
             "h2h_winrate", "h2h_gf", "h2h_ga", "h2h_n"])


def build_training_table(df: pd.DataFrame):
    """Reconstrói a ABT longa (2 linhas/jogo) + Elo dict + estado atual por time."""
    df = df.sort_values("match_date").reset_index(drop=True)
    df["match_importance"] = df["competition"].apply(competition_weight)
    df["is_neutral"] = df["competition"].apply(is_neutral_venue)

    # forma rolling por (time, jogo)
    tm = pd.concat([perspective(df, "home"), perspective(df, "away")],
                   ignore_index=True).sort_values(["team", "date"]).reset_index(drop=True)
    g = tm.groupby("team", group_keys=False)
    for c in ROLL:
        tm[f"form_{c}"] = g[c].apply(lambda s: s.shift(1).rolling(N, min_periods=1).mean())
    tm["rest_days"] = g["date"].apply(lambda s: s.diff().dt.days)

    # Elo dinâmico pré-jogo (mando 0 em campo neutro)
    elo: dict[str, float] = {}
    ph, pa = [], []
    for r in df.itertuples():
        hadv = 0.0 if getattr(r, "is_neutral", 0) else HADV_STD
        rh, ra = elo.get(r.home_team, BASE), elo.get(r.away_team, BASE)
        ph.append(rh); pa.append(ra)
        eh = 1 / (1 + 10 ** ((ra - (rh + hadv)) / 400))
        gd = r.score_home - r.score_away
        sh = 1.0 if gd > 0 else (0.5 if gd == 0 else 0.0)
        elo[r.home_team] = rh + K * (sh - eh)
        elo[r.away_team] = ra + K * ((1 - sh) - (1 - eh))
    df["home_elo"], df["away_elo"] = ph, pa

    # ABT longa
    opp_cols = {f"form_{c}": f"opp_form_{c}" for c in ROLL}
    opp_form = (tm[["event_id", "team"] + [f"form_{c}" for c in ROLL] + ["rest_days"]]
                .rename(columns={"team": "opponent", "rest_days": "opp_rest_days", **opp_cols}))
    L = tm.merge(opp_form, on=["event_id", "opponent"], how="left")
    gf, ga = L["goals_for"], L["goals_against"]
    L["result"] = np.where(gf > ga, "W", np.where(gf == ga, "D", "L"))

    meta = (df[["sofascore_event_id", "home_elo", "away_elo", "home_team_ranking",
                "away_team_ranking", "match_importance", "is_neutral"]]
            .rename(columns={"sofascore_event_id": "event_id"}))
    L = L.merge(meta, on="event_id", how="left")
    L["elo"] = np.where(L.is_home == 1, L.home_elo, L.away_elo)
    L["opp_elo"] = np.where(L.is_home == 1, L.away_elo, L.home_elo)
    L["team_rank"] = np.where(L.is_home == 1, L.home_team_ranking, L.away_team_ranking)
    L["opp_rank"] = np.where(L.is_home == 1, L.away_team_ranking, L.home_team_ranking)

    L = L.sort_values("date").reset_index(drop=True)
    hk = L.groupby(["team", "opponent"], group_keys=False)
    L["h2h_n"] = hk.cumcount()
    L["h2h_winrate"] = hk["win"].apply(lambda s: s.shift(1).rolling(5, min_periods=1).mean())
    L["h2h_gf"] = hk["goals_for"].apply(lambda s: s.shift(1).rolling(5, min_periods=1).mean())
    L["h2h_ga"] = hk["goals_against"].apply(lambda s: s.shift(1).rolling(5, min_periods=1).mean())

    for c in ROLL:
        L[f"diff_form_{c}"] = L[f"form_{c}"] - L[f"opp_form_{c}"]
    L["elo_diff"] = L["elo"] - L["opp_elo"]
    L["rank_diff"] = L["opp_rank"] - L["team_rank"]
    L["rest_diff"] = L["rest_days"] - L["opp_rest_days"]

    return df, tm, L, elo


def latest_form(tm: pd.DataFrame, team: str) -> dict:
    """Forma que o PRÓXIMO jogo do time veria = média dos últimos N jogos reais."""
    sub = tm[tm.team == team].sort_values("date").tail(N)
    if sub.empty:
        raise SystemExit(f"Sem histórico para '{team}'. Confira o nome exato na base.")
    return {c: sub[c].mean() for c in ROLL}


def latest_rank(df: pd.DataFrame, team: str):
    """Ranking FIFA mais recente conhecido do time."""
    for _, r in df.sort_values("match_date", ascending=False).iterrows():
        if r["home_team"] == team and pd.notna(r.get("home_team_ranking")):
            return r["home_team_ranking"]
        if r["away_team"] == team and pd.notna(r.get("away_team_ranking")):
            return r["away_team_ranking"]
    return np.nan


def h2h_features(L: pd.DataFrame, team: str, opp: str) -> dict:
    """Win rate / gols dos últimos 5 confrontos diretos (ótica do team)."""
    meets = L[(L.team == team) & (L.opponent == opp)].sort_values("date").tail(5)
    if meets.empty:
        return {"h2h_winrate": np.nan, "h2h_gf": np.nan, "h2h_ga": np.nan, "h2h_n": 0}
    return {"h2h_winrate": meets["win"].mean(), "h2h_gf": meets["goals_for"].mean(),
            "h2h_ga": meets["goals_against"].mean(), "h2h_n": len(meets)}


def feature_row(tm, df, L, elo, team, opp, match_date, is_home, is_neutral, importance) -> dict:
    tf, of = latest_form(tm, team), latest_form(tm, opp)
    last_team = tm[tm.team == team]["date"].max()
    last_opp = tm[tm.team == opp]["date"].max()
    rk_t, rk_o = latest_rank(df, team), latest_rank(df, opp)
    row = {f"diff_form_{c}": tf[c] - of[c] for c in ROLL}
    row["elo_diff"] = elo.get(team, BASE) - elo.get(opp, BASE)
    row["rank_diff"] = (rk_o - rk_t) if pd.notna(rk_t) and pd.notna(rk_o) else np.nan
    row["rest_diff"] = (match_date - last_team).days - (match_date - last_opp).days
    row["elo"] = elo.get(team, BASE)
    row["opp_elo"] = elo.get(opp, BASE)
    row["is_home"] = is_home
    row["is_neutral"] = is_neutral
    row["match_importance"] = importance
    row.update(h2h_features(L, team, opp))
    return row


def train_best_model(L: pd.DataFrame):
    """Treina o Random Forest calibrado em TODA a base. Retorna (model, prep, le, cls)."""
    imputer = SimpleImputer(strategy="median").fit(L[FEATURES])
    scaler = StandardScaler().fit(imputer.transform(L[FEATURES]))
    def prep(X): return scaler.transform(imputer.transform(X))
    X = prep(L[FEATURES])
    le = LabelEncoder().fit(L["result"])
    y = le.transform(L["result"])
    # Pesa cada jogo só pela importância da competição. NÃO rebalanceamos as
    # classes (compute_sample_weight("balanced")): como o empate é a classe
    # minoritária (~21% da base), o "balanced" tratava os 3 resultados como
    # equiprováveis (~33% cada) e inflava o empate de ~21% para ~35% médio,
    # quebrando a calibração das probabilidades — que é o que esta previsão
    # entrega. Sem ele, P(empate) volta à taxa-base real e o log-loss melhora.
    sw = L["match_importance"].values.astype(float)
    # Random Forest venceu a comparacao da Fase 4 (menor log-loss de teste E menor
    # log-loss medio na CV temporal) DEPOIS de removermos o balanceamento de classe.
    # Com 'balanced', o XGBoost liderava — mas inflava P(empate) e quebrava a
    # calibracao; sem ele, o RF passou a frente. Ver notebooks/04_modelagem.ipynb.
    model = CalibratedClassifierCV(
        RandomForestClassifier(n_estimators=300, max_depth=8, min_samples_leaf=5,
                               random_state=42, n_jobs=-1),
        method="sigmoid", cv=TimeSeriesSplit(3))
    model.fit(X, y, sample_weight=sw)
    return model, prep, le, list(le.classes_)


def predict_one(model, prep, cls, tm, df, L, elo, home, away, match_date, competition):
    """Probabilidades simetrizadas (V/E/D pela ótica do MANDANTE). Retorna (pV, pE, pD)."""
    importance = competition_weight(competition)
    neutral = is_neutral_venue(competition)
    ih_home = 0 if neutral else 1   # campo neutro → sem vantagem de mando
    row_h = feature_row(tm, df, L, elo, home, away, match_date, ih_home, neutral, importance)
    row_a = feature_row(tm, df, L, elo, away, home, match_date, 0, neutral, importance)
    p_h = model.predict_proba(prep(pd.DataFrame([row_h])[FEATURES]))[0]
    p_a = model.predict_proba(prep(pd.DataFrame([row_a])[FEATURES]))[0]
    iD, iL, iW = cls.index("D"), cls.index("L"), cls.index("W")
    pw = (p_h[iW] + p_a[iL]) / 2
    pe = (p_h[iD] + p_a[iD]) / 2
    pl = (p_h[iL] + p_a[iW]) / 2
    t = pw + pe + pl
    return pw / t, pe / t, pl / t


def main() -> None:
    ap = argparse.ArgumentParser(description="Previsão de probabilidades V/E/D de um confronto")
    ap.add_argument("--home", required=True, help="Seleção mandante (nome exato da base)")
    ap.add_argument("--away", required=True, help="Seleção visitante (nome exato da base)")
    ap.add_argument("--competition", default="FIFA World Cup",
                    help="Competição (define peso e campo neutro). Default: FIFA World Cup")
    ap.add_argument("--date", default=None, help="Data do jogo YYYY-MM-DD (default: hoje)")
    args = ap.parse_args()

    match_date = (pd.Timestamp(args.date, tz="UTC") if args.date
                  else pd.Timestamp(datetime.now(timezone.utc)))
    neutral = is_neutral_venue(args.competition)

    print(f"Carregando base: {CSV_PATH.name}")
    df = pd.read_csv(CSV_PATH, parse_dates=["match_date"])
    df, tm, L, elo = build_training_table(df)
    model, prep, le, cls = train_best_model(L)

    pV, pE, pD = predict_one(model, prep, cls, tm, df, L, elo,
                             args.home, args.away, match_date, args.competition)

    print("\n" + "=" * 56)
    print(f"  {args.home}  x  {args.away}")
    print(f"  {args.competition}  |  {match_date.date()}  |  "
          f"{'campo neutro' if neutral else 'mando definido'}")
    print("=" * 56)
    print(f"  Elo:  {args.home} {elo.get(args.home, BASE):.0f}  x  {elo.get(args.away, BASE):.0f} {args.away}")
    print("-" * 56)
    print(f"  Vitória {args.home:<14} : {pV*100:5.1f}%")
    print(f"  Empate            {'':<2} : {pE*100:5.1f}%")
    print(f"  Vitória {args.away:<14} : {pD*100:5.1f}%")
    print("=" * 56)


if __name__ == "__main__":
    main()
