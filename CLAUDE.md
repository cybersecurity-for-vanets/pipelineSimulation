# CLAUDE.md

This file provides guidance to assistant agents when working with code in this repository.

## Project

Simulation pipeline (SUMO + Veins + OMNeT++) measuring the computational and network latency costs of FF1 format-preserving encryption (AES/LEA/SPECK block ciphers) on V2X CAM messages across calibrated traffic scenarios. Developed for a Master's Thesis at POLI-USP.

The experimental matrix evaluates **3 topologies** (`highway`, `urbanRoad`, `intersection`) $\times$ **demand levels** (mapped to HCM Levels of Service A–F via `src.traffic.map_los`) $\times$ **4 protocols** (`baseline`, `FF1_AES`, `FF1_LEA`, `FF1_SPECK`) $\times$ **10 seeds** = **560 runs**. Protocol definitions reside in `config/protocols.json`.

All code and documentation are strictly in English.

## Commands

Python dependencies: `numpy`, `scipy`, `pandas`, `matplotlib`, `requests`, `optuna`, `sumolib`.

```bash
# Full pipeline end-to-end
python run_pipeline.py

# Individual stages
python -m src.traffic.fetch_network
python -m src.traffic.calibrate_krauss
python -m src.traffic.map_los
python -m src.traffic.generate_routes
python -m src.crypto.benchmark
python -m src.simulation.generate_configs
python -m src.simulation.orchestrate --baseline-only
python -m src.simulation.orchestrate
# Stage 4: Statistical Analysis, Publication PNGs & LaTeX Tables
python -m src.analysis.generate_results
```

Build the Veins latency plugin manually:
```bash
cd sim/src
opp_makemake --make-so -f -o LatencyPlugin -I "$VEINS_SRC" -L "$VEINS_SRC" -l veins
make MODE=release    # produces out/**/libLatencyPlugin.dylib
```

Run FF1 benchmark standalone:
```bash
bash sim/crypto_benchmark/run_benchmark.sh
```

## Architecture & Conventions

- `config/`: Central JSON configuration parameters and intermediate generated contracts (`flow_levels.json`, `los_sweep.json`, `krauss_calibration.json`).
- `data/`: Curated empirical traffic surveys from CET-SP (`cet_volumes.csv`, `cet_speeds.csv`) and route GPS definitions (`routes.json`).
- `sim/`: OMNeT++ / Veins simulation assets (`sim/omnetpp.ini`, `sim/ned/`, `sim/src/`, `sim/crypto_benchmark/`).
- `src/`: Modular Python codebase:
  - `src/env.py`: Single source of truth for path resolution and environment overrides (`OMNETPP_HOME`, `VEINS_HOME`, `SUMO_HOME`, `SIM_DATA_DIR`).
  - `src/traffic/`: OSM fetching, network repair, Krauss Optuna calibration, and route generation.
  - `src/crypto/`: C++ benchmark execution and statistical aggregation.
  - `src/simulation/`: Configuration generator and parallel orchestrator CLI.
  - `src/analysis/`: Scavetool metric extraction, paired differences, TOST equivalence tests, and plotting.
- `docs/`: Technical reports, mathematical metric definitions, and plotting guidelines in English.
- `results/`: Output data (`results/rawData/` and `results/plots/`).
