# Execução da calibração da Etapa 1 (CET MSVP 2019)

Ordem dos comandos da calibração só com os dados da CET (branch `cet-calibration`). A TomTom não é mais usada: não há dados dela de 2019. Este arquivo é atualizado conforme os passos rodam. Todos os comandos rodam da raiz do repositório, e toda a configuração vem de `conf/calibration_params.json` (validado por `python calibration/params.py`).

O Python precisa ter `requests` e `optuna`. Nesta máquina, o `.venv` do OMNeT++ (o `python` padrão do shell) não tem `requests`; `~/bin/.venv/env/bin/python` tem todas as dependências.

## Ordem

| # | Passo | Comando | Entrada | Saída | Tempo | Última execução |
|---|---|---|---|---|---|---|
| 1 | Redes e rotas | `python src/fetch_data.py` | `conf/routes.json` | `osmFiles/<topologia>.osm`/`.net.xml`, `conf/cet_scenarios.json` | minutos (download do OSM) | 2026-10-31 (Overpass fora do ar; baixado pela API do OSM em blocos) |
| 2 | Calibração | `python src/calibrate_stage1.py` | `conf/cet_scenarios.json`, `conf/cet_*.csv` | `osmFiles/<topologia>.tls_offsets.add.xml`, `conf/route_flows_calibrated.json`, `conf/krauss_calibration.json` | teste com `--trials 2`: ~40–45 min por topologia; rodada completa estimada em 3–6 h | só testes curtos (2026-10-31) |
| 3 | Rotas por hora | `python src/generate_routes.py` | saídas dos passos 1 e 2 | `trafficXmlFiles/<topologia>_h<HH>.rou.xml`/`.sumocfg`, `conf/flow_levels.json` | segundos | 2026-10-31, sobre o teste curto |

Depois vêm as Etapas 2.0 (`benchmark_crypto.py`), 2 (`generate_stage2_configs.py`), 3 e 4, como no `README.md`.

## Quando repetir cada passo

- **1 (`fetch_data.py`)**: depois de mudar `conf/routes.json` ou `network.*`. Baixar o OSM de novo pode mudar a rede e invalidar os offsets e a calibração. `--skip-download` reaproveita os `.osm`; `--skip-netconvert` só refaz o mapeamento das rotas.
- **2 (`calibrate_stage1.py`)**: depois do passo 1 ou de mudar `cet.*`, `flow.*`, `simulation.*`, `krauss.*` ou os CSVs da CET. `--topology` limita a topologias; as demais ficam como estão nos JSONs.
- **3 (`generate_routes.py`)**: depois do passo 2 ou de mudar `cet.synthetic`.

## Opções úteis

```bash
# Teste curto da calibração de uma topologia
python src/calibrate_stage1.py --topology urban_road --trials 3

# Validar conf/calibration_params.json
python calibration/params.py
```

## Próximo passo

Em 2026-11-01 o código rodou de ponta a ponta com `calibrate_stage1.py --smoke` (dados 2016–2019), `generate_routes.py` (54 cenários: highway 22, urban_road 22, intersection 10) e `generate_stage2_configs.py` (216 configs). Os `conf/*_calibrated.json` atuais vieram do `--smoke` e **não valem como calibração**; `generate_routes.py` avisa isso.

O usuário roda as etapas longas:

```bash
PY=~/bin/.venv/env/bin/python
$PY src/calibrate_stage1.py          # calibração completa (várias horas)
$PY src/generate_routes.py
$PY src/generate_stage2_configs.py
$PY src/orchestrate_stage3.py        # 2.160 simulações OMNeT++ (todos os protocolos)
$PY src/analyze_stage4_results.py
```
