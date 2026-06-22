# World Cup Analytics — Resumo

**Autor:** Caio Falleiro dos Santos

**Objetivo:** prever o resultado das partidas da Copa do Mundo 2026 — vitória da seleção A, empate ou vitória da seleção B, com a respectiva probabilidade.

**Base de dados:** os últimos 50 jogos de cada uma das 48 seleções participantes, extraídos diretamente do SofaScore (de qualquer competição — Copa, eliminatórias e amistosos), para capturar a forma recente de cada time.

**Dados utilizados:** placar e resultado de cada partida, estatísticas por jogo (posse, finalizações, passes, duelos) e estatísticas individuais dos jogadores (rating, gols, xG, xA), estas agregadas por time.

**Como trabalhamos:** scraping em Python → armazenamento em PostgreSQL → engenharia de features de forma recente → modelo de classificação (scikit-learn/XGBoost) → previsões exportadas em Excel, seguindo a metodologia CRISP-DM.
