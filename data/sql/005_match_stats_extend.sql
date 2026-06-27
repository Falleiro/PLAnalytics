-- =============================================================================
-- World Cup Analytics — Extensão de features (jun/2026)
-- Executar APÓS 001/004. Idempotente (ADD COLUMN IF NOT EXISTS).
--
-- Motivação (ver Docs/03): capturar o que o SofaScore já fornece e estávamos
-- descartando — em especial o xG de time (preditor nº 1 da literatura), as
-- estatísticas do VISITANTE (antes só salvávamos o mandante) e o ranking FIFA
-- das duas seleções (embutido no payload do evento).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- matches: ranking (tipo FIFA) das duas seleções, vindo de event.homeTeam.ranking
-- ---------------------------------------------------------------------------
alter table public.matches add column if not exists home_team_ranking int;
alter table public.matches add column if not exists away_team_ranking int;

-- ---------------------------------------------------------------------------
-- match_stats:
--   (a) novos campos de alto valor (xG, goleiro, ataque em profundidade)
--   (b) versões "_against" = mesmo stat do VISITANTE (antes inexistente)
-- Convenção: <campo> = mandante; <campo>_against = visitante.
-- ---------------------------------------------------------------------------

-- (a) novos — mandante
alter table public.match_stats add column if not exists expected_goals       numeric(5,3);
alter table public.match_stats add column if not exists goals_prevented      numeric(6,3);
alter table public.match_stats add column if not exists big_chances_scored   int;
alter table public.match_stats add column if not exists touches_in_box       int;
alter table public.match_stats add column if not exists final_third_entries  int;
alter table public.match_stats add column if not exists recoveries           int;

-- (b) versões do visitante (_against) — alto valor
alter table public.match_stats add column if not exists expected_goals_against      numeric(5,3);
alter table public.match_stats add column if not exists goals_prevented_against     numeric(6,3);
alter table public.match_stats add column if not exists possession_pct_against      numeric(5,2);
alter table public.match_stats add column if not exists shots_total_against         int;
alter table public.match_stats add column if not exists shots_on_target_against     int;
alter table public.match_stats add column if not exists big_chances_against         int;
alter table public.match_stats add column if not exists big_chances_scored_against  int;
alter table public.match_stats add column if not exists passes_total_against        int;
alter table public.match_stats add column if not exists pass_accuracy_pct_against   numeric(5,2);
alter table public.match_stats add column if not exists touches_in_box_against      int;
alter table public.match_stats add column if not exists final_third_entries_against int;
alter table public.match_stats add column if not exists recoveries_against          int;
alter table public.match_stats add column if not exists corners_against             int;
alter table public.match_stats add column if not exists tackles_against             int;
alter table public.match_stats add column if not exists interceptions_against       int;
