-- =============================================================================
-- World Cup Analytics — Schema SQL
-- Alinhado aos modelos Pydantic: Match, MatchStats (scraper/models.py)
--
-- Como executar:
--   Opção A (recomendado): Supabase → SQL Editor → colar e executar
--   Opção B (automatizado): python pipeline/scripts/run_migrations.py
-- =============================================================================

-- ---------------------------------------------------------------------------
-- teams: cadastro das seleções nacionais
-- ---------------------------------------------------------------------------
create table if not exists public.teams (
  id              serial primary key,
  name            text not null unique,
  slug            text not null unique,        -- ex: "brazil", "argentina"
  primary_color   text not null default '#1B3A6B',
  secondary_color text not null default '#FFFFFF',
  badge_url       text,                        -- URL do escudo (popular manualmente ou via API)
  created_at      timestamptz default now()
);

-- ---------------------------------------------------------------------------
-- matches: tabela de fatos — uma linha por (time analisado × partida)
-- Nota: match_date é timestamptz (não date) para preservar horário do jogo
-- ---------------------------------------------------------------------------
create table if not exists public.matches (
  id                  uuid primary key default gen_random_uuid(),
  team_id             int references public.teams(id) on delete cascade,

  -- Identificador único do evento no SofaScore (para auditoria e deduplicação)
  sofascore_event_id  bigint unique,

  -- Dados da partida
  match_date          timestamptz not null,
  home_team           text not null,
  away_team           text not null,
  home_team_id        int,                    -- ID do time mandante no SofaScore
  away_team_id        int,                    -- ID do time visitante no SofaScore
  score_home          int not null,
  score_away          int not null,
  score_home_ht       int,                    -- placar no intervalo (mandante)
  score_away_ht       int,                    -- placar no intervalo (visitante)

  -- Contexto da partida
  competition         text not null,
  season              text,                   -- ex: "World Cup 2026"
  round_number        int,                    -- rodada
  venue               text,
  venue_city          text,
  attendance          int,
  referee             text,

  -- Resultado do ponto de vista do time analisado (team_id)
  result              text not null check (result in ('W', 'D', 'L')),

  scraped_at          timestamptz default now(),

  -- Constraint de unicidade para upsert idempotente
  unique (team_id, match_date, home_team, away_team)
);

-- ---------------------------------------------------------------------------
-- match_stats: estatísticas detalhadas por partida
-- Uma linha por match_id (relação 1:1 com matches)
-- ---------------------------------------------------------------------------
create table if not exists public.match_stats (
  id      uuid primary key default gen_random_uuid(),
  match_id uuid references public.matches(id) on delete cascade unique,

  -- Possession
  possession_pct        numeric(5,2),

  -- Shooting
  shots_total           int,
  shots_on_target       int,
  shots_off_target      int,
  shots_blocked         int,
  big_chances           int,
  big_chances_missed    int,

  -- Passing
  passes_total          int,
  passes_accurate       int,
  pass_accuracy_pct     numeric(5,2),
  long_balls_total      int,
  long_balls_accurate   int,

  -- Defending
  tackles               int,
  interceptions         int,
  clearances            int,
  goalkeeper_saves      int,

  -- Duels
  dribbles_attempted    int,
  dribbles_succeeded    int,
  ground_duels_total    int,
  ground_duels_won      int,
  aerial_duels_total    int,
  aerial_duels_won      int,

  -- Set pieces & fouls
  corners               int,
  free_kicks            int,
  goal_kicks            int,
  throw_ins             int,
  offsides              int,
  fouls                 int,
  yellow_cards          int,
  red_cards             int
);

-- ---------------------------------------------------------------------------
-- Indexes para performance
-- ---------------------------------------------------------------------------
create index if not exists idx_matches_team_id    on public.matches(team_id);
create index if not exists idx_matches_match_date on public.matches(match_date desc);
create index if not exists idx_matches_event_id   on public.matches(sofascore_event_id);
