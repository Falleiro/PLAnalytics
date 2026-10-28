# API REST de Previsão — World Cup Analytics

Fase de **Implantação (Deployment)** do CRISP-DM: expõe o modelo escolhido na
Fase 4 (**Random Forest calibrado** — `CalibratedClassifierCV` sigmoid +
`RandomForestClassifier`, validação temporal) como uma API REST em **FastAPI**.

A decisão de projeto central: a API **reaproveita exatamente o mesmo código** de
engenharia de features e treino do CLI `pipeline/scripts/predict_match.py`
(funções `build_training_table`, `train_best_model`, `predict_one`) — não existe
lógica de previsão duplicada. O que o notebook 04 validou é o que a API serve.

## Como criamos

1. **Fase 4 → funções reutilizáveis.** A engenharia de features do notebook
   (forma recente N=10 leakage-safe, Elo dinâmico pré-jogo com seed pelo ranking
   FIFA, head-to-head, descanso, features de equilíbrio) foi extraída para
   `predict_match.py` como funções puras.
2. **Empacotamento em API.** O pacote `api/` importa essas funções e as expõe
   via HTTP, separado por responsabilidade:

   | Arquivo | Responsabilidade |
   |---|---|
   | `main.py` | app FastAPI e endpoints |
   | `schemas.py` | contratos Pydantic de request/response |
   | `model_loader.py` | estado em memória (base + modelo) e carga tolerante a falha |

3. **Treino no startup, uma vez por processo.** No evento `lifespan`, a API lê
   `output/worldcup_dataset_clean.csv`, monta a ABT e treina o modelo em memória
   (segundos). Não há artefato `.pkl` persistido — decisão deliberada, coerente
   com o caráter manual do projeto (ver `Docs/05_deployment.md`, seção 3).
4. **Carga tolerante a falha.** Se a base estiver ausente/corrompida, a API sobe
   em **modo degradado**: `/health` responde `degraded` e os endpoints de
   previsão retornam `503` — em vez de um processo morto no boot.
5. **Simetrização.** Cada confronto é previsto pela ótica das duas seleções e as
   probabilidades são a média das duas visões, eliminando viés de orientação
   (P(A vence) = média de P_A(vitória) e P_B(derrota)).

## O que ela faz — endpoints

| Endpoint | Descrição |
|---|---|
| `GET /health` | Healthcheck: `ok`/`degraded`, se o modelo carregou, nº de seleções. |
| `GET /model-info` | Metadados do modelo servido: algoritmo, calibração, janela de forma, features, base de treino (arquivo, nº de jogos, intervalo de datas). |
| `GET /predict` | Previsão de um confronto: `home`, `away` (+ opcionais `competition`, `date`). |
| `GET /predict/today` | Jogos da Copa de uma data (default hoje) com a previsão de cada um. |
| `GET /teams` | Nomes de seleção aceitos em `home`/`away` (filtro `?search=`). |
| `GET /docs` | Swagger UI interativo (gerado pelo FastAPI). |

### Exemplo — `GET /predict?home=Brazil&away=Argentina`

```json
{
  "home": "Brazil", "away": "Argentina",
  "competition": "FIFA World Cup", "match_date": "2026-07-03",
  "is_neutral": true,
  "elo_home": 1810.6, "elo_away": 1898.5,
  "prob_home_win": 0.2395, "prob_draw": 0.2611, "prob_away_win": 0.4994,
  "favorite": "Argentina",
  "summary": "Brazil 24.0%  |  Empate 26.1%  |  Argentina 49.9%  —  favorito: Argentina (49.9%)"
}
```

**Erros tratados:** `404` (seleção sem histórico na base), `422` (`home == away`
ou data inválida), `503` (modelo indisponível — modo degradado), e em
`/predict/today`: `500` (chave da football-data.org ausente) e `502` (erro da
football-data.org).

### `/predict/today` — de onde vêm os jogos do dia

A **lista de confrontos** (quem joga, quando) vem da API oficial e gratuita
**football-data.org** (`pipeline/football_data_client.py`) — scraping via browser
não é viável dentro de uma request HTTP. **Só a lista**: estatísticas de forma,
Elo e o modelo continuam vindo 100% da nossa base. Requer `FOOTBALL_DATA_API_KEY`
no `.env` (chave gratuita: https://www.football-data.org/client/register; free
tier de 10 req/min inclui a Copa). Os 6 nomes de seleção que divergem entre a
football-data.org e a nossa base (ex.: "United States" → "USA") são normalizados
por `TEAM_NAME_MAP`.

## Como rodar

Da **raiz do projeto** (a API precisa achar `output/worldcup_dataset_clean.csv`):

```bash
# com uv:
uv run uvicorn api.main:app --reload

# ou com a venv ativa (pip):
uvicorn api.main:app --reload
```

Depois: **http://127.0.0.1:8000/docs** (Swagger — dá para testar tudo pelo
navegador).

```bash
curl "http://127.0.0.1:8000/health"
curl "http://127.0.0.1:8000/predict?home=Brazil&away=Argentina"
curl "http://127.0.0.1:8000/predict?home=Portugal&away=Uzbekistan&date=2026-06-23"
curl "http://127.0.0.1:8000/teams?search=uru"
curl "http://127.0.0.1:8000/predict/today?date=2026-07-03"   # requer FOOTBALL_DATA_API_KEY
```

**Atualizar o modelo:** após recoletar a base (`build_dataset.py` + notebook 03),
basta **reiniciar o processo** — o retreino acontece no startup.

## Testes

```bash
uv run python -m pytest api/tests/ -v
```

`api/tests/test_main.py` cobre (via `TestClient`, sem bater em serviços
externos — a football-data.org é mockada): caminho feliz de todos os endpoints,
probabilidades somando 1, seleção desconhecida (404), `home == away` (422), data
inválida (422), chave ausente (500), fixture com seleção desconhecida caindo em
`skipped`, e o modo degradado (`/health` = `degraded`, `/predict` = 503).

## Limitações conhecidas (escopo acadêmico)

- Sem autenticação/CORS — pensada para uso local, não para exposição pública.
- Sem persistência de artefato de modelo nem hot-reload — reinício manual.
- Sem Docker/CI — roda local via `uvicorn`, coerente com o restante do projeto.
- `/predict/today` não cacheia fixtures (free tier de 10 req/min é suficiente
  para uso manual).

Mais detalhes e justificativas: `Docs/05_deployment.md`.
