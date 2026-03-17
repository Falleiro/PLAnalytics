-- =============================================================================
-- Premier League Analytics — Role somente leitura para Power BI
-- Executar APÓS 001_schema.sql
--
-- ATENÇÃO: Substitua 'TROQUE-ESTA-SENHA' por uma senha forte antes de executar.
--          Salve a senha no seu gerenciador de senhas — você precisará dela ao
--          configurar a conexão PostgreSQL no Power BI Desktop (Fase 5).
-- =============================================================================

-- Criar role com login
create role powerbi_reader with login password 'TROQUE-ESTA-SENHA';

-- Permissões de conexão e schema
grant connect on database postgres to powerbi_reader;
grant usage on schema public to powerbi_reader;

-- Permissões de leitura nas tabelas relevantes para o dashboard
grant select on public.teams        to powerbi_reader;
grant select on public.matches      to powerbi_reader;
grant select on public.match_stats  to powerbi_reader;
-- pipeline_runs não é necessário no Power BI

-- ---------------------------------------------------------------------------
-- Dados de conexão para o Power BI Desktop (Fase 5):
--   Host:     DB_HOST do .env  (ex: db.rmbodglluhlvfhbkfoie.supabase.co)
--   Port:     5432
--   Database: postgres
--   User:     powerbi_reader
--   Password: [a senha definida acima]
--   SSL:      Required
-- ---------------------------------------------------------------------------
