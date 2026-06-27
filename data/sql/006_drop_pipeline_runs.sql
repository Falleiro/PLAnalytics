-- =============================================================================
-- World Cup Analytics — Remoção da tabela legada pipeline_runs
-- =============================================================================
-- pipeline_runs foi criada para a orquestração (Airflow + frontend), que saiu
-- de escopo na migração para Copa do Mundo. Sem consumidor no pipeline manual.
-- Esta migration remove a tabela de bancos já existentes; 001/002 já não a criam.
-- =============================================================================

drop table if exists public.pipeline_runs cascade;
