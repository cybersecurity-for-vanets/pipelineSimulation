# Stage 1: Traffic Calibration Reproduction Guide

This guide details the sequential execution of Stage 1 traffic modeling, empirical calibration, and route generation using CET-SP MSVP survey data. All commands are executed from the repository root, configured by `config/calibration_params.json` (validated via `python -m src.traffic.params`).

---

## 1. Execution Order & Stage Dependencies

| Step | Operation | Command | Primary Inputs | Primary Outputs | Typical Runtime |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1.1** | Network Building & Topological Repair | `python -m src.traffic.fetch_network` | `data/routes.json` | `osmFiles/*.osm`, `osmFiles/*.net.xml`, `config/cet_scenarios.json` | ~2–5 min (OSM download) |
| **1.2** | Krauss Car-Following Model Calibration | `python -m src.traffic.calibrate_krauss` | `config/cet_scenarios.json`, `data/cet_*.csv` | `osmFiles/*.tls_offsets.add.xml`, `config/krauss_calibration.json` | `--smoke`: ~10 min; Full run: ~3–6 hours |
| **1.3** | HCM Level of Service Demand Sweep | `python -m src.traffic.map_los` | Outputs of Steps 1.1 and 1.2 | `config/los_sweep.json` | ~45–60 min (126 SUMO runs) |
| **1.4** | SUMO Route Generation | `python -m src.traffic.generate_routes` | Outputs of Steps 1.1, 1.2, 1.3 | `trafficXmlFiles/*.rou.xml`, `trafficXmlFiles/*.sumocfg`, `config/flow_levels.json` | < 10 seconds |

Subsequent execution proceeds to **Stage 2.0** (`src.crypto.benchmark`), **Stage 2.1** (`src.simulation.generate_configs`), **Stage 3.0** (`src.simulation.orchestrate`), and **Stage 4.0** (`src.analysis.generate_results`).

---

## 2. Trigger Conditions (When to Rerun)

- **Step 1.1 (`fetch_network`):** Rerun whenever `data/routes.json` or `network.*` parameters in `config/calibration_params.json` are modified.
  - `--skip-download`: Reuses existing `.osm` files in `osmFiles/`.
  - `--skip-netconvert`: Reruns route map-matching without recompiling `.net.xml`.
- **Step 1.2 (`calibrate_krauss`):** Rerun if CET input data (`data/cet_*.csv`) or calibration hyperparameters (`krauss.bounds`, `simulation.search_seeds`, `optuna` settings) are updated.
  - `--topology <NAME>`: Restricts calibration to a single topology (e.g., `highway`, `urban_road`, `intersection`).
  - `--trials <N>`: Conducts a rapid trial run with $N$ iterations.
  - `--verdict-only`: Re-evaluates validation criteria against recorded runs without re-simulating.
- **Step 1.3 (`map_los`):** Rerun if `los.factors` or HCM boundaries change in `config/calibration_params.json`.
  - `--factors`: Refines LOS boundary transitions without re-running the full sweep.
- **Step 1.4 (`generate_routes`):** Must be executed after Step 1.3 to update `trafficXmlFiles/` and the contract file `config/flow_levels.json`.

---

## 3. Useful Commands

```bash
# Validate configuration parameters
python -m src.traffic.params

# Fast smoke test of the calibration pipeline
python -m src.traffic.calibrate_krauss --smoke

# Calibrate a single topology with 10 Optuna trials
python -m src.traffic.calibrate_krauss --topology urban_road --trials 10

# Re-apply acceptance criteria without re-running SUMO
python -m src.traffic.calibrate_krauss --verdict-only
```
