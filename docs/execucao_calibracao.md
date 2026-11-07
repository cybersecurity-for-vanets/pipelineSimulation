# Execução da calibração da Etapa 1 (CET MSVP 2019)

Ordem dos comandos da calibração só com os dados da CET (branch `cet-calibration`). A TomTom não é mais usada: não há dados dela de 2019. Este arquivo é atualizado conforme os passos rodam. Todos os comandos rodam da raiz do repositório, e toda a configuração vem de `conf/calibration_params.json` (validado por `python calibration/params.py`).

O Python precisa ter `requests` e `optuna`. Nesta máquina, o `.venv` do OMNeT++ (o `python` padrão do shell) não tem `requests`; `~/bin/.venv/env/bin/python` tem todas as dependências.

## Ordem

| # | Passo | Comando | Entrada | Saída | Tempo | Última execução |
|---|---|---|---|---|---|---|
| 1 | Redes, rotas e reparos | `python src/fetch_data.py` | `conf/routes.json` | `osmFiles/<topologia>.osm`/`.net.xml` (reparada), `conf/cet_scenarios.json` (rotas e alças) | minutos (download do OSM) | 2026-11-06 com `--skip-download` (OSM de 2026-10-31; `network.join_nodes` na intersection; highway e urban_road sem mudança de faixas, conexões ou fases) |
| 2 | Calibração | `python src/calibrate_stage1.py` | `conf/cet_scenarios.json`, `conf/cet_*.csv` | `osmFiles/<topologia>.tls_offsets.add.xml`, `conf/route_flows_calibrated.json`, `conf/krauss_calibration.json` | `--smoke`: 8–15 min; rodada completa 3–6 h (2026-11-05: ~4 h com 3 níveis de calibração; com 6 níveis, estimar o dobro) | 2026-11-06: rodada completa com calibração em 2019 inteiro; intersection recalibrada após `network.join_nodes`. highway aprovada (22,6%); urban_road (57,9%) e intersection (72,4%) reprovadas, aceitas como limitação |
| 3 | Rotas por hora | `python src/generate_routes.py` | saídas dos passos 1 e 2 | `trafficXmlFiles/<topologia>_h<HH>.rou.xml`/`.sumocfg`, `conf/flow_levels.json` | segundos | 2026-11-06, sobre a rodada completa |

Depois vêm as Etapas 2.0 (`benchmark_crypto.py`), 2 (`generate_stage2_configs.py`), 3 e 4, como no `README.md`.

## Quando repetir cada passo

- **1 (`fetch_data.py`)**: depois de mudar `conf/routes.json` ou `network.*`. Baixar o OSM de novo pode mudar a rede e invalidar os offsets e a calibração. `--skip-download` reaproveita os `.osm`; `--skip-netconvert` refaz o mapeamento das rotas sobre a rede atual. Os reparos da rede (`network_repair.py`) rodam sempre e são idempotentes. Para mudar `network.min_main_green_share` ou `network.min_other_green_s`, rode sem `--skip-netconvert`: a rede já reparada não volta ao plano original.
- **2 (`calibrate_stage1.py`)**: depois do passo 1 ou de mudar `cet.*`, `flow.*`, `simulation.*`, `krauss.*` ou os CSVs da CET. `--topology` limita a topologias; as demais ficam como estão nos JSONs.
- **3 (`generate_routes.py`)**: depois do passo 2 ou de mudar `cet.synthetic`.

## Opções úteis

```bash
# Teste curto da calibração de uma topologia
python src/calibrate_stage1.py --topology urban_road --trials 3

# Reaplicar o critério de validação ao relatório gravado, sem simular
python src/calibrate_stage1.py --verdict-only

# Validar conf/calibration_params.json
python calibration/params.py
```

## Próximo passo

Em 2026-11-05 a validação da calibração anterior (2026-10-31) mostrou fila de inserção de 400–2.200 s e velocidade simulada muito abaixo da CET. A causa era a rede: faixas que não seguiam em frente, verde de 15–28% para a via principal, conversões artificiais entre pistas paralelas da Nove de Julho e o volume da 23 de Maio forçado de ponta a ponta através das reduções de faixa. As correções estão em `docs/calibracao_etapa1.md` (seções "Reparos da rede" e "Alças da highway").

A rodada completa de 2026-11-05 (calibração em 2019 17–19h) reprovou nas três topologias pelo critério de 25%: highway 37,3%, intersection 62,8%, urban_road 66,7%. Duas causas:

1. Rede e demanda. As saídas da 23 de Maio antes do ponto de contagem punham 2.000–2.400 veic/h por faixa a montante. Duas entradas em dê a preferência não deixavam nenhum veículo entrar. A intersection_main_centro começava numa aresta de 1 faixa da Av. Cidade Jardim. As três coisas foram corrigidas (`generate_routes._ramp_flows`, `sumo_route_utils.route_ramps` e `trim_route_ends`).
2. Escolha das horas. Só com a tarde, o TPE escolheu perfis de baixa capacidade para imitar a fila que vem de fora da rede, e as manhãs travaram. Agora a calibração usa todas as horas de 2019 (intersection: 7, 9, 17 e 19h; valida 8 e 18h).

Resultado de 2026-11-06: highway aprovada (22,6%). A intersection foi recalibrada sozinha depois de unir as junções da R. Alemanha e da R. Turquia (`network.join_nodes`) e ficou em 72,4%; a urban_road ficou em 57,9%. As duas reprovam porque a CET mede velocidades muito diferentes com o mesmo volume (congestionamento de fora do recorte) e foram aceitas como limitação documentada. `generate_routes.py` já rodou sobre esses perfis.

Rodadas longas usam `nohup`, para não depender da sessão do terminal:

```bash
nohup ~/bin/.venv/env/bin/python -u src/calibrate_stage1.py --topology intersection > logs/calibracao_intersection.log 2>&1 &
```

Próximas etapas:

```bash
PY=~/bin/.venv/env/bin/python
$PY src/generate_routes.py
$PY src/generate_stage2_configs.py
$PY src/orchestrate_stage3.py        # 2.160 simulações OMNeT++ (todos os protocolos)
$PY src/analyze_stage4_results.py
```
