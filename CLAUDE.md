# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Simulation pipeline (SUMO + Veins + OMNeT++) that measures the cost of FF1 format-preserving encryption (AES/LEA/SPECK block ciphers) on V2X CAM messages across calibrated traffic scenarios. Traffic is calibrated only with CET-SP MSVP survey data (2019 report `docs/relatorio-msvp-2019-revisao-2-junho-22.pdf`, 2016–2018 in `docs/msvp_<year>/`); TomTom was removed on branch `cet-calibration`. Flow levels are observed CET (year, hour) volumes (`y2019h07`…) plus synthetic multiples of the 2019 mean (`s025`, `s050`, `s075`, `s125`): 22 levels for highway and urban_road, 10 for intersection, × 4 protocols × 10 seeds = 2,160 runs. Protocol definitions live in `conf/protocols.json`, and the README is written in Portuguese, as are most script messages and comments. Keep new user-facing output consistent with that.

Realism is the goal: prefer ETSI/OBU-based parameters over Veins defaults.

## Commands

No tests, linter, formatter, or `requirements.txt` are configured. Dependencies are `requests`, `optuna`, `pandas`, `numpy`, `matplotlib`, plus SUMO, OMNeT++ 6.2, and Veins 5.3.1. On this machine the default `python` (the OMNeT++ `.venv`) lacks `requests`; `~/bin/.venv/env/bin/python` has everything.

```bash
# Full pipeline (runs stages in sequence and stops on the first failure)
python src/run_pipeline.py

# Any single stage (run from the repo root; stage scripts resolve paths from their own location)
python src/calibrate_stage1.py
python src/benchmark_crypto.py
python src/generate_stage2_configs.py
python src/orchestrate_stage3_baseline.py   # what run_pipeline.py actually calls
python src/orchestrate_stage3.py            # all protocols; commented out in run_pipeline.py
python src/analyze_stage4_results.py

# Optional plots after stage 4 (not called by run_pipeline.py)
python src/plot_kpi_charts.py
python src/generate_kpi_heatmaps.py
```

Build the Veins latency plugin by hand (normally done by `orchestrate_stage3.py:build_latency_plugin`):

```bash
cd src/latency
opp_makemake --make-so -f -o LatencyPlugin -I "$VEINS_SRC" -L "$VEINS_SRC" -l veins
make MODE=release    # produces out/**/libLatencyPlugin.dylib
```

The FF1 benchmark compiles with `$CXX`, falling back to `clang++` then `g++`, using `-std=c++17 -O2`.

The only correctness checks are built into the pipeline:
- `benchmark_crypto.py` validates AES-128 (FIPS-197), LEA-128 (KISA), SPECK-128/128, and NIST FF1-AES128 examples 1–3 before timing. A failure aborts the stage.
- The `ff1DecryptFailures` scalar must be 0 in every run.

## Hardcoded local paths

`orchestrate_stage3.py`, `orchestrate_stage3_baseline.py` (via import), and `analyze_stage4_results.py` hardcode macOS paths under `/Users/fabiotorres/` (`OPP_RUN`, `OPP_MAKEMAKE`, `VEINS_*`, `SCAVETOOL_BIN`) and `/usr/local/bin/sumo`. They also check for `/Volumes/PSSD T7/simulationData/`. If that directory exists, stage 3 writes its logs and results there, and stage 4 reads results from there, instead of using `logs/` and `results/rawData/` in the project. Check this before looking for output in the project folder.

## Architecture

The stages form a chain where each one consumes the previous stage's files in `conf/`:

1. **Data and calibration.** Every stage-1 constant lives in `conf/calibration_params.json`, read only through `calibration/params.py`. `conf/routes.json` defines one CET speed section per route and direction (points `[lat, lon]` in travel order, `cet_distance_m`, `demand`). Sites: highway = Av. 23 de Maio, urban_road = R. da Consolação, intersection = Av. Nove de Julho × Av. São Gabriel. `fetch_data.py` downloads roads from Overpass (falling back to tiled OSM API calls), runs `netconvert` without `--tls.guess`, maps routes to SUMO edges (`sumo_route_utils.py`) and writes `conf/cet_scenarios.json`. CET hourly volumes and speeds are in `conf/cet_volumes.csv` / `conf/cet_speeds.csv` (read by `cet_data.py`, volumes converted with `cet.pce`). `calibrate_stage1.py` uses the observed volume as demand (`demand: cet_volume`); Av. São Gabriel has no count, so its flow per level is bisected against the CET speed (`demand: speed_bisection`), falling back to the same 2019 hour when a year has no speed. Signalized topologies get green-wave offsets (`tlsCoordinator.py` → `osmFiles/<topology>.tls_offsets.add.xml`). One Krauss profile per topology (`krauss.bounds`, including `speedFactor`/`speedDev`) is chosen by TPE on `cet.calibration` (2019, 17–19h) and validated on every other observed level with a CET speed (2019 mornings, highway 2016, urban_road 2017), following Khaleghian et al. 2023 (see `docs/calibracao_etapa1.md`). Outputs `conf/route_flows_calibrated.json` (flow per route and level) and `conf/krauss_calibration.json` (profile + calibration/validation report). `calibration/simulation.py` runs SUMO for calibration. `--smoke` runs a minutes-long code check whose outputs are flagged and must not be used.
2. **Routes.** `generate_routes.py` writes `trafficXmlFiles/<topology>_<level>.rou.xml` and `.sumocfg` for each observed level of the topology and each synthetic factor (`cet.synthetic`), plus `conf/flow_levels.json`. That file and `src/flow_levels.py` are the contract for stages 2–4 and the plots: level ids (`y2019h07`, `s050`), labels, kind (observed/synthetic), and veh/h/lane per topology and level. Each topology has its own level list.
3. **Crypto timings.** `benchmark_crypto.py` builds `src/crypto_benchmark/ff1_benchmark.cc`, which reuses the same cipher sources as the Veins module, and writes `conf/crypto_timings.json`. Those medians become the per-protocol `cryptoTxDelay`/`cryptoRxDelay`. The timings come from the host machine, not an OBU, and the stage prints a warning about this.
4. **OMNeT++ config.** `generate_stage2_configs.py` joins `protocols.json` with `crypto_timings.json` (via `protocols.py`) and writes `conf/omnetpp.ini` plus one `conf/scenario_<topology>_<level>.launchd.xml` per scenario. Each run is a section `[Config Config_<topology>_<protocol>_<level>]` (e.g. `Config_urbanRoad_FF1_AES_y2019h07`). Stage 3 finds these sections by regex and stage 4 splits the level with `flow_levels.split_level`, so the naming scheme is a contract.
5. **Simulation.** The orchestrators build the plugin, start `veins_launchd` on port 9999 from `conf/`, and run `opp_run` in Cmdenv once per config and seed, with 6 parallel workers. Stdout and stderr go to `logs/<topology>/<config>/run_<seed>.{out,err}`, and results go to `results/rawData/`. `orchestrate_stage3_baseline.py` imports `orchestrate_stage3.py` and keeps only configs named `<topology>_baseline_*`.
6. **Analysis.** `analyze_stage4_results.py` runs `opp_scavetool` on the `.sca` files to produce `scavetool_export.csv`, then aggregates mean and 95% CI into `final_metrics_summary.csv` and plots. Protocol and topology are parsed from config names.

### C++ module (`src/latency`)

`LatencyTraCIDemo11p` extends Veins `TraCIDemo11p` and is loaded as a shared library, not as a standalone binary. It is the only place where simulated behavior is defined.

- **Encryption is real and timing is modeled.** With `cryptoCipher` set to anything other than `none`, each CAM is actually encrypted with FF1 on send and decrypted on receive, using plaintext built from fields the receiver sees. Simulated time advances only by `cryptoTxDelay`/`cryptoRxDelay`, not by the real computation time.
- **Single-core OBU model.** One FIFO CPU queue (`cpuBusyUntil`) is shared by TX and RX work. Queue wait, busy time, and deadline misses are recorded.
- **Metrics as bins.** Processing latency is a 200-bin histogram (`procBin_k`, 10 µs bins), which the stage 4 script reads. PDR is binned by distance. These are summable across nodes.
- **Warmup.** Statistics restart at `warmupTime`, but protocol state is kept.

`cam_model.json`, `antenna.xml`, and `vehicle_types.xml` in `conf/` set the CAM and radio parameters. The `.ned` files in `conf/` define the network.

## Conventions

- `conf/` is mostly gitignored. Only `routes.json`, `cet*.csv`, `protocols.json`, `calibration_params.json`, `crypto_timings.json`, `cam_model.json`, `coordinates_scenaries.json`, and the `pipelineSimulation/*.ned` file are tracked. Old TomTom data (`tomtom_*.json`, `jobs_*.json`) may still sit in `conf/`, unused. Generated files there are local artifacts, and deleting them forces the earlier stages to run again.
- `src/latency/Makefile`, `out/`, and `*.dylib` are generated and ignored.
- Stage 2 writes `omnetpp.ini` with `sim-time-limit = WARMUP + MEASUREMENT` and `repeat = 10`. Stage 3 reads `repeat` from that file rather than hardcoding it.
