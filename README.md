# Pipeline de Simulação VANET (SUMO + Veins + OMNeT++)

Pipeline para avaliar o impacto da cifração com preservação de formato **FF1** (NIST SP 800-38G) em mensagens V2X (CAM) sobre cenários de tráfego reais calibrados só com os dados da CET-SP (relatório MSVP 2019 de volumes e velocidades).

A matriz experimental é **3 topologias** (`highway`, `urbanRoad`, `intersection`) × **níveis de fluxo** × **4 protocolos**, com 10 repetições (sementes) por configuração. Os níveis observados são os volumes CET de cada ano e hora pesquisados (`y2019h07` = 2019, 07h–08h; anos 2016–2019, horas 7–9h e 17–19h). Os níveis sintéticos (`s025`, `s050`, `s075`, `s125`) são múltiplos do fluxo médio de 2019 e cobrem volumes que a CET não pesquisou. São 22 níveis na highway, 22 na urban_road e 10 na intersection: 54 cenários, 216 configurações e 2.160 simulações.

| Topologia | Trecho CET | Demanda |
| --- | --- | --- |
| `highway` | Av. 23 de Maio, Vd. Brig. Luís Antônio – Vd. Paraíso (2.498 m), dois sentidos | Contagem no Viad. Beneficência Portuguesa |
| `urbanRoad` | R. da Consolação, Av. Paulista – R. Rêgo Freitas (1.922 m), dois sentidos | Contagem entre R. Caio Prado e R. Marquês de Paranaguá |
| `intersection` | Av. Nove de Julho (Av. Cidade Jardim – Av. São Gabriel, 1.351 m) × Av. São Gabriel (Av. Nove de Julho – Av. Antônio J. de M. Andrade, ~905 m), dois sentidos | Nove de Julho: contagem entre R. Manuel Guedes e R. Emanuel Kant; São Gabriel: sem contagem, fluxo inferido da velocidade CET |

| Protocolo | Descrição |
| --- | --- |
| `baseline` | CAM sem criptografia |
| `FF1_AES` | FF1 sobre AES-128 |
| `FF1_LEA` | FF1 sobre LEA-128 |
| `FF1_SPECK` | FF1 sobre SPECK-128/128 |

Os protocolos estão definidos em `conf/protocols.json`, lido por todos os scripts; a ordem das chaves é a ordem nos gráficos. FF1 preserva o formato, então o CAM não cresce (overhead de 0 bytes) e os protocolos diferem pelo atraso de processamento, medido pelo benchmark da Etapa 2.0.

## Estrutura de diretórios

```text
pipelineSimulation
├── src                     # Scripts do pipeline (descritos abaixo)
│   ├── latency             # Módulo C++ Veins (LatencyTraCIDemo11p) + FF1/AES/LEA/SPECK, compilado como plugin
│   └── crypto_benchmark    # Benchmark e vetores de teste do FF1
├── conf                    # Configurações de entrada e arquivos gerados (ini, launchd, calibração)
├── osmFiles                # Mapas OSM baixados e redes SUMO (*.net.xml) por topologia
├── trafficXmlFiles         # Rotas (*.rou.xml) e configs SUMO (*.sumocfg) por topologia/fluxo
├── logs                    # Saída stdout/stderr de cada simulação, por topologia/configuração
├── results
│   ├── rawData             # Resultados OMNeT++ (.sca/.vec) e CSVs exportados
│   └── plots               # Gráficos gerados
└── README.md
```

## Execução

O pipeline completo é executado a partir da raiz do projeto:

```bash
python src/run_pipeline.py
```

`run_pipeline.py` executa cada etapa como um subprocesso, em sequência, e interrompe tudo se alguma etapa falhar. Cada script também pode ser executado isoladamente (`python src/<script>.py`).

### Pré-requisitos

- `clang++` com suporte a C++17 (benchmark FF1)
- Python com `requests`, `optuna`, `pandas`, `numpy`, `matplotlib`
- SUMO (`sumo`, `netconvert` e `sumolib`; `SUMO_HOME` configurado)
- OMNeT++ 6.2 e Veins 5.3.1 — os caminhos estão fixos em `orchestrate_stage3.py` e `analyze_stage4_results.py` (`OPP_RUN`, `VEINS_*`, `SCAVETOOL_BIN`) e devem ser ajustados para a máquina local

## Etapas do pipeline

```text
fetch_data ─► calibrate_stage1 ─► generate_routes ─► benchmark_crypto ─► generate_stage2_configs ─► orchestrate_stage3 ─► analyze_stage4_results
 (Etapa 1.1)     (Etapa 1.2)         (Etapa 1.3)         (Etapa 2.0)            (Etapa 2)                 (Etapa 3)               (Etapa 4)
```

Todos os parâmetros da Etapa 1 ficam em `conf/calibration_params.json`, lido e validado só por `calibration/params.py` (`python calibration/params.py` valida o arquivo).

### Etapa 1.1 — `fetch_data.py`: redes viárias e rotas CET

- Lê `conf/routes.json`: um trecho CET por rota e sentido, com pontos `[lat, lon]` em ordem de viagem.
- Calcula uma *bounding box* por topologia (pontos + `network.map_margin_km`), baixa só as vias pelo Overpass (`osmFiles/<topologia>.osm`) e, se todos os servidores falharem, pela API do OSM em blocos. Converte para rede SUMO com `netconvert` (`osmFiles/<topologia>.net.xml`).
- Mapeia cada rota para *edges* da rede SUMO (`sumo_route_utils.py`) e avisa quando o comprimento no SUMO se afasta do trecho CET mais que `network.max_length_ratio_error`.
- Saída: `conf/cet_scenarios.json`. `--skip-download` reaproveita os `.osm`; `--skip-netconvert` só refaz o mapeamento.

### Etapa 1.2 — `calibrate_stage1.py`: calibração com a CET

Volume e velocidade vêm dos relatórios MSVP 2016–2019 (`conf/cet_volumes.csv` e `conf/cet_speeds.csv`, lidos por `cet_data.py`). O método segue Khaleghian et al. (2023): calibra no pico da tarde de 2019 (`cet.calibration`) e valida em todos os outros níveis com velocidade CET. Detalhes em [`docs/calibracao_etapa1.md`](docs/calibracao_etapa1.md).

- **Demanda:** o fluxo de cada rota é o volume CET da hora em equivalente-carro (`cet.pce`). A Av. São Gabriel não tem contagem: o fluxo dela é achado por bisseção contra a velocidade CET de cada hora.
- **Semáforos:** em `topologies.signal_coordinated`, offsets de onda verde do `tlsCoordinator.py` do SUMO, ponderados pelo fluxo médio da tarde (`osmFiles/<topologia>.tls_offsets.add.xml`).
- **Krauss:** um perfil por topologia (`krauss.bounds`, inclusive `speedFactor` e `speedDev`), escolhido por TPE (Optuna) para minimizar o RMSE da velocidade nas horas de calibração. O primeiro trial é `krauss.default`.
- **Relatório:** simula as 6 horas com `simulation.report_seeds` e separa RMSE/MAE/viés de calibração e de validação.
- `--topology` calibra só algumas topologias; `--trials N` faz um teste curto. `SUMO_BINARY` permite trocar o executável.
- Saídas: `conf/route_flows_calibrated.json` (fluxo por rota e hora) e `conf/krauss_calibration.json` (perfil e relatório).

### Etapa 1.3 — `generate_routes.py`: rotas SUMO

- Para cada topologia, gera `trafficXmlFiles/<topologia>_<nível>.rou.xml` e o `.sumocfg` (com os offsets de semáforo, quando existem) de cada nível observado e de cada fator de `cet.synthetic`.
- Grava `conf/flow_levels.json`: a lista de níveis, os rótulos (`07h–08h`) e o fluxo total e por faixa de cada topologia e nível. Esse arquivo é o contrato com as Etapas 2–4 e os gráficos (ver `src/flow_levels.py`).

### Etapa 2.0 — `benchmark_crypto.py`: tempos do FF1

- Compila `src/crypto_benchmark/ff1_benchmark.cc` com o mesmo código C++ de `src/latency` (`Aes128`, `Lea128`, `Speck128`, `Ff1`).
- Antes de medir, valida os vetores de teste: AES-128 (FIPS-197), LEA-128 (KISA), SPECK-128/128 e os exemplos 1–3 de FF1-AES128 do NIST. Se algum falhar, a etapa é interrompida.
- Mede 10 000 cifrações e decifrações FF1 (radix 256) de um payload de 32 bytes, que é o tamanho do CAM (`beaconLengthBits` = 256 bit), por cifra de bloco.
- Saída: `conf/crypto_timings.json`, com as medianas usadas como `cryptoTxDelay`/`cryptoRxDelay` e as estatísticas completas. Os tempos refletem a máquina onde o benchmark rodou (registrada em `metadata`), não uma OBU embarcada.

Medir antes, e não durante a simulação, mantém as simulações reprodutíveis, mesmo com várias rodando em paralelo.

### Etapa 2 — `generate_stage2_configs.py`: configuração OMNeT++/Veins

- Gera um `conf/scenario_<topologia>_<fluxo>.launchd.xml` por cenário, apontando para o `.sumocfg` correspondente.
- Gera `conf/omnetpp.ini` com uma configuração por combinação topologia × fluxo × protocolo: área (*playground*) calculada a partir dos limites da rede SUMO, parâmetros PHY/MAC 802.11p, beacons de 1 s, cifra (`cryptoCipher`), overhead e atrasos medidos de cada protocolo (lidos de `conf/protocols.json` e `conf/crypto_timings.json`), `sim-time-limit` = aquecimento + medição (`WARMUP_S` + `MEASUREMENT_S`) e `repeat = 10`. Os níveis de fluxo vêm de `conf/flow_levels.json`.

### Etapa 3 — `orchestrate_stage3.py`: execução das simulações

- Compila o módulo `src/latency` (aplicação `LatencyTraCIDemo11p`, derivada de `TraCIDemo11p`) como biblioteca carregável. Ele registra latência fim a fim, *channel busy ratio*, atrasos criptográficos e PDR. Com `cryptoCipher` diferente de `none`, cada CAM é de fato cifrado com FF1 no envio e decifrado na recepção. O texto claro é montado a partir de campos que o receptor também vê, e o tweak é derivado do cabeçalho, sem bytes extras. Decifrações que não batem são contadas no escalar `ff1DecryptFailures`, que deve ser sempre 0. O tempo simulado vem só de `cryptoTxDelay`/`cryptoRxDelay`.
- Inicia o `veins_launchd` na porta 9999.
- Lê as configurações e o número de repetições do `omnetpp.ini` e executa todas as simulações em `Cmdenv`, em paralelo (6 *threads*).
- Saídas: resultados em `results/rawData/` e logs em `logs/<topologia>/<config>/run_<seed>.{out,err}`.

### Etapa 4 — `analyze_stage4_results.py`: análise estatística

- Exporta os resultados com `opp_scavetool` para `results/rawData/scavetool_export.csv`.
- Extrai topologia, protocolo e cenário do nome de cada configuração, agrega as métricas (média e intervalo de confiança) e salva `results/rawData/final_metrics_summary.csv`.
- Gera os gráficos em `results/plots/`.

## Scripts auxiliares

| Script | Função |
| --- | --- |
| `sumo_route_utils.py` | Carrega o `sumolib` e as redes SUMO; faz o *map matching* dos pontos de cada trecho CET para sequências de edges. |
| `cet_data.py` | Lê `conf/cet_volumes.csv` e `conf/cet_speeds.csv` e converte as contagens classificadas em equivalente-carro (`cet.pce`). |
| `flow_levels.py` | Contrato dos níveis de fluxo (`y2019h07`, `s050`): ids, rótulos, tipo e leitura de `conf/flow_levels.json`. |
| `protocols.py` | `load_protocols()` e `load_crypto_timings()`: leem `conf/protocols.json` e `conf/crypto_timings.json`. |
| `plot_kpi_charts.py` | A partir de `scavetool_export.csv`, calcula KPIs por repetição e gera barras com IC 95% (inclusive *Latency vs baseline (ms)*), tendências por fluxo, boxplots de latência e dispersão CBR × latência em `results/plots/kpi_charts/`. |
| `generate_kpi_heatmaps.py` | A partir de `final_metrics_summary.csv`, gera *heatmaps* de KPIs (BSMs recebidos, CBR, latência e atrasos criptográficos) por topologia/fluxo × protocolo em `results/plots/kpi_heatmaps.{png,pdf}`. |
| `inspect_network.py` | Lista os edges (não internos) de `conf/network.net.xml` para inspeção rápida. |

`plot_kpi_charts.py` e `generate_kpi_heatmaps.py` não são chamados por `run_pipeline.py`; execute-os manualmente após a Etapa 4.
