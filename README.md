# Pipeline de Simulação VANET (SUMO + Veins + OMNeT++)

Pipeline para avaliar o impacto da cifração com preservação de formato **FF1** (NIST SP 800-38G) em mensagens V2X (CAM) sobre cenários de tráfego reais calibrados com dados da TomTom e da CET-SP.

A matriz experimental é **3 topologias** (`highway`, `urbanRoad`, `intersection`) × **3 densidades** (`low_flow`, `medium_flow`, `high_flow`) × **4 protocolos**, com 30 repetições (sementes) por configuração: 36 configurações e 1080 simulações.

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
- Chave da API TomTom (lida por `tomtom_traffic_stats.py`)

## Etapas do pipeline

```text
fetch_data ─► calibrate_stage1 ─► generate_routes ─► benchmark_crypto ─► generate_stage2_configs ─► orchestrate_stage3 ─► analyze_stage4_results
 (Etapa 1.1)     (Etapa 1.2)         (Etapa 1.3)         (Etapa 2.0)            (Etapa 2)                 (Etapa 3)               (Etapa 4)
```

### Etapa 1.1 — `fetch_data.py`: dados de tráfego e redes viárias

- Lê `conf/tomtom.json` e `conf/routes.json` e cria um job *Route Analysis* na API TomTom Traffic Stats para um dia histórico, com velocidades hora a hora por rota.
- Calcula uma *bounding box* por topologia (rotas + margem de 2 km), baixa o mapa via Overpass API (`osmFiles/<topologia>.osm`) e converte para rede SUMO com `netconvert` (`osmFiles/<topologia>.net.xml`).
- Mapeia os segmentos de cada rota TomTom para *edges* da rede SUMO (`sumo_route_utils.py`).
- Saída: `conf/tomtom_calibrated_rates.json` (velocidades, edges SUMO, fluxos HCM e bounding boxes).

Opções:

- `--dry-run`: mostra o resumo da requisição sem chamadas de rede.
- `-f/--file <arquivo>`: reutiliza um resultado TomTom já baixado em `conf/` em vez de criar um novo job.

### Etapa 1.2 — `calibrate_stage1.py`: calibração do modelo Krauss

- Para cada topologia, otimiza os parâmetros do modelo de seguimento Krauss (`accel`, `decel`, `tau`, `sigma`, `minGap`) com Optuna (TPE, 50 trials).
- Cada trial executa o SUMO (1 h simulada, 10 min de aquecimento) e mede o RMSE entre as velocidades simuladas e as velocidades-alvo da TomTom:
  - **com contagens CET**: injeta os fluxos horários da CET 2019 (`conf/cet_volumes_2019.csv`, convertidos em carros equivalentes) e compara com a velocidade TomTom da mesma hora; valida também contra `conf/cet_speeds_2019.csv`;
  - **sem contagens CET**: usa o fluxo HCM de calibração e a velocidade média de 24 h.
- As simulações horárias rodam em paralelo (`SUMO_WORKERS`, padrão até 4; `SUMO_BINARY` permite trocar o executável).
- Saída: `conf/krauss_calibration.json`.

### Etapa 1.3 — `generate_routes.py`: rotas SUMO

- Para cada topologia e cada nível de fluxo HCM, gera `trafficXmlFiles/<topologia>_<fluxo>.rou.xml` (fluxos nas rotas TomTom com o perfil Krauss calibrado, ou o padrão se não houver calibração) e o respectivo `.sumocfg`.

### Etapa 2.0 — `benchmark_crypto.py`: tempos do FF1

- Compila `src/crypto_benchmark/ff1_benchmark.cc` com o mesmo código C++ de `src/latency` (`Aes128`, `Lea128`, `Speck128`, `Ff1`).
- Antes de medir, valida os vetores de teste: AES-128 (FIPS-197), LEA-128 (KISA), SPECK-128/128 e os exemplos 1–3 de FF1-AES128 do NIST. Se algum falhar, a etapa é interrompida.
- Mede 10 000 cifrações e decifrações FF1 (radix 256) de um payload de 32 bytes, que é o tamanho do CAM (`beaconLengthBits` = 256 bit), por cifra de bloco.
- Saída: `conf/crypto_timings.json`, com as medianas usadas como `cryptoTxDelay`/`cryptoRxDelay` e as estatísticas completas. Os tempos refletem a máquina onde o benchmark rodou (registrada em `metadata`), não uma OBU embarcada.

Medir antes, e não durante a simulação, mantém as simulações reprodutíveis, mesmo com várias rodando em paralelo.

### Etapa 2 — `generate_stage2_configs.py`: configuração OMNeT++/Veins

- Gera um `conf/scenario_<topologia>_<fluxo>.launchd.xml` por cenário, apontando para o `.sumocfg` correspondente.
- Gera `conf/omnetpp.ini` com uma configuração por combinação topologia × fluxo × protocolo: área (*playground*) calculada a partir dos limites da rede SUMO, parâmetros PHY/MAC 802.11p, beacons de 1 s, cifra (`cryptoCipher`), overhead e atrasos medidos de cada protocolo (lidos de `conf/protocols.json` e `conf/crypto_timings.json`), `sim-time-limit = 600s` e `repeat = 30`.

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
| `tomtom_traffic_stats.py` | Cliente da API TomTom Traffic Stats: monta o *payload* de Route Analysis, aguarda o job, baixa o resultado e normaliza as velocidades por rota/hora. Usado por `fetch_data.py`. |
| `sumo_route_utils.py` | Carrega o `sumolib` e as redes SUMO; faz o *map matching* das geometrias TomTom para sequências de edges. |
| `protocols.py` | `load_protocols()` e `load_crypto_timings()`: leem `conf/protocols.json` e `conf/crypto_timings.json`. |
| `reference_data.py` | Lê os dados da CET 2019 (`conf/cet_volumes_2019.csv` e `conf/cet_speeds_2019.csv`) e converte contagens classificadas em carros equivalentes (auto 1,0; ônibus/caminhão 1,5; moto 0,4). |
| `plot_kpi_charts.py` | A partir de `scavetool_export.csv`, calcula KPIs por repetição e gera barras com IC 95% (inclusive *Latency vs baseline (ms)*), tendências por fluxo, boxplots de latência e dispersão CBR × latência em `results/plots/kpi_charts/`. |
| `generate_kpi_heatmaps.py` | A partir de `final_metrics_summary.csv`, gera *heatmaps* de KPIs (BSMs recebidos, CBR, latência e atrasos criptográficos) por topologia/fluxo × protocolo em `results/plots/kpi_heatmaps.{png,pdf}`. |
| `inspect_network.py` | Lista os edges (não internos) de `conf/network.net.xml` para inspeção rápida. |
| `test.py` | Rascunho para testar a API TomTom Flow Segment. Não faz parte do pipeline. |

`plot_kpi_charts.py` e `generate_kpi_heatmaps.py` não são chamados por `run_pipeline.py`; execute-os manualmente após a Etapa 4.
