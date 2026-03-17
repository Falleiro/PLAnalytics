# ⚽ Premier League Analytics — Escopo de Projeto

> **Stack:** Python · Playwright · Apache Airflow · Supabase · Power BI · Next.js  
> **Objetivo:** Pipeline end-to-end de engenharia de dados com dashboard interativo e portfolio web  
> **Audiência:** Vagas de Engenheiro de Dados, Analista de Dados e Data Scientist

---

## Visão Geral

| Item | Descrição |
|---|---|
| Fonte de dados | SofaScore (web scraping via Playwright) |
| Escopo de dados | Últimos 30 jogos de cada time da Premier League (20 times) |
| Orquestração | Apache Airflow (DAGs agendadas + trigger manual) |
| Armazenamento | Supabase (PostgreSQL + Storage) |
| Visualização | Power BI com tema dinâmico por time (cores + badge) |
| Atualização | Power BI REST API acionada via Airflow DAG |
| Portfolio | Site Next.js hospedado na Hostinger (VPS + PM2 + Nginx) |
| Projeto futuro | Modelos de Machine Learning (previsão de resultados) |

---

## Estrutura do Repositório

```
premier-league-analytics/
├── scraper/                  # Scripts Playwright + config dos times
│   ├── config/
│   │   └── teams.yaml        # Lista dos 20 times com slugs do SofaScore
│   ├── scraper.py            # Script principal (aceita --team ou --all)
│   ├── models.py             # Pydantic models para validação dos dados
│   └── tests/
├── pipeline/                 # Apache Airflow
│   ├── dags/
│   │   ├── dag_scraping_premier_league.py
│   │   └── dag_refresh_powerbi.py
│   ├── operators/            # Operadores customizados
│   ├── supabase_client.py    # Módulo de integração com Supabase
│   └── docker-compose.yaml   # Setup local do Airflow
├── data/                     # Schemas, exemplos de output, contratos
├── powerbi/
│   ├── PremierLeagueAnalytics.pbix
│   └── docs/                 # Documentação das medidas DAX
├── portfolio/                # Next.js app
│   ├── app/
│   ├── components/
│   └── .env.example
├── notebooks/                # EDA e modelos DS (Fase 7)
├── docs/
│   └── architecture.png      # Diagrama do pipeline
├── .github/
│   └── workflows/
│       └── deploy.yml        # CI/CD: deploy automático para Hostinger VPS
└── README.md
```

---

## Fase 1 — Extração de Dados (Web Scraping)

### Contexto
Script parcialmente pronto em Playwright que extrai os últimos 30 jogos do Vasco. Precisa ser refatorado para operar sobre qualquer time da Premier League.

### TODOs

- [ ] Criar `scraper/config/teams.yaml` com os 20 times da Premier League e seus slugs/IDs no SofaScore
- [ ] Refatorar `scraper.py` para aceitar parâmetro `--team <nome>` ou `--all`
- [ ] Implementar loop que itera sobre todos os times quando `--all` for passado
- [ ] Adicionar retry logic com backoff exponencial (usar biblioteca `tenacity`)
- [ ] Implementar rate limiting entre requisições (evitar bloqueio de IP)
- [ ] Serializar output em JSON por time com timestamp no nome do arquivo
- [ ] Adicionar logs estruturados com `loguru` (níveis DEBUG/INFO/WARNING/ERROR)
- [ ] Validar dados extraídos com Pydantic models antes de salvar
- [ ] Escrever testes unitários básicos (`pytest`)

### Schema de dados por jogo

```python
class Match(BaseModel):
    team_name: str
    match_date: datetime
    home_team: str
    away_team: str
    score_home: int
    score_away: int
    competition: str
    venue: str
    result: Literal["W", "D", "L"]   # do ponto de vista do time analisado
    stats: MatchStats

class MatchStats(BaseModel):
    possession_pct: float
    shots_total: int
    shots_on_target: int
    passes_total: int
    pass_accuracy_pct: float
    corners: int
    fouls: int
    yellow_cards: int
    red_cards: int
```

### Bibliotecas

| Biblioteca | Uso |
|---|---|
| `playwright` | Automação do browser (scraping JS-heavy) |
| `asyncio` | Execução assíncrona para múltiplos times em paralelo |
| `pydantic` | Validação e modelagem dos dados extraídos |
| `tenacity` | Retry automático com backoff exponencial |
| `loguru` | Logs estruturados e legíveis |
| `pandas` | Manipulação dos dados antes do upsert |

### Execução esperada

```bash
# Coletar um time específico
python scraper/scraper.py --team Arsenal

# Coletar todos os 20 times
python scraper/scraper.py --all

# Coletar com limite de jogos customizado
python scraper/scraper.py --team Chelsea --last 10
```

---

## Fase 2 — Configuração do Projeto Supabase

### Contexto
Antes de implementar as DAGs do Airflow ou qualquer integração de dados, o projeto no Supabase precisa existir e suas credenciais precisam estar disponíveis localmente. Esta fase cobre **apenas** a criação do projeto e configuração das variáveis de ambiente — a criação das tabelas, RLS e integrações Python é feita na Fase 4.

### Passo a passo

#### 1. Criar o projeto no supabase.com

1. Acesse [supabase.com](https://supabase.com) e crie uma conta (gratuito)
2. Clique em **"New project"**
3. Preencha:
   - **Name:** `pl-analytics`
   - **Database Password:** gere uma senha forte e **salve-a** (você precisará dela para o Power BI)
   - **Region:** `West EU (Ireland)` ou `East US (North Virginia)` — o mais próximo
4. Aguarde ~2 minutos para o projeto ser provisionado

#### 2. Obter as credenciais

No painel do projeto → **Settings → API**:

| Variável | Onde encontrar | Uso |
|---|---|---|
| `SUPABASE_URL` | "Project URL" | Todos os clientes (Python, Next.js) |
| `SUPABASE_ANON_KEY` | chave "anon / public" | Frontend Next.js (leitura pública) |
| `SUPABASE_SERVICE_KEY` | chave "service_role" | Backend / Airflow — **nunca expor no browser** |

No painel → **Settings → Database → Connection string**:

| Item | Valor |
|---|---|
| Host | `db.<project-ref>.supabase.co` |
| Port | `5432` |
| User | `postgres` |
| Password | senha definida na criação |

> Anote o host e a senha — necessários para conectar o Power BI na Fase 5.

#### 3. Criar o `.env` na raiz do projeto

```bash
# .env  (nunca commite este arquivo)
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...
```

Certifique-se que `.env` está no `.gitignore`:

```
.env
*.env
```

#### 4. Instalar dependências Python

```bash
# No venv do projeto (já ativado)
pip install supabase python-dotenv
```

#### 5. Verificar a conexão

```python
# Execute no terminal Python para validar as credenciais
from dotenv import load_dotenv
from supabase import create_client
import os

load_dotenv()
client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

# Esperado: PostgrestAPIError "relation does not exist"
# → conexão OK, tabelas ainda não criadas (isso é correto nesta fase)
response = client.table("teams").select("*").execute()
print(response)
```

> Um erro `"relation \"public.teams\" does not exist"` confirma que a conexão foi bem-sucedida. As tabelas são criadas na Fase 4.

### TODOs

- [ ] Criar projeto `pl-analytics` no [supabase.com](https://supabase.com) (free tier: 500 MB banco + 1 GB storage)
- [ ] Copiar `SUPABASE_URL`, `SUPABASE_ANON_KEY` e `SUPABASE_SERVICE_KEY` para `.env` na raiz
- [ ] Garantir que `.env` está no `.gitignore`
- [ ] Instalar `supabase` e `python-dotenv`: `pip install supabase python-dotenv`
- [ ] Verificar conexão básica via Python (erro "relation does not exist" = sucesso)
- [ ] Anotar host e senha do banco para usar no Power BI (Fase 5)

---

## Fase 3 — Orquestração com Apache Airflow

### Contexto
Airflow é a ferramenta de orquestração mais cobrada em vagas DE no LinkedIn. Ter DAGs funcionais no portfolio é um diferencial real.

### Setup local

```bash
# docker-compose.yaml inclui: webserver, scheduler, worker, postgres, redis
docker compose up -d

# Acessar UI em http://localhost:8080
# Login padrão: airflow / airflow
```

### TODOs

- [ ] Criar `pipeline/docker-compose.yaml` baseado na imagem oficial `apache/airflow`
- [ ] Configurar Connections na UI do Airflow: Supabase (HTTP connection com service key) e Power BI (OAuth2)
- [ ] Configurar Variables: `PL_TEAMS_LIST`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `POWERBI_DATASET_ID`
- [ ] Implementar `dag_scraping_premier_league` (ver spec abaixo)
- [ ] Implementar `dag_refresh_powerbi` (ver spec abaixo)
- [ ] Configurar sensor de dependência entre as duas DAGs
- [ ] Configurar alertas de falha por e-mail ou Slack

### DAG 1 — `dag_scraping_premier_league`

```python
# Schedule: toda segunda-feira às 06:00 UTC
# Params: team_name (opcional) para trigger manual de time específico

scrape_all_teams >> validate_data >> upsert_to_supabase >> notify_success
```

| Task | Operador | Descrição |
|---|---|---|
| `scrape_all_teams` | `PythonOperator` | Chama `scraper.py --all` |
| `validate_data` | `PythonOperator` | Valida schema com Pydantic |
| `upsert_to_supabase` | `PythonOperator` | Upsert dos dados via `supabase-py` |
| `notify_success` | `EmailOperator` | Notifica conclusão com sumário |

### DAG 2 — `dag_refresh_powerbi`

```python
# Disparada após conclusão da dag_scraping OU via trigger manual (botão no portfolio)

check_new_data >> trigger_powerbi_refresh >> poll_refresh_status >> notify
```

| Task | Operador | Descrição |
|---|---|---|
| `check_new_data` | `PythonOperator` | Consulta Supabase para confirmar dados novos |
| `trigger_powerbi_refresh` | `HttpOperator` | POST `/datasets/{id}/refreshes` |
| `poll_refresh_status` | `PythonOperator` | Polling até refresh concluir |
| `notify` | `EmailOperator` | Notifica status final |

> **Dica de portfolio:** Documente no README como faria o deploy do Airflow em produção rodando em **Docker no próprio VPS da Hostinger**. Isso demonstra conhecimento de deploy real de pipelines DE.

---

## Fase 4 — Armazenamento com Supabase

### Contexto
Supabase é um backend-as-a-service open source construído sobre PostgreSQL. Oferece banco relacional, storage de arquivos, API REST gerada automaticamente e Realtime — tudo num único serviço com **free tier generoso**. O Power BI conecta diretamente via conector PostgreSQL nativo, sem ODBC ou drivers extras.

### Arquitetura no Supabase

```
Supabase Project: pl-analytics
├── Database (PostgreSQL)
│   ├── public.teams              # Cadastro dos 20 times com cores e badges
│   ├── public.matches            # Dados de partidas (fatos)
│   ├── public.match_stats        # Estatísticas detalhadas por partida
│   ├── public.player_match_stats # Estatísticas individuais de jogadores por partida
│   └── public.pipeline_runs      # Log de execuções (para status no botão do portfolio)
└── Storage
    └── bucket: raw-data          # JSONs brutos do scraper (auditoria/reprocessamento)
        └── sofascore/{team}/{date}.json
```

### TODOs — Banco de dados

- [ ] Executar `data/sql/001_schema.sql` (tabelas + índices)
- [ ] Executar `data/sql/002_rls_policies.sql` (RLS + políticas de leitura pública)
- [ ] Editar senha em `data/sql/003_powerbi_role.sql` → executar
- [ ] Configurar bucket `raw-data` no Supabase Storage: `python pipeline/scripts/setup_storage.py`
- [ ] Popular tabela `teams` com os 20 times: `python pipeline/scripts/seed_teams.py`

> **Como executar os arquivos SQL:**
> Opção A (manual): Supabase → SQL Editor → colar e executar cada arquivo em ordem
> Opção B (automatizado): `pip install psycopg2-binary && python pipeline/scripts/run_migrations.py`
> Para pular o role do Power BI: `python pipeline/scripts/run_migrations.py --skip 003_powerbi_role.sql`

### Schema SQL (alinhado aos modelos Pydantic)

> **Nota:** O schema foi atualizado para refletir os modelos reais `Match` e `MatchStats`.
> Os arquivos definitivos estão em `data/sql/`. O schema abaixo é apenas referência.

```sql
-- Ver data/sql/001_schema.sql para o schema completo e atualizado.
-- Resumo das tabelas:

-- public.teams         → id, name, slug, primary_color, secondary_color, badge_url
-- public.matches       → id, team_id, sofascore_event_id, match_date (timestamptz),
--                        home_team, away_team, home_team_id, away_team_id,
--                        score_home, score_away, score_home_ht, score_away_ht,
--                        competition, season, round_number, venue, venue_city,
--                        attendance, referee, result NOT NULL
-- public.match_stats   → id, match_id UNIQUE, possession_pct, shots_total,
--                        shots_on_target, shots_off_target, shots_blocked,
--                        big_chances, big_chances_missed, passes_total,
--                        passes_accurate, pass_accuracy_pct, long_balls_total,
--                        long_balls_accurate, tackles, interceptions, clearances,
--                        goalkeeper_saves, dribbles_attempted, dribbles_succeeded,
--                        ground_duels_total, ground_duels_won, aerial_duels_total,
--                        aerial_duels_won, corners, free_kicks, goal_kicks,
--                        throw_ins, offsides, fouls, yellow_cards, red_cards
-- public.pipeline_runs → id, dag_id, status, started_at, finished_at, details jsonb
```

### Estatísticas de Jogadores por Partida ✅

A tabela `player_match_stats` armazena stats individuais de cada jogador em cada partida, extraídos do endpoint `/event/{eid}/lineups` (já chamado pelo scraper — nenhuma chamada adicional necessária).

**Schema:** `data/sql/004_player_stats.sql`

| Campo | Tipo | Descrição |
|---|---|---|
| `sofascore_player_id` | int | ID do jogador no SofaScore |
| `player_name` | text | Nome do jogador |
| `team_side` | text | `'home'` ou `'away'` |
| `is_starter` | bool | Titular ou substituto |
| `captain` | bool | Capitão |
| `shirt_number` | int | Número da camisa |
| `position` | text | G / D / M / F |
| `minutes_played` | int | Minutos jogados |
| `rating` | numeric(4,2) | Nota SofaScore (ex: 7.84) |
| `goals` | int | Gols |
| `assists` | int | Assistências |
| `shots_total` | int | Total de chutes |
| `shots_on_target` | int | Chutes no gol |
| `expected_goals` | numeric(5,3) | xG |
| `expected_assists` | numeric(5,3) | xA |
| `passes_total` | int | Total de passes |
| `passes_accurate` | int | Passes certos |
| `key_passes` | int | Passes-chave |
| `tackles` | int | Desarmes |
| `interceptions` | int | Interceptações |
| `dribbles_won` | int | Dribles certos |
| `aerial_duels_won` | int | Duelos aéreos ganhos |
| `aerial_duels_lost` | int | Duelos aéreos perdidos |
| `fouls_committed` | int | Faltas cometidas |
| `yellow_cards` | int | Cartões amarelos |
| `red_cards` | int | Cartões vermelhos |
| `saves` | int | Defesas (goleiros) |

**Para ativar:** executar `data/sql/004_player_stats.sql` no SQL Editor do Supabase.

### TODOs — Integração Python com Supabase ✅

- [x] `supabase-py` instalado
- [x] `pipeline/supabase_client.py` criado com cliente via variáveis de ambiente
- [x] `upsert_teams(teams)`, `upsert_matches(slug, matches)` implementados
- [x] Upload do JSON bruto para Supabase Storage implementado
- [x] `log_pipeline_run` e `update_pipeline_run` implementados

```python
# pipeline/supabase_client.py
from supabase import create_client
import os

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)

def upsert_matches(matches: list[dict]) -> None:
    supabase.table("matches").upsert(
        matches,
        on_conflict="team_id,match_date,home_team,away_team"
    ).execute()

def upload_raw_json(team: str, date: str, content: bytes) -> None:
    path = f"sofascore/{team}/{date}.json"
    supabase.storage.from_("raw-data").upload(path, content, {"upsert": "true"})

def log_pipeline_run(dag_id: str, status: str, details: dict = None) -> str:
    result = supabase.table("pipeline_runs").insert({
        "dag_id": dag_id,
        "status": status,
        "details": details or {}
    }).execute()
    return result.data[0]["id"]
```

### Conectar Power BI ao Supabase

O Power BI se conecta ao Supabase via **conector PostgreSQL nativo** — sem ODBC adicional.

- [ ] Power BI Desktop → Obter Dados → Banco de Dados PostgreSQL
- [ ] Host: `db.<project-ref>.supabase.co` | Porta: `5432`
- [ ] Usuário: `powerbi_reader` | Senha: a definida no SQL acima
- [ ] Importar: `teams`, `matches`, `match_stats`

---

## Fase 5 — Dashboard Power BI

### Contexto
Ao selecionar um time, **cores, ícones e elementos visuais mudam automaticamente** para refletir a identidade do clube.

### Modelo de dados (Star Schema)

```
fMatches  ──→  dTeams
    │              (name, primary_color, secondary_color, badge_url)
    └──→  dCalendar
fMatchStats ──→ fMatches
```

### TODOs — Modelagem

- [ ] Conectar ao Supabase via PostgreSQL connector
- [ ] Importar tabelas `teams`, `matches`, `match_stats`
- [ ] Criar `dCalendar` com `CALENDAR()` DAX
- [ ] Configurar relacionamentos no modelo estrela
- [ ] Publicar dataset no Power BI Service

### TODOs — Medidas DAX principais

```dax
Total Gols Marcados = CALCULATE(SUM(fMatches[score_team]))
Total Gols Sofridos = CALCULATE(SUM(fMatches[score_opponent]))
Aproveitamento %    = DIVIDE([Pontos Ganhos], [Pontos Possíveis])
Cor Primária        = SELECTEDVALUE(dTeams[primary_color], "#1B3A6B")
Badge URL           = SELECTEDVALUE(dTeams[badge_url])
```

- [ ] Conditional formatting com medidas de cor em cartões e barras
- [ ] Visual de imagem com URL dinâmica do badge
- [ ] Slicer de time com dropdown
- [ ] Criar template de tema JSON base

### Visuais sugeridos

| Visual | Conteúdo |
|---|---|
| Cards KPI | Vitórias / Empates / Derrotas / Aproveitamento % |
| Barras | Gols marcados vs. sofridos por jogo |
| Linha | Aproveitamento acumulado na temporada |
| Tabela estilizada | Últimos 10 jogos: data, adversário, placar, resultado |
| Radar (Deneb) | Stats comparativas: posse, chutes, passes |
| Mapa de calor | Performance por rodada / dia da semana |

---

## Fase 6 — Atualização via API (Power BI + Airflow)

### Arquitetura do fluxo

```
[Automático]
dag_scraping conclui → dag_refresh_powerbi → Power BI REST API
                                ↓
                    pipeline_runs no Supabase (log de status)

[Manual via portfolio]
Botão no site → Next.js API Route → Airflow REST API → dag_refresh_powerbi
                                                              ↓
                                                  Supabase Realtime notifica o frontend
```

### TODOs — Power BI REST API

- [ ] Registrar app no **Azure Active Directory** (App Registration)
- [ ] Conceder permissões: `Dataset.ReadWrite.All`, `Report.Read.All`
- [ ] Salvar `CLIENT_ID` e `CLIENT_SECRET` em variáveis de ambiente
- [ ] Implementar autenticação OAuth2 `client_credentials`
- [ ] Criar função `power_bi_refresh(dataset_id)` com polling

```python
def power_bi_refresh(dataset_id: str) -> bool:
    token = get_oauth_token()
    requests.post(
        f"https://api.powerbi.com/v1.0/myorg/datasets/{dataset_id}/refreshes",
        headers={"Authorization": f"Bearer {token}"}
    )
    for _ in range(30):
        if check_refresh_status(dataset_id, token) == "Completed":
            return True
        time.sleep(60)
    return False
```

### TODOs — Airflow REST API (trigger externo)

- [ ] Habilitar Airflow REST API (Airflow 2.0+)
- [ ] Next.js API Route `POST /api/trigger-update` → chama `POST /api/v1/dags/{dag_id}/dagRuns`
- [ ] Gravar `run_id` na tabela `pipeline_runs` do Supabase
- [ ] Frontend escuta mudanças em `pipeline_runs` via **Supabase Realtime** (sem polling manual)

> ⚠️ **Segurança:** NUNCA commite secrets no GitHub. Use `.env` + `.gitignore`. No servidor Hostinger, configure as variáveis via arquivo `.env` fora do repositório.

---

## Fase 7 — Portfolio Web

### Stack

| Item | Tecnologia |
|---|---|
| Framework | Next.js 14 (App Router) |
| Styling | Tailwind CSS |
| Componentes | shadcn/ui |
| Animações | Framer Motion |
| Dados | `@supabase/supabase-js` |
| Power BI | `powerbi-client-react` |
| Hosting | **Hostinger VPS** (KVM1 ou KVM2) |
| Processo | **PM2** (gerenciador Node.js) |
| Proxy | **Nginx** (porta 80/443 → 3000) |
| SSL | **Certbot** + Let's Encrypt (gratuito) |

### Arquitetura de deploy no Hostinger

```
GitHub push na main
      ↓
GitHub Actions (deploy.yml)
      ↓ SSH
Hostinger VPS (Ubuntu 22.04)
      ├── Nginx :80/:443  →  proxy_pass localhost:3000
      ├── PM2             →  mantém Next.js no ar (restart automático)
      ├── Next.js :3000   →  app em produção
      └── Certbot         →  SSL automático via Let's Encrypt
```

### TODOs — Configuração do servidor Hostinger

- [ ] Contratar plano VPS Hostinger (KVM 1 ou 2 — Ubuntu 22.04 LTS)
- [ ] Configurar acesso SSH com chave pública (desabilitar login por senha)
- [ ] Instalar `nvm` → Node.js LTS → `npm install -g pm2`
- [ ] Instalar e configurar Nginx como proxy reverso
- [ ] Instalar Certbot e configurar SSL com domínio

```nginx
# /etc/nginx/sites-available/portfolio
server {
    listen 80;
    server_name seudominio.com www.seudominio.com;

    location / {
        proxy_pass         http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection 'upgrade';
        proxy_set_header   Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
```

```bash
# Iniciar o Next.js com PM2
pm2 start npm --name "portfolio" -- start
pm2 save && pm2 startup
```

### TODOs — CI/CD GitHub Actions → Hostinger

- [ ] Adicionar secrets no GitHub: `HOST`, `USERNAME`, `SSH_KEY`
- [ ] Criar `.github/workflows/deploy.yml`

```yaml
name: Deploy to Hostinger

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.HOST }}
          username: ${{ secrets.USERNAME }}
          key: ${{ secrets.SSH_KEY }}
          script: |
            cd /var/www/portfolio
            git pull origin main
            npm ci --production
            npm run build
            pm2 restart portfolio
```

### TODOs — Next.js

- [ ] `npx create-next-app@latest portfolio --typescript --tailwind`
- [ ] `npm install @supabase/supabase-js powerbi-client-react powerbi-client framer-motion`
- [ ] `npx shadcn-ui@latest init`

### TODOs — Leitura de dados com Supabase

```typescript
// lib/supabase.ts
import { createClient } from '@supabase/supabase-js';

export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!   // chave pública — segura no frontend
);

// Buscar últimas partidas de um time
const { data } = await supabase
  .from('matches')
  .select('*, match_stats(*), teams(name, primary_color, badge_url)')
  .eq('teams.slug', 'arsenal')
  .order('match_date', { ascending: false })
  .limit(10);

// Status da última execução do pipeline
const { data: lastRun } = await supabase
  .from('pipeline_runs')
  .select('*')
  .order('started_at', { ascending: false })
  .limit(1)
  .single();
```

### TODOs — Embed Power BI

```typescript
// app/api/powerbi-token/route.ts  ← server-side, nunca expor Client Secret no cliente
export async function GET() {
  const token = await generateEmbedToken({
    clientId: process.env.AZURE_CLIENT_ID!,
    clientSecret: process.env.AZURE_CLIENT_SECRET!,
    tenantId: process.env.AZURE_TENANT_ID!,
    reportId: process.env.POWERBI_REPORT_ID!,
    datasetId: process.env.POWERBI_DATASET_ID!,
  });
  return Response.json({ token, embedUrl: token.embedUrl });
}
```

```typescript
// components/PowerBIDashboard.tsx
import { PowerBIEmbed } from 'powerbi-client-react';
import { models } from 'powerbi-client';

export function PowerBIDashboard({ embedToken, embedUrl, reportId }) {
  return (
    <PowerBIEmbed
      embedConfig={{
        type: 'report',
        id: reportId,
        embedUrl,
        accessToken: embedToken,
        tokenType: models.TokenType.Embed,
        settings: { navContentPaneEnabled: false }
      }}
      cssClassName="powerbi-report"
    />
  );
}
```

### TODOs — Botão de atualização com Supabase Realtime

```typescript
// Supabase Realtime — escuta mudanças em pipeline_runs sem polling manual
supabase
  .channel('pipeline-status')
  .on('postgres_changes', {
    event: '*',
    schema: 'public',
    table: 'pipeline_runs'
  }, (payload) => {
    setStatus(payload.new.status);   // atualiza a UI automaticamente
    setLastRun(payload.new.finished_at);
  })
  .subscribe();
```

- [ ] Componente `<UpdateButton />` com estados: idle / loading / success / error
- [ ] `POST /api/trigger-update` → chama Airflow REST API
- [ ] Supabase Realtime escutando `pipeline_runs` para feedback automático na UI

### Variáveis de ambiente

```bash
# .env.example

# Supabase
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=        # chave pública (safe para o frontend)
SUPABASE_SERVICE_KEY=                 # chave privada (só no servidor / Airflow)

# Azure / Power BI
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
AZURE_TENANT_ID=
POWERBI_WORKSPACE_ID=
POWERBI_REPORT_ID=
POWERBI_DATASET_ID=

# Airflow
AIRFLOW_BASE_URL=
AIRFLOW_USERNAME=
AIRFLOW_PASSWORD=
```

---

## Fase 8 — Data Science (Projeto Futuro)

### Modelos de ML recomendados

| Modelo | Tipo | Target |
|---|---|---|
| Previsão de resultado | Classificação multiclasse | W / D / L |
| Previsão de gols | Regressão | Total de gols |
| Clustering de times | Não supervisionado | Grupos por estilo de jogo |
| Série temporal | Séries temporais | Aproveitamento futuro |

### Stack

| Biblioteca | Uso |
|---|---|
| `scikit-learn` | Modelos clássicos |
| `xgboost` / `lightgbm` | Gradient Boosting — SOTA para dados tabulares |
| `mlflow` | Tracking de experimentos e modelos |
| `shap` | Explicabilidade (XAI) |
| `optuna` | Hyperparameter tuning |
| `plotly` / `seaborn` | Visualizações para EDA |

### TODOs (quando chegar nesta fase)

- [ ] Buscar dados do Supabase nos notebooks via `supabase-py`
- [ ] EDA: distribuição de resultados, correlações, features
- [ ] Feature engineering: forma recente, confronto direto histórico
- [ ] Baseline + comparação de modelos com MLflow
- [ ] SHAP values para explicabilidade
- [ ] Seção "Data Science" no portfolio com métricas e feature importance
- [ ] (Opcional) Deploy do modelo como Streamlit app no mesmo VPS Hostinger

---

## README.md — Elementos obrigatórios

```markdown
# ⚽ Premier League Analytics

[![CI/CD](badge)] [![Python](badge)] [![Airflow](badge)] [![Supabase](badge)] [![Power BI](badge)]

> Pipeline end-to-end: Playwright → Airflow → Supabase → Power BI → Next.js

[GIF ou screenshot do dashboard funcionando]

## Arquitetura
[diagrama do pipeline]

## Stack
[logos das tecnologias]

## Setup rápido
\`\`\`bash
git clone ...
cp .env.example .env        # preencher variáveis
docker compose up -d        # sobe Airflow
cd portfolio && npm install && npm run dev
\`\`\`

## Links
- [Portfolio ao vivo](https://seudominio.com)
- [Dashboard Power BI](link)
```

---

## Checklist Geral

### Fase 1 — Extração ✅
- [x] `teams.yaml` com 20 times e slugs do SofaScore
- [x] Scraper refatorado (`--team` e `--all`)
- [x] Retry logic + rate limiting
- [x] Validação com Pydantic
- [x] Logs com `loguru`
- [x] Testes com `pytest`

### Fase 2 — Supabase (Projeto)
- [ ] Projeto `pl-analytics` criado no supabase.com
- [ ] `.env` com `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`
- [ ] `.env` no `.gitignore`
- [ ] `supabase` e `python-dotenv` instalados
- [ ] Conexão básica verificada via Python

### Fase 3 — Airflow ✅
- [x] Docker Compose com Airflow (LocalExecutor + Dockerfile customizado com Playwright)
- [x] `dag_scraping_premier_league` (com `upsert_to_supabase`)
- [x] `dag_refresh_powerbi` (stub — implementar na Fase 6)
- [ ] Connections configuradas (Supabase + Power BI) — via variáveis de ambiente no docker-compose
- [ ] Alertas de falha

### Fase 4 — Supabase (Armazenamento)
- [x] Executar `data/sql/001_schema.sql` (tabelas + índices)
- [x] Executar `data/sql/002_rls_policies.sql` (RLS + políticas)
- [x] Editar senha + executar `data/sql/003_powerbi_role.sql`
- [x] `python pipeline/scripts/setup_storage.py` (bucket raw-data)
- [x] `python pipeline/scripts/seed_teams.py` (20 times na tabela teams)
- [x] `pipeline/supabase_client.py` implementado
- [ ] Executar `data/sql/004_player_stats.sql` (tabela player_match_stats)
- [x] Scraper extrai player stats do endpoint lineups (sem nova chamada de API)
- [x] `upsert_matches()` inclui upsert de `player_match_stats` automaticamente

### Fase 5 — Power BI
- [ ] Conexão PostgreSQL ao Supabase configurada
- [ ] Star schema (dTeams, fMatches, dCalendar)
- [ ] Medidas DAX principais
- [ ] Cores e badge dinâmicos por time
- [ ] Publicado no Power BI Service

### Fase 6 — API
- [ ] App Registration no Azure AD
- [ ] Função `power_bi_refresh()` implementada
- [ ] DAG de refresh no Airflow
- [ ] Airflow REST API habilitada
- [ ] Log de execuções gravado no Supabase (`pipeline_runs`)

### Fase 7 — Portfolio Web
- [ ] Next.js + Tailwind + shadcn/ui
- [ ] Integração `@supabase/supabase-js`
- [ ] Embed Power BI funcionando
- [ ] Botão de atualização com Supabase Realtime
- [ ] VPS Hostinger: Node.js + PM2 + Nginx + SSL
- [ ] CI/CD via GitHub Actions → deploy automático no Hostinger
- [ ] `.env.example` documentado

### GitHub
- [ ] Estrutura de monorepo organizada
- [ ] README com badges, diagrama e screenshot
- [ ] CI/CD com GitHub Actions
- [ ] `.env.example` completo
- [ ] `.gitignore` cobrindo `.env`, `__pycache__`, `node_modules`

---

## Timeline Sugerida

| Semana | Fase | Entregável |
|---|---|---|
| 1–2 | Fase 1 ✅ | Scraper funcionando para os 20 times |
| 2 | Fase 2 | Projeto Supabase criado e `.env` configurado |
| 3 | Fase 3 | Airflow local com `dag_scraping` funcional |
| 4 | Fase 4 | Dados chegando no Supabase via pipeline |
| 5–6 | Fase 5 | Dashboard Power BI com tema dinâmico publicado |
| 7 | Fase 6 | Refresh automático via API funcionando |
| 8–9 | Fase 7 | Portfolio ao vivo no Hostinger com embed e botão de update |
| 10+ | Fase 8 | EDA e primeiros modelos de ML |

---

*Premier League Analytics — portfolio end-to-end de engenharia e análise de dados*
