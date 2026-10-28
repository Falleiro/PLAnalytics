# World Cup Analytics — Previsão de Resultados da Copa do Mundo 2026

**Autor:** Caio Falleiro dos Santos

Projeto de ciência de dados, seguindo a metodologia **CRISP-DM**, para prever o
resultado de partidas da Copa do Mundo 2026 — **vitória da seleção A, empate ou
vitória da seleção B, com as respectivas probabilidades** — a partir da forma
recente das seleções.

**Base de dados:** os últimos ~50 jogos de cada uma das 48 seleções classificadas
(de qualquer competição — Copa, eliminatórias, amistosos, Copa América/Euro/etc.),
extraídos do SofaScore: placar, estatísticas de time (posse, finalizações, xG) e
estatísticas individuais de jogadores (rating, gols, xG, xA) agregadas por time.

**Fluxo completo:**

```
SofaScore ──scraper (Patchright)──▶ Supabase (PostgreSQL)
    ──build_dataset.py──▶ output/worldcup_dataset.csv          (base bruta, 1 linha/partida)
    ──notebook 03───────▶ output/worldcup_dataset_clean.csv    (base limpa + features de forma)
    ──notebook 04───────▶ escolha do modelo (Random Forest calibrado)
    ──deployment────────▶ API REST (api/) + CLI (predict_match.py)
```

---

## Mapa CRISP-DM → artefatos

| Fase CRISP-DM | Onde está |
|---|---|
| 1. Entendimento do Negócio | `Docs/01_entendimento_do_negocio.md` |
| 2. Entendimento dos Dados | `notebooks/03_preparacao_e_analise.ipynb` (EDA e qualidade) |
| 3. Preparação dos Dados | `Docs/02_agregacao_jogadores.md` + `pipeline/scripts/build_dataset.py` + notebook 03 (limpeza e features de forma leakage-safe) |
| 4. Modelagem | `notebooks/04_modelagem.ipynb` (baselines → Regressão Logística → Random Forest/XGBoost) |
| 5. Avaliação | notebook 04 (validação cruzada temporal, log-loss, calibração) |
| 6. Implantação | `api/` (API REST FastAPI — ver `api/README.md` e `Docs/05_deployment.md`) + `pipeline/scripts/predict_match.py` (CLI) |

## Estrutura do projeto

```
├── README.md                  # este arquivo
├── Docs/                      # documentação CRISP-DM
├── notebooks/
│   ├── 03_preparacao_e_analise.ipynb   # EDA, limpeza, análise estatística, features de forma
│   └── 04_modelagem.ipynb              # comparação de modelos, avaliação, escolha final
├── api/                       # API REST de previsão (FastAPI) — ver api/README.md
├── scraper/                   # scraper do SofaScore (Patchright + Pydantic) + testes
│   └── config/teams.yaml      # as 48 seleções da Copa 2026 (slugs/IDs do SofaScore)
├── pipeline/
│   ├── supabase_client.py     # upserts no Supabase
│   ├── football_data_client.py# fixtures do dia via football-data.org (só p/ /predict/today)
│   └── scripts/               # scripts de coleta, carga, dataset e previsão (ver tabela abaixo)
├── data/sql/                  # migrations do banco (schema, RLS, extensões)
├── output/
│   ├── worldcup_dataset.csv        # base bruta (1 linha por partida)
│   └── worldcup_dataset_clean.csv  # base limpa — insumo do modelo e da API
├── pyproject.toml / uv.lock   # dependências (uv)
└── requirements.txt           # dependências (pip)
```

---

## Como rodar

### 1. Instalação

Requer **Python 3.12+**. Duas opções:

```bash
# Opção A — com uv (recomendado; instala tudo do uv.lock):
uv sync --group notebooks

# Opção B — com pip:
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
```

> **Importante:** os CSVs em `output/` já vêm no pacote, então **notebooks, CLI de
> previsão e API rodam sem nenhuma credencial/`.env`**. Credenciais só são
> necessárias para *recoletar* dados (ver seção "Recoleta de dados" abaixo).

### 2. Notebooks (Fases 3–5)

```bash
uv run jupyter lab        # ou: jupyter lab (com a venv ativa)
```

- `notebooks/03_preparacao_e_analise.ipynb` — lê `output/worldcup_dataset.csv` e
  gera `worldcup_dataset_clean.csv` (os notebooks localizam o CSV automaticamente
  em `output/` ou `data/`).
- `notebooks/04_modelagem.ipynb` — lê `worldcup_dataset_clean.csv`, compara os
  modelos e justifica a escolha do Random Forest calibrado.

### 3. Previsão via CLI

```bash
uv run pipeline/scripts/predict_match.py --home Brazil --away Argentina
uv run pipeline/scripts/predict_match.py --home Portugal --away Uzbekistan --date 2026-06-23
```

Treina o modelo em memória (leva segundos) e imprime as probabilidades
Vitória/Empate/Derrota do confronto.

### 4. API REST

```bash
uv run uvicorn api.main:app --reload
# Swagger UI: http://127.0.0.1:8000/docs
```

Exemplos:

```bash
curl "http://127.0.0.1:8000/health"
curl "http://127.0.0.1:8000/predict?home=Brazil&away=Argentina"
curl "http://127.0.0.1:8000/teams?search=bra"
```

Detalhes completos (endpoints, arquitetura, decisões) em **`api/README.md`** e
`Docs/05_deployment.md`.

> O único endpoint que exige configuração é `GET /predict/today` (busca os jogos
> do dia na football-data.org): requer `FOOTBALL_DATA_API_KEY` no `.env`
> (chave gratuita em https://www.football-data.org/client/register).

### 5. Testes

```bash
uv run python -m pytest api/tests/ -v        # testes da API (não precisam de rede)
uv run python -m pytest scraper/tests/ -v    # testes do scraper (modelos Pydantic etc.)
```

---

## Recoleta de dados (opcional — exige credenciais próprias)

A base já vem pronta nos CSVs. Para **reproduzir a coleta do zero** é preciso um
projeto Supabase próprio (free tier) e as variáveis do `.env` (ver `.env.example`):

| Passo | Comando |
|---|---|
| 1. Criar tabelas (migrations) | `python pipeline/scripts/run_migrations.py` |
| 2. Popular a tabela de seleções | `python pipeline/scripts/seed_teams.py` |
| 3. Raspar + carregar no banco | `PLAYWRIGHT_HEADLESS=false python pipeline/scripts/scrape_and_load.py --all` |
| 4. Gerar o dataset único | `python pipeline/scripts/build_dataset.py --csv` |

**Scripts auxiliares** (manutenção da base):

| Script | O que faz |
|---|---|
| `pipeline/scripts/update_from_last_date.py` | Atualização **incremental**: descobre a data do último jogo no banco e carrega só os jogos da Copa finalizados desde então (sem revarrer seleção por seleção). |
| `pipeline/scripts/refetch_missing_by_id.py` | Re-busca partidas **por id de evento** do SofaScore: recupera jogos sem stats de jogador e faz backfill de features novas (xG, ranking, stats do visitante). Resumível e idempotente. |
| `pipeline/scripts/predict_today.py` | Versão CLI do "jogos do dia": descobre os jogos no SofaScore (browser) e prevê todos. |
| `pipeline/scripts/setup_storage.py` | Cria o bucket `raw-data` no Supabase Storage (backup dos JSONs brutos). |

> **Nota sobre o scraping:** o SofaScore bloqueia requests diretos e browser
> headless. O scraper usa **Patchright** (fork anti-detecção do Playwright) e deve
> rodar com browser visível: `PLAYWRIGHT_HEADLESS=false` (PowerShell:
> `$env:PLAYWRIGHT_HEADLESS="false"`). Requer `patchright install chromium`.

## Variáveis de ambiente (`.env`)

Ver `.env.example`. Resumo do que cada grupo habilita:

| Grupo | Necessário para |
|---|---|
| `FOOTBALL_DATA_API_KEY` | somente o endpoint `GET /predict/today` da API |
| `SUPABASE_URL` / `SUPABASE_*_KEY` | recoleta: carga de dados no banco |
| `DB_*` (PostgreSQL direto) | recoleta: rodar as migrations |

**Nada disso é necessário** para notebooks, `predict_match.py`, os demais
endpoints da API ou os testes.
