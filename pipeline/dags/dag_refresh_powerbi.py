"""
DAG: dag_refresh_powerbi
Status: STUB — implementar na Fase 6 quando Azure AD estiver configurado.

Flow planejado (Fase 6):
    check_new_data → trigger_powerbi_refresh → poll_refresh_status → notify

Trigger: manual (via botão no portfolio) ou após conclusão de dag_scraping_premier_league.
"""

from __future__ import annotations

from datetime import datetime, timezone

from airflow.decorators import dag, task
from airflow.operators.empty import EmptyOperator

DAG_ID = "dag_refresh_powerbi"


@dag(
    dag_id=DAG_ID,
    description="[STUB] Trigger Power BI dataset refresh via REST API — implement in Fase 6",
    schedule=None,   # triggered manually or via TriggerDagRunOperator
    start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["powerbi", "stub"],
)
def dag_refresh_powerbi():
    # TODO (Fase 6): replace with real tasks
    # check_new_data → trigger_powerbi_refresh → poll_refresh_status → notify
    EmptyOperator(task_id="stub_placeholder")


dag_refresh_powerbi()
