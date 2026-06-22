-- =============================================================================
-- World Cup Analytics — Row Level Security (RLS) e Políticas
-- Executar APÓS 001_schema.sql
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Habilitar RLS em todas as tabelas
-- (sem RLS, qualquer chave anon tem acesso total — não recomendado)
-- ---------------------------------------------------------------------------
alter table public.teams          enable row level security;
alter table public.matches        enable row level security;
alter table public.match_stats    enable row level security;
alter table public.pipeline_runs  enable row level security;

-- ---------------------------------------------------------------------------
-- Política de leitura pública (SELECT) para usuários anônimos
-- Permite que qualquer usuário leia as tabelas sem autenticação (ex.: leitura
-- via ANON_KEY em notebooks/análises).
-- ---------------------------------------------------------------------------
create policy "public read teams"
  on public.teams for select
  using (true);

create policy "public read matches"
  on public.matches for select
  using (true);

create policy "public read match_stats"
  on public.match_stats for select
  using (true);

create policy "public read pipeline_runs"
  on public.pipeline_runs for select
  using (true);

-- ---------------------------------------------------------------------------
-- Notas:
-- - INSERT/UPDATE/DELETE são feitos exclusivamente via SERVICE_KEY (scripts de
--   carga manual), que bypassa o RLS por padrão — não é necessária política de
--   escrita.
-- ---------------------------------------------------------------------------
