-- =============================================================================
-- Premier League Analytics — Estatísticas de jogadores por partida
-- Executar APÓS 001_schema.sql
-- =============================================================================

create table if not exists public.player_match_stats (
  id                    uuid primary key default gen_random_uuid(),
  match_id              uuid references public.matches(id) on delete cascade,

  -- Identificação do jogador
  sofascore_player_id   int not null,
  player_name           text not null,
  team_side             text check (team_side in ('home', 'away')),
  is_starter            bool default false,
  captain               bool default false,
  shirt_number          int,
  position              text,           -- G, D, M, F

  -- Tempo de jogo e nota
  minutes_played        int,
  rating                numeric(4,2),   -- SofaScore rating (ex: 7.84)

  -- Ataque
  goals                 int,
  assists               int,
  shots_total           int,
  shots_on_target       int,
  expected_goals        numeric(5,3),   -- xG
  expected_assists      numeric(5,3),   -- xA

  -- Passes
  passes_total          int,
  passes_accurate       int,
  key_passes            int,

  -- Defesa / duelos
  tackles               int,
  interceptions         int,
  dribbles_won          int,
  aerial_duels_won      int,
  aerial_duels_lost     int,

  -- Disciplina
  fouls_committed       int,
  yellow_cards          int,
  red_cards             int,

  -- Goleiro
  saves                 int,

  -- Deduplicação: um registro por jogador por partida
  unique (match_id, sofascore_player_id)
);

create index if not exists idx_player_match_stats_match_id
  on public.player_match_stats(match_id);

create index if not exists idx_player_match_stats_player_id
  on public.player_match_stats(sofascore_player_id);

-- ---------------------------------------------------------------------------
-- RLS — adicionar após executar 002_rls_policies.sql
-- ---------------------------------------------------------------------------
alter table public.player_match_stats enable row level security;

create policy "public read player_match_stats"
  on public.player_match_stats for select using (true);

-- ---------------------------------------------------------------------------
-- Power BI reader — adicionar SELECT nesta tabela
-- ---------------------------------------------------------------------------
-- Se o role powerbi_reader já existe, rode esta linha separadamente:
-- grant select on public.player_match_stats to powerbi_reader;
-- ---------------------------------------------------------------------------
