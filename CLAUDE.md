# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Premier League Analytics** — end-to-end data engineering portfolio project.

Pipeline: SofaScore (Playwright scraping) → Apache Airflow → Supabase (PostgreSQL) → Power BI → Next.js portfolio site.

## Planned Repository Structure

```
premier-league-analytics/
├── scraper/                  # Playwright scraper + Pydantic models
│   ├── config/teams.yaml     # 20 Premier League teams with SofaScore slugs
│   ├── scraper.py            # Entry point: --team <name> | --all | --last <n>
│   └── models.py             # Pydantic: Match, MatchStats
├── pipeline/                 # Apache Airflow
│   ├── dags/                 # dag_scraping_premier_league, dag_refresh_powerbi
│   ├── operators/            # Custom operators
│   ├── supabase_client.py    # upsert_matches(), upload_raw_json(), log_pipeline_run()
│   └── docker-compose.yaml   # Airflow local stack
├── data/                     # Schemas, output examples, contracts
├── powerbi/                  # .pbix + DAX docs
├── portfolio/                # Next.js 14 app (App Router)
│   ├── app/api/              # Server-side routes (Power BI token, Airflow trigger)
│   └── components/
├── notebooks/                # EDA + ML (Phase 7)
└── docs/architecture.png
```

## Commands

### Scraper (Python)
```bash
# Run scraper for one team or all
python scraper/scraper.py --team Arsenal
python scraper/scraper.py --all
python scraper/scraper.py --team Chelsea --last 10

# Tests
pytest scraper/tests/
pytest scraper/tests/test_models.py  # single test file
```

### Airflow (Docker)
```bash
# Todos os comandos a partir de pipeline/
cd pipeline

# 1. Build da imagem customizada (apenas na primeira vez ou após mudar Dockerfile/requirements)
docker compose build

# 2. Inicializar banco de metadados do Airflow + criar usuário admin (apenas primeira vez)
docker compose up airflow-init
# Aguardar "airflow-init exited with code 0" antes de prosseguir

# 3. Subir o stack completo (SEMPRE usar --env-file para passar variáveis Supabase)
docker compose --env-file ../.env up -d

# 4. Verificar se todos os serviços estão healthy
docker compose ps

# 5. Acompanhar logs do scheduler (onde as DAGs executam)
docker compose logs -f airflow-scheduler

# 6. Acessar UI: http://localhost:8080  (airflow / airflow)

# 7. Parar o stack
docker compose down

# 8. Parar e remover volumes (reset completo — apaga metadados)
docker compose down -v
```

#### Testar DAG manualmente via UI
1. Acesse http://localhost:8080
2. DAGs → `dag_scraping_premier_league` → botão "Trigger DAG w/ config"
3. Para um time específico, passar JSON: `{ "team_name": "Arsenal" }`
4. Acompanhar execução em Graph View ou logs de cada task

#### Testar DAG via CLI (dentro do container)
```bash
# Listar DAGs disponíveis
docker compose exec airflow-scheduler airflow dags list

# Trigger manual via CLI
docker compose exec airflow-scheduler airflow dags trigger dag_scraping_premier_league

# Trigger com parâmetro de time
docker compose exec airflow-scheduler airflow dags trigger dag_scraping_premier_league \
  --conf '{"team_name": "Arsenal"}'

# Ver últimas execuções
docker compose exec airflow-scheduler airflow dags list-runs -d dag_scraping_premier_league
```

### Fase 4 — Supabase Storage (scripts de setup)
```bash
# Rodar a partir da raiz do projeto
# Criar bucket raw-data no Storage
python pipeline/scripts/setup_storage.py

# Popular tabela teams (20 times)
python pipeline/scripts/seed_teams.py

# Rodar migrations SQL (cria tabelas, RLS, role powerbi_reader)
pip install psycopg2-binary
python pipeline/scripts/run_migrations.py
# Para pular a criação do role (senha já definida):
python pipeline/scripts/run_migrations.py --skip 003_powerbi_role.sql

# Verificar dados no Supabase após DAG rodar
python -c "
from pipeline.supabase_client import get_client
c = get_client()
print('teams:', len(c.table('teams').select('*').execute().data))
print('matches:', len(c.table('matches').select('*').execute().data))
print('match_stats:', len(c.table('match_stats').select('*').execute().data))
print('player_match_stats:', len(c.table('player_match_stats').select('*').execute().data))
"
```

### Portfolio (Next.js)
```bash
cd portfolio
npm install
npm run dev                   # development server
npm run build && npm start    # production build
```

### Deployment (Hostinger VPS via PM2)
```bash
pm2 start npm --name "portfolio" -- start
pm2 restart portfolio
```

## Architecture

### Data Flow
```
SofaScore → Playwright scraper → JSON (Supabase Storage)
                                 → upsert → Supabase PostgreSQL
                                              → Power BI (PostgreSQL connector)
                                              → Next.js (supabase-js client)

Airflow orchestrates: dag_scraping → dag_refresh_powerbi → Power BI REST API
Portfolio button → Next.js API → Airflow REST API → triggers dag_refresh_powerbi
Supabase Realtime pushes pipeline_runs status changes to the frontend (no polling)
```

### Supabase Schema (Star Schema)
- `teams` — 20 clubs with colors and badge URLs
- `matches` — fact table (team_id, match_date, score, result); unique on `(team_id, match_date, home_team, away_team)`
- `match_stats` — per-match stats (possession, shots, passes, cards), FK → matches
- `player_match_stats` — per-player per-match stats (rating, goals, assists, shots, passes, tackles, xG, xA, etc.), FK → matches; unique on `(match_id, sofascore_player_id)`
- `pipeline_runs` — execution log (dag_id, status, details jsonb); read by frontend for status display

Power BI connects via `powerbi_reader` role (SELECT only). Star schema: `fMatches → dTeams`, `fMatches → dCalendar`, `fMatchStats → fMatches`.

### Power BI Embed Security
The Azure AD `CLIENT_SECRET` must never reach the browser. The Next.js route `app/api/powerbi-token/route.ts` generates embed tokens server-side; the frontend only receives the short-lived token.

### Key Python Dependencies
`patchright` (Playwright fork não-detectável — substitui playwright), `pydantic`, `tenacity` (retry/backoff), `loguru` (structured logging), `supabase` (supabase-py), `pandas`, `asyncio`

### Por que Patchright em vez de Playwright
SofaScore usa detecção de automação via `Runtime.enable` do CDP. Playwright expõe esse sinal; Patchright patcha o CDP para escondê-lo. A troca é drop-in (`from patchright.async_api import async_playwright`). O scraper usa `PLAYWRIGHT_HEADLESS=false` + Xvfb dentro do Docker (iniciado na task `scrape_teams` da DAG) para garantir execução de JS e cookies de sessão.

### Key Frontend Dependencies
`@supabase/supabase-js`, `powerbi-client-react`, `powerbi-client`, `framer-motion`, `shadcn/ui`

## Environment Variables

See `.env.example` in `portfolio/`. Key groups:
- `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` — public, safe for frontend
- `SUPABASE_SERVICE_KEY` — private, server/Airflow only
- `AZURE_CLIENT_ID/SECRET/TENANT_ID` — Power BI embed, server-side only
- `POWERBI_WORKSPACE_ID`, `POWERBI_REPORT_ID`, `POWERBI_DATASET_ID`
- `AIRFLOW_BASE_URL/USERNAME/PASSWORD`

## Current State

### Fase 1 — Scraper ✅ (completo)
- `scraper/scraper.py` — CLI com `--team`, `--all`, `--last`, `--debug`. Usa Patchright para interceptar event IDs e faz chamadas diretas à API do SofaScore.
- `scraper/models.py` — Pydantic v2: `Match`, `MatchStats`, `MatchLineups`, `Incident`, `TeamConfig`, `TeamScrapeResult`.
- `scraper/config/teams.yaml` — 20 times da Premier League com slugs e IDs do SofaScore.
- Output em `output/{team-slug}/{timestamp}.json`. Logs em `logs/`.

### Fase 2 — Supabase (Projeto) ✅ (completo)
- Projeto `pl-analytics` criado no supabase.com.
- `.env` preenchido com `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`, `DB_HOST`, `DB_PASSWORD`.

### Fase 3 — Airflow ✅ (completo)
- `pipeline/` — Dockerfile customizado (Airflow + Patchright + Xvfb), docker-compose LocalExecutor.
- DAG `dag_scraping_premier_league` — scrape → upsert Supabase → log.
- Xvfb iniciado dentro da task `scrape_teams` para browser não-headless no Docker.
- Variáveis Supabase passadas via `--env-file ../.env`.

### Fase 4 — Supabase Storage ✅ (completo)
- Tabelas criadas: `teams`, `matches`, `match_stats`, `pipeline_runs`, `player_match_stats`.
- RLS habilitado + políticas de leitura pública.
- Role `powerbi_reader` criado (senha definida via SQL Editor).
- Bucket `raw-data` criado no Supabase Storage.
- Pipeline end-to-end validado: Arsenal scraped → matches + match_stats + player_match_stats no Supabase.
- `player_match_stats`: stats individuais de jogadores por partida (rating, gols, assistências, chutes, passes, dribles, xG, xA, etc.) extraídos do endpoint `/event/{eid}/lineups` já chamado pelo scraper. Executar `data/sql/004_player_stats.sql` no SQL Editor do Supabase.

### playwirght.py
Protótipo original para Vasco da Gama (hardcoded). Mantido apenas como referência histórica.
