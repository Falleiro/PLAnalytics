# Estratégia de Agregação de Dados de Jogadores

> **Fase CRISP-DM:** Preparação dos Dados (Data Preparation)
> **Projeto:** World Cup Analytics
> **Data:** Junho/2026

---

## 1. Objetivo

Definir **como** os dados individuais de jogadores (`player_match_stats`) serão
transformados em features no nível de partida, e **por que** dessa forma, para
produzir um **DataFrame único** (1 linha por partida) que sirva tanto à entrega da
base quanto à modelagem preditiva.

---

## 2. O problema: granularidade (grain)

As tabelas têm granularidades diferentes:

| Tabela | Granularidade | Linhas (aprox.) |
|--------|---------------|-----------------|
| `matches` (+ `match_stats`) | 1 linha por **partida** | ~1.100 |
| `player_match_stats` | 1 linha por **jogador × partida** | ~46.000 |

**Não se pode fazer um "merge cru"** (juntar as linhas de jogadores direto na
partida): isso produziria ~22 linhas por partida, com toda a informação da partida
**repetida 22 vezes**. Consequências: arquivo inflado, risco de erro de análise
(somar/contar colunas de partida 22× a mais) e granularidade incompatível com o
alvo de previsão (que é **1 resultado por partida**).

**Solução:** primeiro **agregar** os jogadores ao nível de time-na-partida, depois
juntar à partida. Resultado: 1 linha por partida com colunas dos dois lados.

---

## 3. A questão central: agregar sem perder o "craque"

A agregação ingênua pela **média** do time apaga informação importante. Ex.: se a
Argentina tem o Messi com rating altíssimo, a média do time dilui esse sinal — e
perdemos a informação de que um jogador excepcional aumenta a chance de vitória.

Pesquisamos como projetos acadêmicos e práticos resolvem isso. Conclusões:

1. **Média pura perde sinal — confirmado.** Estudo de agregação de habilidade de
   times mostra que **MAX frequentemente supera SUM/MIN**, porque "a performance
   geral do time é determinada principalmente pelo membro de maior habilidade".
   → Uma feature de **máximo / top-N captura justamente o craque**.
2. **Agregação por posição (role-based) é o padrão.** O framework da PLOS One
   transforma centenas de features de jogadores em features de time agregando
   **por posição** (atacante / meia / defensor / goleiro), e troca a média por
   agregação para "não ignorar a vantagem numérica" (uma linha de 5 defensores é
   mais forte que de 4).
3. **Nuance:** futebol é coletivo — um único craque não salva um time fraco. Então
   o sinal do craque existe mas não é determinístico, o que justifica usar
   **média (qualidade geral) + máximo (craque) juntos**.
4. **Estado da arte** (Graph Neural Nets que modelam cada jogador como nó) preserva
   o jogador individual sem agregar — porém é **overkill** para o escopo deste
   projeto (Excel + scikit-learn/XGBoost).

**Decisão:** manter a estrutura "agregar por `match_id` + `team_side`", mas usar
**várias funções de agregação** (não só média), preservando o craque via `max` /
`top-N` e a estrutura tática via agregação **por posição**.

---

## 4. Conjunto de features de jogadores (por time × partida)

A partir de `player_match_stats`, agrupando por `(match_id, team_side)` —
considerando os jogadores que atuaram (com `rating` preenchido):

| Feature | Função | O que captura |
|---------|--------|---------------|
| `rating_mean` | média do rating | qualidade geral do time |
| `rating_max` | máximo do rating | **o craque** ⭐ |
| `rating_top3_mean` | média dos 3 maiores ratings | núcleo de estrelas |
| `rating_std` | desvio-padrão do rating | "time de um homem só" vs. coletivo |
| `xg_sum` / `xg_max` | soma / máx. de expected goals | poder ofensivo / finalizador decisivo |
| `xa_sum` | soma de expected assists | criação de chances |
| `goals_sum`, `assists_sum` | soma | produção concreta |
| `key_passes_sum`, `shots_sum` | soma | volume ofensivo |
| `dribbles_won_sum` | soma de dribles certos | habilidade no 1×1 |
| `aerial_won_sum`, `aerial_total_sum` | soma de duelos aéreos ganhos / totais | jogo aéreo |
| `att_rating_mean` | média rating dos atacantes (pos = F) | força de ataque |
| `mid_rating_mean` | média rating dos meias (pos = M) | controle de meio |
| `def_rating_mean` | média rating dos defensores (pos = D) | solidez defensiva |
| `gk_rating` | rating do goleiro (pos = G) | qualidade do goleiro |
| `n_players` | contagem | nº de jogadores com dados |

> Posições no SofaScore: `G` (goleiro), `D` (defensor), `M` (meia), `F` (atacante).

Essas colunas são geradas para **cada lado** e renomeadas com prefixo
`home_` / `away_` ao montar a linha da partida.

---

## 5. Pipeline de agregação (3 etapas)

```
player_match_stats (1 linha/jogador/partida)
   │  groupby(match_id, team_side).agg(mean, max, top3, std, sum, por posição)
   ▼
agregado (2 linhas/partida: home + away)
   │  pivot/unstack team_side → colunas home_* e away_*
   ▼
features de jogadores (1 linha/partida)
   │  merge em matches(+match_stats) por match_id
   ▼
DF ÚNICO (1 linha por partida)
```

---

## 6. Distinção crítica: BASE descritiva × ABT preditiva (vazamento de dados)

Há **dois usos** do mesmo df, e eles diferem num ponto essencial:

- **Base descritiva (entrega atual ao professor):** cada linha descreve uma partida
  que **já aconteceu**, então pode conter os agregados de jogadores **daquele jogo**
  (ex.: `home_rating_max` = melhor rating da escalação naquela partida). Serve para
  análise/EDA e como entregável da base.

- **ABT preditiva (modelagem, fase seguinte):** para **prever** um jogo, as stats
  dos jogadores **daquele** jogo não existem antes da partida → usá-las seria
  **vazamento de dados**. Nessa fase, cada feature de jogador vira a **média móvel
  dos N jogos anteriores** de cada seleção (ex.: "`rating_max` médio da escalação da
  Argentina nos últimos 5 jogos") e, idealmente, a **diferença** entre os lados
  (`home − away`).

> Em resumo: a **mesma lógica de agregação** alimenta os dois; muda apenas se ela é
> aplicada à própria partida (base) ou em janela passada (ABT preditiva).

---

## 7. Estrutura do DataFrame único entregue

1 linha por partida, contendo:

- **Identificação/contexto:** `sofascore_event_id`, `match_date`, `competition`,
  `season`, `home_team`, `away_team`, `venue`.
- **Resultado:** `score_home`, `score_away`, `result` (W/D/L do mandante).
- **Estatísticas de time (match_stats):** posse, finalizações, passes, etc. (já 1:1).
- **Agregados de jogadores:** colunas `home_*` e `away_*` da Seção 4.

---

## 8. Cobertura e limitações conhecidas da base

Estado da base gerada (jun/2026, `output/worldcup_dataset.xlsx`):

- **1.101 partidas × 78 colunas** (1 linha por partida).
- **872 (79%)** partidas têm agregados de jogadores completos (com rating).
- **120** têm escalação mas **sem rating** e **109** sem linhas de jogador —
  concentradas em **amistosos e eliminatórias menores** (CAF/AFC/AfCON quals),
  onde o SofaScore **não fornece rating de jogador**. É limitação da **fonte**,
  não do pipeline; nessas linhas as colunas de jogador ficam vazias (NaN).

**Limitação do `match_stats` (stats de time):** o scraper original salvou apenas o
lado do **mandante** (só `home_val`). Por isso as colunas de estatística de time
têm prefixo `home_` e **não há equivalente para o visitante**. Os agregados de
**jogadores**, esses sim, existem para os dois lados (`home_*` e `away_*`) e cobrem
boa parte da força do time visitante. Para capturar as stats de time dos dois lados
seria necessário ajustar o modelo `MatchStats` e **re-raspar** (decisão futura).

**Limpeza de colunas vazias do `match_stats`:** algumas colunas vinham 100% vazias
da fonte. Tratamento aplicado no `build_dataset.py`:
- **Removidas (sem fonte):** `long_balls_accurate`, `ground_duels_total/won`
  (não há equivalente nos jogadores).
- **Removidas e substituídas por agregados de jogadores (dois lados):**
  `dribbles_succeeded` → `*_dribbles_won_sum`; `aerial_duels_won/total` →
  `*_aerial_won_sum` / `*_aerial_total_sum`.
- **Recuperada por cálculo:** `pass_accuracy_pct` = `passes_accurate / passes_total`.
- `interceptions` ficou de fora: veio 0% também no `player_match_stats` (campo não
  capturado pela fonte).

**Implicações para a modelagem:** tratar os NaN (excluir partidas sem dados ou
imputar) e considerar usar como base de treino prioritária as 872 partidas com
features completas.

## 9. Referências

- Machine Learning for Soccer Match Result Prediction — arXiv 2403.07669
- Interpretable match prediction with FIFA ratings and team formation — PLOS One
  (doi:10.1371/journal.pone.0284318)
- Evaluating Team Skill Aggregation (MAX vs SUM/MIN) — arXiv 2106.11397
- Player-Team Heterogeneous Interaction Graph Transformer — arXiv 2507.10626
- From Players to Champions: ML for World Cup outcome prediction — arXiv 2505.01902
