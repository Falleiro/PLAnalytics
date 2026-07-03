# Fase 6 (CRISP-DM) — Implantação (Deployment)

> **Fase CRISP-DM:** Implantação (Deployment)
> **Projeto:** World Cup Analytics
> **Autor:** Caio Falleiro
> **Data:** Julho/2026

---

## 1. Decisão: previsão exposta via API REST

A [Fase 1](01_entendimento_do_negocio.md) previa a entrega final das previsões em
uma **planilha Excel**. Para permitir consumo programático (ex.: por um front-end,
outro serviço, ou chamadas ad-hoc), o resultado da modelagem (Fase 4,
`notebooks/04_modelagem.ipynb`) passou a ser exposto **também** via uma **API REST**
local, em `api/main.py`, além de (não em substituição a) qualquer exportação Excel
que venha a ser gerada manualmente a partir dos notebooks.

A API reaproveita **exatamente** a mesma engenharia de features e o mesmo modelo
(Random Forest calibrado) já usados pelo CLI `pipeline/scripts/predict_match.py` —
não há lógica de previsão duplicada.

### Organização do pacote `api/`

O código é separado por responsabilidade (facilita leitura e manutenção):

| Arquivo | Responsabilidade |
|---------|------------------|
| `api/main.py` | App FastAPI e endpoints. |
| `api/schemas.py` | Contratos Pydantic de request/response (o "contrato da API" num só lugar). |
| `api/model_loader.py` | Estado em memória (base + modelo) e carga **tolerante a falha**. |

## 2. Escopo desta primeira versão

| Endpoint | Descrição |
|----------|-----------|
| `GET /health` | Healthcheck; reporta `ok`/`degraded`, se o modelo carregou e nº de seleções. |
| `GET /model-info` | Metadados do modelo servido (algoritmo, calibração, features, base). |
| `GET /predict` | Previsão de um confronto explícito (`home`, `away`, `competition`, `date`). |
| `GET /predict/today` | Jogos da Copa de uma data (default hoje) + previsão de cada um. |
| `GET /teams` | Lista os nomes de seleção aceitos em `home`/`away` (com filtro `search`). |
| `GET /docs` | Swagger UI (gerado automaticamente pelo FastAPI). |

### `/predict/today`: football-data.org em vez de scraping

O fluxo original (`predict_today.py`) depende de abrir um browser via
**Patchright em modo headed** para escapar do bloqueio anti-bot do SofaScore —
inviável dentro do ciclo de vida de uma request HTTP comum (lento, exige
display, frágil sob concorrência).

Para `/predict/today`, a **lista de confrontos** (quem joga, quando) passou a
vir da API oficial e gratuita **[football-data.org](https://www.football-data.org/)**
(`pipeline/football_data_client.py`), não do SofaScore. Escopo estritamente
limitado a isso — **estatísticas de forma recente, Elo e o próprio modelo
continuam vindo 100% da nossa base** (`worldcup_dataset_clean.csv`); a
football-data.org só informa "quem joga contra quem, em que data".

- Free tier inclui a Copa do Mundo (`competition id=2000`, code `WC`),
  10 requests/minuto, requer API key grátis (`FOOTBALL_DATA_API_KEY` no `.env`,
  ver `.env.example`).
- **Mapeamento de nomes:** comparei as 48 seleções classificadas para 2026 (via
  `GET /v4/competitions/2000/teams`) contra os nomes usados na nossa base
  (SofaScore). 42 batem exatamente; 6 precisam de mapeamento
  (`TEAM_NAME_MAP` em `pipeline/football_data_client.py`):

  | football-data.org | nossa base (SofaScore) |
  |---|---|
  | Bosnia-Herzegovina | Bosnia & Herzegovina |
  | Cape Verde Islands | Cabo Verde |
  | Congo DR | DR Congo |
  | Ivory Coast | Côte d'Ivoire |
  | Turkey | Türkiye |
  | United States | USA |

  Validado ao vivo contra a API real (não é suposição): busquei jogos de cada
  uma das 6 seleções mapeadas na temporada 2026 e confirmei, via
  `/predict/today`, que todas resolvem para a nossa base sem cair em
  `skipped` (campo que a resposta retorna para qualquer confronto cujo nome de
  seleção — mapeado ou não — ainda não bata com `known_teams`, como rede de
  segurança para nomes novos/imprevistos).

## 3. Arquitetura e ciclo de vida do modelo

- **Treino em memória, uma vez, no startup** (evento `lifespan` do FastAPI): a API
  carrega `output/worldcup_dataset_clean.csv`, monta a ABT (`build_training_table`)
  e treina o Random Forest calibrado (`train_best_model`) — igual ao que o CLI faz
  a cada execução, mas aqui só acontece **uma vez por processo**, não por request.
- **Sem artefato `.pkl` persistido nesta versão.** Decisão deliberada: o projeto é
  de **execução manual** por escolha (ver `CLAUDE.md`), sem orquestração. Gerenciar
  versionamento de artefato adicionaria uma etapa manual extra (rodar script de
  treino → salvar → apontar API pro artefato certo) sem benefício claro no escopo
  atual (treino leva segundos, não minutos).
- **Atualização do modelo:** quando a base é recoletada (`build_dataset.py`), o
  processo da API precisa ser **reiniciado** para retreinar com os dados novos.
  Não há endpoint de hot-reload nesta versão.
- **Carga tolerante a falha:** `load_state()` (em `api/model_loader.py`) captura
  qualquer erro de carga/treino em `state.load_error` em vez de deixar o startup
  quebrar. A API sobe em **modo degradado**: `/health` responde `status: degraded`
  (`model_loaded: false`) e os endpoints de previsão retornam `503`. Assim uma base
  ausente/corrompida vira um diagnóstico claro, não um processo morto no boot.

## 4. Contrato da API

### `GET /health` e `GET /model-info`

`/health` — healthcheck para deploy/monitoramento:
```json
{"status": "ok", "model_loaded": true, "teams_loaded": 194, "detail": null}
```
Em falha de carga: `{"status": "degraded", "model_loaded": false, "teams_loaded": 0, "detail": "<erro>"}`.

`/model-info` — metadados do modelo servido (transparência/reprodutibilidade —
alinhado ao espírito CRISP-DM). Expõe algoritmo, calibração, janela de forma
(`N`), a lista de `features`, e a base de treino (arquivo, nº de jogos, intervalo
de datas). Retorna `503` se o modelo não carregou.

### `GET /predict`

**Query params:** `home` (obrigatório), `away` (obrigatório), `competition`
(default `"FIFA World Cup"`), `date` (formato `YYYY-MM-DD`, default hoje).

**Resposta (200):**
```json
{
  "home": "Brazil",
  "away": "Argentina",
  "competition": "FIFA World Cup",
  "match_date": "2026-07-03",
  "is_neutral": true,
  "elo_home": 1810.6,
  "elo_away": 1898.5,
  "prob_home_win": 0.2395,
  "prob_draw": 0.2611,
  "prob_away_win": 0.4994,
  "favorite": "Argentina",
  "summary": "Brazil 24.0%  |  Empate 26.1%  |  Argentina 49.9%  —  favorito: Argentina (49.9%)"
}
```
`favorite`/`summary` são derivados dos três `prob_*` (o campeão da comparação
das três probabilidades, "Empate" incluso) — não é um dado novo do modelo, só
uma leitura mais rápida do mesmo resultado no Swagger/curl.

**Erros:**
- `404` — seleção (`home` ou `away`) sem histórico na base.
- `422` — `home == away`, ou `date` em formato inválido.
- `503` — modelo indisponível (API subiu em modo degradado).

### `GET /predict/today`

**Query params:** `date` (formato `YYYY-MM-DD`, default hoje/UTC).

**Resposta (200):**
```json
{
  "date": "2026-07-03",
  "fixtures": [
    {
      "home": "Argentina", "away": "Cabo Verde",
      "utc_date": "2026-07-03T22:00:00Z", "status": "TIMED",
      "prob_home_win": 0.7464, "prob_draw": 0.1638, "prob_away_win": 0.0899,
      "favorite": "Argentina",
      "summary": "Argentina 74.6%  |  Empate 16.4%  |  Cabo Verde 9.0%  —  favorito: Argentina (74.6%)"
    }
  ],
  "skipped": []
}
```
`skipped` lista confrontos cujo `home`/`away` (após o mapeamento de nomes) não
bateu com nenhuma seleção da nossa base — não deveria acontecer para as 48
seleções da Copa 2026, mas fica como sinalização explícita em vez de falhar
silenciosamente ou quebrar a resposta inteira. `fixtures` vem ordenado por
`utc_date`.

**Erros:** `422` (data inválida), `500` (`FOOTBALL_DATA_API_KEY` ausente),
`502` (football-data.org retornou erro), `503` (modelo indisponível).

### `GET /teams`

**Query params:** `search` (opcional, filtro por substring case-insensitive).

**Resposta (200):** `{"count": 194, "teams": ["Afghanistan", "Albania", ...]}`
— serve para descobrir o nome exato aceito por `home`/`away` em `/predict`
(inclui todas as seleções da base, não só as 48 da Copa 2026, já que
`/predict` aceita qualquer confronto presente na base).

## 5. Como rodar

```bash
uv run uvicorn api.main:app --reload
# Swagger em http://127.0.0.1:8000/docs
```

## 6. Testes

`api/tests/test_main.py` cobre, via `fastapi.testclient.TestClient` (dispara o
`lifespan`, treina uma vez por sessão de teste):
- `/health` responde `ok` e `/model-info` traz os metadados esperados;
- `/predict` no caminho feliz (probabilidades somam ~1, todas em [0,1]);
- `/predict` com data explícita;
- seleção desconhecida → 404;
- `home == away` → 422;
- data inválida → 422;
- `/predict/today` no caminho feliz (com `fetch_wc_fixtures` mockado — não bate
  na football-data.org real durante os testes), incluindo o caso de fixture
  com seleção não reconhecida caindo em `skipped`;
- `/predict/today` com data inválida → 422 e sem `FOOTBALL_DATA_API_KEY` → 500;
- `/teams` (lista ordenada e filtro `search`);
- carga degradada: `load_state()` marca `load_error` sem crashar, `/health`
  reporta `degraded` e `/predict` retorna `503` quando o modelo não carregou.

```bash
uv run python -m pytest api/tests/ -v
```

## 7. Limitações conhecidas (escopo acadêmico)

- Sem autenticação/autorização — não deve ser exposta publicamente sem revisão.
  (O `/health` degradado e o `503` cobrem indisponibilidade do modelo, não acesso.)
- Sem CORS configurado (adicionar se um front-end externo for consumir a API).
- Sem persistência de artefato de modelo nem endpoint de reload — reinício manual.
- Sem Docker/CI — roda localmente via `uv run uvicorn`, consistente com o restante
  do projeto (execução manual, sem orquestração).
- `/predict/today` depende do free tier da football-data.org (10 req/min) — não
  cacheia fixtures entre requests; suficiente para o uso manual/local do projeto.

## 8. Próximos passos (não implementados)

- Persistir o artefato treinado (`joblib`) caso o tempo de treino cresça com a base.
- CORS + autenticação simples, caso a API passe a ser consumida por um front externo.
- Cache curto (ex.: alguns minutos) das fixtures da football-data.org, caso o
  rate limit de 10 req/min vire um problema real de uso.
