# Fase 1 — Entendimento do Negócio (Business Understanding)

> **Metodologia:** CRISP-DM (*Cross-Industry Standard Process for Data Mining*)
> **Projeto:** World Cup Analytics — Previsão de resultados de jogos da Copa do Mundo
> **Autor:** Caio Falleiro
> **Data:** Junho/2026

---

## 0. Contexto

A Copa do Mundo FIFA 2026 (Estados Unidos, Canadá e México) é a primeira edição
com 48 seleções e 104 partidas, o que amplia tanto o volume de dados disponíveis
quanto o interesse do público em previsões. Casas de aposta, torcedores e a mídia
esportiva consomem cada vez mais análises preditivas baseadas em dados objetivos
de desempenho — e não apenas em opinião.

Este projeto é um **trabalho de ciência de dados** de escopo enxuto e execução
**manual** (sem orquestração automatizada): coleta de dados via scraping do
SofaScore, armazenamento em Supabase/PostgreSQL, modelagem preditiva em notebooks
de ML e **entrega final em uma planilha Excel** com as previsões dos confrontos.

---

## 1. Determinar os Objetivos de Negócio

### 1.1. Background (Situação atual)

Hoje, a previsão de resultados de jogos de futebol é feita, na maioria dos casos,
de forma **subjetiva** — por palpites de comentaristas, "favoritismo" histórico
ou rankings estáticos (como o ranking FIFA), que não capturam a **forma recente**
de cada seleção. Não existe, para o torcedor comum, uma ferramenta acessível que:

- consolide o **desempenho objetivo recente** de cada seleção (gols, finalizações,
  posse, expected goals, etc.);
- transforme esses dados em uma **estimativa de probabilidade** de vitória, empate
  ou derrota para um confronto específico;
- apresente isso de forma **visual e interpretável**.

### 1.2. Objetivos de Negócio

> *O que o "cliente" (torcedor / analista esportivo / leitor do portfólio) quer
> alcançar do ponto de vista do negócio.*

1. **Antecipar o resultado** de partidas da Copa do Mundo (vitória da seleção A,
   empate ou vitória da seleção B) com base no desempenho recente das seleções.
2. **Quantificar a incerteza** do confronto — não apenas "quem ganha", mas
   *com qual probabilidade*, dando ao usuário uma noção de quão equilibrado é o jogo.
3. **Identificar os fatores** que mais influenciam o resultado (ex.: a posse de
   bola importa mais que o número de finalizações? o aproveitamento defensivo
   pesa mais que o ataque?).
4. **Entregar as previsões** em uma planilha Excel organizada, com os confrontos,
   as probabilidades de cada resultado e o resultado mais provável.

### 1.3. Critérios de Sucesso de Negócio

> *Como saberemos, em termos de negócio, que o projeto foi bem-sucedido.*

- O sistema consegue gerar uma previsão para **qualquer confronto** entre duas
  seleções presentes na base, de forma automática.
- As previsões são **mais acertivas que o palpite ingênuo** (ex.: "sempre vence
  quem tem melhor ranking FIFA" ou "sempre vence o mandante").
- Um usuário leigo consegue **entender a previsão e a confiança** associada em
  menos de 30 segundos olhando a planilha de previsões.
- O projeto é **reproduzível**: rodando os scripts/notebooks manualmente, a base é
  recoletada e as previsões são regeradas de forma consistente.

---

## 2. Avaliar a Situação

### 2.1. Inventário de Recursos

**Dados**
- API pública do **SofaScore** (acessada via scraping com Patchright): histórico
  de jogos por seleção, estatísticas por partida (posse, finalizações, passes,
  cartões), escalações e estatísticas individuais de jogadores (rating, xG, xA).

**Infraestrutura / Tecnologia**
- Scraper em Python (Patchright + Pydantic + Tenacity + Loguru) — reaproveitado da
  versão Premier League.
- Banco **Supabase/PostgreSQL** para armazenar os dados coletados (carga manual).
- **Notebooks Python** (pandas + scikit-learn) para EDA, features e modelagem.
- **Excel** como formato de entrega final das previsões.

> **Fora de escopo (intencionalmente):** este é um projeto enxuto e de execução
> manual. Não há orquestração de pipeline (Airflow), agendamento/cron, dashboards
> em Power BI nem portfólio web em Next.js. Cada etapa é rodada sob demanda.

**Conhecimento**
- Domínio do autor sobre futebol e sobre o stack de dados.
- Metodologia CRISP-DM como guia de processo.

### 2.2. Requisitos, Premissas e Restrições

**Requisitos**
- A previsão deve sair **antes** do jogo (usar apenas dados disponíveis
  pré-partida — nada de "vazamento" de dados do próprio jogo a prever).
- As etapas devem ser **reproduzíveis** rodando os scripts/notebooks manualmente,
  com a saída final consolidada em **Excel**.

**Premissas**
- O **desempenho recente** de uma seleção é um indicador razoável do seu
  desempenho futuro de curto prazo.
- As estatísticas do SofaScore são suficientemente **completas e confiáveis**.
- A forma demonstrada em jogos recentes (incluindo amistosos e eliminatórias)
  é transferível para o contexto de Copa do Mundo.

**Restrições**
- Seleções jogam **poucas partidas oficiais** por ano → volume de dados por
  seleção é menor que o de clubes (importante para a modelagem).
- O scraping depende da **estabilidade da API** não-oficial do SofaScore.
- Sem dados de mando de campo "real" (na Copa, a maioria dos jogos é em campo
  neutro) — o fator "mandante" perde força em relação a ligas nacionais.

### 2.3. Riscos e Contingências

| Risco | Impacto | Contingência |
|-------|---------|--------------|
| Poucos jogos por seleção → modelo com pouca amostra | Alto | Engenharia de features agregadas; incluir amistosos/eliminatórias; usar features de "média móvel" |
| Mudança/bloqueio da API do SofaScore | Alto | Patchright (anti-detecção) + retries; armazenar JSON bruto no Storage para reprocessar |
| Desbalanceamento de classes (empates são minoria) | Médio | Métricas adequadas (log-loss, F1) e técnicas de balanceamento |
| Overfitting a seleções "grandes" | Médio | Validação cruzada; features normalizadas, não nominais |

### 2.4. Terminologia

- **Forma recente:** desempenho de uma seleção nos últimos *N* jogos.
- **xG / xA:** *expected goals* / *expected assists* — qualidade das chances.
- **Confronto:** par (seleção mandante/lado A, seleção visitante/lado B).
- **Resultado (target):** Vitória A / Empate / Vitória B (classificação multiclasse).

### 2.5. Custos e Benefícios

- **Custos:** tempo de desenvolvimento; infra (gratuita/baixo custo — Supabase free
  tier, Docker local). Sem custo de dados (API pública).
- **Benefícios:** portfólio técnico demonstrável end-to-end; ferramenta de previsão
  funcional; base reaproveitável para outras competições.

---

## 3. Determinar as Metas de Mineração de Dados

> *Tradução dos objetivos de negócio para objetivos técnicos de dados.*

### 3.1. Metas de Data Mining

1. **Construir uma base histórica limpa** de jogos de seleções, com estatísticas
   por partida, a partir do scraping do SofaScore.
2. **Engenharia de features de "forma recente"** por seleção: médias móveis dos
   últimos *N* jogos (gols marcados/sofridos, posse, finalizações, xG, % de
   vitórias, etc.).
3. **Treinar um modelo de classificação multiclasse** que, dado um confronto,
   prevê a probabilidade de {Vitória A, Empate, Vitória B}.
4. **Avaliar a importância das features** para responder *quais fatores mais
   pesam* no resultado.

### 3.2. Critérios de Sucesso de Data Mining

- O modelo supera dois baselines: (a) classe majoritária e (b) "vence o de melhor
  ranking/forma".
- **Métricas-alvo:** acurácia acima do baseline e **log-loss** baixo (probabilidades
  bem calibradas importam mais que só acerto bruto).
- Probabilidades **calibradas** (quando o modelo diz 70%, acerta ~70% das vezes).
- Pipeline de features reproduzível e sem vazamento de dados (apenas info
  pré-jogo).

---

## 4. Produzir o Plano de Projeto

### 4.1. Plano (fases CRISP-DM × estado atual do projeto)

| Fase CRISP-DM | Entrega | Status |
|---------------|---------|--------|
| 1. Entendimento do Negócio | **Este documento** | 🟡 Em andamento |
| 2. Entendimento dos Dados | Catálogo de dados do SofaScore, EDA inicial, qualidade dos dados | ⬜ A fazer |
| 3. Preparação dos Dados | Scraper adaptado para seleções; tabela de features de "forma recente" | ⬜ A fazer |
| 4. Modelagem | Modelos de classificação (baseline → árvores/boosting), tuning | ⬜ A fazer |
| 5. Avaliação | Comparação com baselines, calibração, importância de features | ⬜ A fazer |
| 6. Implantação (Deployment) | **Planilha Excel** com os confrontos e previsões (probabilidades + resultado provável) | ⬜ A fazer |

> **Nota de migração:** o scraper (Patchright) e o banco (Supabase/PostgreSQL) já
> estão implementados na versão Premier League e serão **reaproveitados**. As
> partes de **orquestração (Airflow), dashboards (Power BI) e portfólio web
> (Next.js) saem de escopo**. A adaptação principal é mudar a descoberta de jogos
> de "por time/clube" para "por seleção", trocar o filtro de torneio e introduzir o
> conceito de **fase do torneio** (grupos → mata-mata).

### 4.2. Avaliação Inicial de Ferramentas e Técnicas

- **Coleta:** Python + Patchright (scraping resiliente do SofaScore), executado
  manualmente.
- **Armazenamento/Processamento:** Supabase/PostgreSQL + pandas.
- **Modelagem:** scikit-learn (baselines, Logistic Regression), depois
  XGBoost/LightGBM para o modelo principal; calibração via `CalibratedClassifierCV`.
- **Avaliação:** validação cruzada temporal, log-loss, matriz de confusão,
  feature importance (SHAP).
- **Entrega:** exportação das previsões para **Excel** (`openpyxl`/`pandas.to_excel`).
