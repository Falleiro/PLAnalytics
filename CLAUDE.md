# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**World Cup Analytics** — projeto de ciência de dados para **previsão de resultados
de jogos da Copa do Mundo** (vitória da seleção A / empate / vitória da seleção B,
com probabilidades) a partir da forma recente das seleções.

Projeto **enxuto e de execução manual** (sem orquestração automatizada). Metodologia:
**CRISP-DM** (documentação em `Docs/`).

Pipeline (manual): SofaScore (scraping com Patchright) → Supabase (PostgreSQL) →
notebooks Python (pandas + scikit-learn) → **planilha Excel com as previsões**.

> **Migração:** este projeto nasceu como "Premier League Analytics" (clubes da PL,
> com Airflow + Power BI + portfólio Next.js). Foi repivotado para Copa do Mundo e
> teve a orquestração (Airflow), os dashboards (Power BI) e o portfólio (Next.js)
> **removidos do escopo**. O scraper e o banco foram reaproveitados.

## Repository Structure

```
PLAnalytics/                  # (nome da pasta mantido por legado)
├── scraper/                  # Playwright/Patchright scraper + Pydantic models
│   ├── config/teams.yaml     # Seleções + slugs/IDs do SofaScore (VER PENDÊNCIA)
│   ├── scraper.py            # Entry point: --team <name> | --all | --last <n>
│   ├── models.py             # Pydantic: Match, MatchStats, MatchLineups, etc.
│   ├── capture_fixtures.py   # Script one-off p/ capturar fixtures de teste
│   └── tests/                # pytest (fixtures usam dados de exemplo)
├── pipeline/                 # Carga manual de dados (NÃO é mais orquestração)
│   ├── supabase_client.py    # upsert_matches(), upsert_teams(), upload_raw_json()
│   └── scripts/              # setup_storage, seed_teams, run_migrations
├── data/sql/                 # Migrations: 001_schema, 002_rls_policies, 004_player_stats
├── Docs/                     # Documentação CRISP-DM (01_entendimento_do_negocio.md, ...)
├── notebooks/                # EDA + feature engineering + modelagem (a criar)
├── output/                   # JSONs do scraper (gitignored)
└── logs/                     # Logs do scraper (gitignored)
```

## Commands

### Scraper (Python)
```bash
# Rodar o scraper para uma seleção ou todas as do teams.yaml
python scraper/scraper.py --team Brazil
python scraper/scraper.py --all
python scraper/scraper.py --team Argentina --last 10

# Tests
pytest scraper/tests/
pytest scraper/tests/test_models.py        # arquivo único
```

### Supabase — setup e carga manual (rodar da raiz do projeto)
```bash
# Rodar migrations SQL (cria tabelas, RLS) — requer DB_* no .env
pip install psycopg2-binary
python pipeline/scripts/run_migrations.py
# Pular um arquivo específico:
python pipeline/scripts/run_migrations.py --skip 002_rls_policies.sql

# Criar bucket raw-data no Storage
python pipeline/scripts/setup_storage.py

# Popular tabela teams a partir do teams.yaml
python pipeline/scripts/seed_teams.py

# Verificar dados no Supabase
python -c "
from pipeline.supabase_client import get_client
c = get_client()
print('teams:', len(c.table('teams').select('*').execute().data))
print('matches:', len(c.table('matches').select('*').execute().data))
print('match_stats:', len(c.table('match_stats').select('*').execute().data))
print('player_match_stats:', len(c.table('player_match_stats').select('*').execute().data))
"
```

## Architecture

### Data Flow (manual)
```
SofaScore → Patchright scraper → JSON em output/ (+ opcional: Supabase Storage)
                                → upsert (supabase_client) → Supabase PostgreSQL
                                              → notebooks (pandas/scikit-learn)
                                                  → ABT (1 linha por confronto)
                                                  → modelo → previsões → Excel
```
Cada etapa é executada **manualmente** sob demanda — não há agendamento/cron.

### Supabase Schema (Star Schema)
- `teams` — seleções (cores, slug, badge)
- `matches` — tabela fato (team_id, match_date, score, result); unique em
  `(team_id, match_date, home_team, away_team)`
- `match_stats` — stats por partida (posse, finalizações, passes, cartões), FK → matches
- `player_match_stats` — stats por jogador por partida (rating, gols, assists, xG,
  xA, etc.), FK → matches; unique em `(match_id, sofascore_player_id)`

### Modelagem (planejada — ver Docs/)
- Unidade de modelagem: **1 linha por confronto**, com features de forma recente das
  duas seleções (médias móveis dos últimos N jogos) + colunas de diferença (A − B).
- **As features de forma (médias móveis leakage-safe) são construídas já na Fase 3**
  (`notebooks/03_preparacao_e_analise.ipynb`, Seção 3.5), não mais só na modelagem.
  Motivo: a análise estatística inferencial da Fase 3 (Seção 4.4) precisa testar a
  significância dos **preditores reais pré-jogo** (`form_diff_*`, `rank_diff`), e não
  de estatísticas medidas durante a partida (que seriam tautológicas/vazamento). A
  Fase 4 reaproveita essas mesmas colunas para treinar o modelo.
- Alvo: classificação multiclasse {Vitória A, Empate, Vitória B}.
- Modelos: baselines → Regressão Logística → Random Forest / XGBoost-LightGBM.
- Avaliação: validação cruzada temporal, log-loss, calibração de probabilidades.
- **Sem rede neural** (base pequena, tabular — boosting tende a vencer).

## Key Python Dependencies
`patchright` (fork não-detectável do Playwright), `pydantic`, `tenacity` (retry/
backoff), `loguru` (logging), `supabase` (supabase-py), `pandas`, `scikit-learn`,
`openpyxl` (export Excel), `asyncio`.

### Por que Patchright em vez de Playwright
SofaScore detecta automação via `Runtime.enable` do CDP. Playwright expõe esse sinal;
Patchright patcha o CDP para escondê-lo. A troca é drop-in
(`from patchright.async_api import async_playwright`).

## Environment Variables

Ver `.env.example`. Grupos:
- `SUPABASE_URL` / `SUPABASE_ANON_KEY` — leitura
- `SUPABASE_SERVICE_KEY` — escrita (carga de dados)
- `DB_HOST/PORT/USER/PASSWORD/NAME` — conexão PostgreSQL direta (migrations/análises)

## Current State

### Concluído (herdado e adaptado)
- **Scraper** (`scraper/`): Patchright + Pydantic v2 (`Match`, `MatchStats`,
  `MatchLineups`, `Incident`, `TeamConfig`, `TeamScrapeResult`). CLI `--team/--all/--last`.
  `TOURNAMENT_IDS = None` → coleta jogos de **qualquer torneio** (Copa, eliminatórias,
  amistosos, Copa América/Euro/Nations League). O `competition` é salvo por jogo, então
  dá para filtrar depois ao montar features.
- **teams.yaml**: 48 seleções da Copa 2026 + `sofascore_id` (extraídos do SofaScore).
- **Supabase**: tabelas criadas, RLS habilitado, carga manual via `supabase_client`.
- **Docs/**: Fase 1 CRISP-DM (Entendimento do Negócio).

**Importante para rodar o scraper:** SofaScore retorna 403 em request direto e em modo
headless. Rodar com `PLAYWRIGHT_HEADLESS=false` (no Windows não precisa de Xvfb). O
acesso à API é por interceptação das chamadas que a própria página faz.

### Pendências da migração (a fazer)
1. **Lógica de descoberta de jogos**: hoje é *por time* (últimos N eventos do time,
   3 páginas / ~90 jogos). Avaliar adicionar o conceito de **fase** (grupos →
   mata-mata) ao modelo `Match` para os jogos de Copa.

### Limpeza de legado (concluída — jun/2026)
- **`pipeline_runs`** removida: tabela e funções `log/update_pipeline_run` deletadas;
  migration `006_drop_pipeline_runs.sql` limpa bancos existentes; 001/002 não a criam mais.
- **`escopo.md`** removido (documento antigo do projeto Premier League).

### playwirght.py
Protótipo original para Vasco da Gama (hardcoded). Mantido apenas como referência histórica.
