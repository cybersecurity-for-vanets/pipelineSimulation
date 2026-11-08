# V2X Simulation Pipeline: Impact of FF1 Format-Preserving Encryption on CAM Latency

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![SUMO: 1.20+](https://img.shields.io/badge/SUMO-1.20+-green.svg)](https://eclipse.dev/sumo/)
[![OMNeT++: 6.2.0](https://img.shields.io/badge/OMNeT++-6.2.0-orange.svg)](https://omnetpp.org/)
[![Veins: 5.3.1](https://img.shields.io/badge/Veins-5.3.1-red.svg)](https://veins.car2x.org/)

Experimental simulation framework developed for a Master's Thesis at the **Escola Politécnica da Universidade de São Paulo (POLI-USP)**.

This pipeline evaluates the end-to-end computational and physical channel impacts of **NIST SP 800-38G FF1 Format-Preserving Encryption (FPE)** on European Telecommunications Standards Institute Cooperative Awareness Messages (**ETSI CAM**). The ciphers evaluated include **AES-128**, **LEA-128**, and **SPECK-128/128**, simulated across realistic traffic scenarios in São Paulo calibrated against open empirical data from **CET-SP** and classified by **HCM Levels of Service (LOS)**.

---

## 1. Experimental Matrix

The experimental evaluation comprises **56 unique configurations** executed over **10 random seeds (560 total simulation runs)**:

- **Topologies (3):**
  - `highway`: Av. 23 de Maio (2,498 m arterial expressway, 6 demand levels: LOS A–F)
  - `urbanRoad`: R. da Consolação (1,922 m signalized urban corridor, 4 demand levels: LOS C–F)
  - `intersection`: Av. Nove de Julho $\times$ Av. São Gabriel (signalized crossing, 4 demand levels: LOS A, B, C, F)
- **Protocols (4):**
  - `baseline`: Standard unencrypted CAM with ECDSA P-256 signature
  - `FF1_AES`: Selective CAM field encryption via FF1 over AES-128
  - `FF1_LEA`: Selective CAM field encryption via FF1 over LEA-128
  - `FF1_SPECK`: Selective CAM field encryption via FF1 over SPECK-128/128

Because FF1 preserves ciphertext length and radix, encrypted CAM payloads incur **0 bytes of network transmission overhead**, preventing physical layer fragmentation. The protocols differ solely by cryptographic processing delay ($T_{\text{tx\_delay}}$ and $T_{\text{rx\_delay}}$).

---

## 2. Directory Structure

```text
├── config/                 # Calibration hyperparameters and protocol profiles
├── data/                   # Empirical CET-SP survey datasets and GPS routes
├── docs/                   # Technical documentation and methodology (English)
├── sim/                    # OMNeT++ / Veins simulation models and C++ ciphers
│   ├── ned/                # Network description files (.ned)
│   ├── src/                # C++ Veins application & block cipher sources
│   └── crypto_benchmark/   # Standalone C++ benchmark & NIST test vectors
├── src/                    # Modular Python pipeline package
│   ├── env.py              # Centralized environment & path resolver
│   ├── pipeline.py         # Master pipeline orchestrator
│   ├── traffic/            # Stage 1: Network repair, Krauss calibration, LOS sweep
│   ├── crypto/             # Stage 2.0: FF1 timing benchmark & aggregation
│   ├── simulation/         # Stages 2 & 3: Config generator & parallel orchestrator
│   └── analysis/           # Stage 4: Scavetool extraction, statistics & plotting
├── requirements.txt        # Python package dependencies
├── environment.yml         # Conda environment specification
└── run_pipeline.py         # Root convenience execution script
```

---

## 3. Prerequisites & Installation

### System Requirements
1. **C++17 Compiler:** `clang++` or `g++` with OpenSSL 3 (`libcrypto`).
2. **Traffic Simulator:** [Eclipse SUMO](https://eclipse.dev/sumo/) (v1.20+) with `SUMO_HOME` set.
3. **Network Simulator:** [OMNeT++](https://omnetpp.org/) (v6.2.0) and [Veins](https://veins.car2x.org/) (v5.3.1).

### Python Setup
```bash
# Clone the repository
git clone https://github.com/fabiotorres/pipelineSimulation.git
cd pipelineSimulation

# Install Python dependencies
pip install -r requirements.txt
```

### Environment Configuration (Optional)
By default, the pipeline resolves paths relative to the repository. External paths can be customized via environment variables:
```bash
export OMNETPP_HOME="/path/to/omnetpp-6.2.0"
export VEINS_HOME="/path/to/veins-veins-5.3.1"
export SUMO_HOME="/usr/local/share/sumo"
export SIM_DATA_DIR="/Volumes/ExternalSSD"  # Optional external drive for heavy traces
```

---

## 4. Execution

### Full Pipeline End-to-End
Execute the complete pipeline sequentially from the repository root:
```bash
python run_pipeline.py
```

### Modular Stage Execution
Individual stages can be executed independently:

```bash
# Stage 1: Traffic Calibration & LOS Mapping
python -m src.traffic.fetch_network       # 1.1 Download OSM & build SUMO network
python -m src.traffic.calibrate_krauss     # 1.2 Krauss micro-calibration via Optuna
python -m src.traffic.map_los              # 1.3 HCM Level of Service demand sweep
python -m src.traffic.generate_routes      # 1.4 Generate SUMO routes & flow_levels.json

# Stage 2: Hardware Benchmark & Config Generation
python -m src.crypto.benchmark             # 2.0 Benchmark FF1 and verify NIST test vectors
python -m src.simulation.generate_configs  # 2.1 Generate omnetpp.ini & launchd XMLs

# Stage 3: Simulation Orchestration
python -m src.simulation.orchestrate                    # Run full matrix (560 runs)
python -m src.simulation.orchestrate --baseline-only    # Run baseline only
python -m src.simulation.orchestrate --protocol FF1_AES # Run specific cipher

# Stage 4: Statistical Analysis, Publication PNGs & LaTeX Tables
python -m src.analysis.generate_results    # Unified entry point: Publication PNGs & LaTeX summary tables
```

---

## 5. Technical Documentation

- [Stage 1: Traffic Calibration Methodology](docs/stage1_traffic_calibration.md)
- [Stage 1: Calibration Reproduction Guide](docs/calibration_reproduction.md)
- [Stage 2: Cryptographic Evaluation & Lead Times](docs/stage2_crypto_evaluation.md)
- [Stage 4: Mathematical Metric Definitions (PDR, CBR, Latency)](docs/stage4_metrics_definition.md)
- [Stage 4: Experimental Results & Findings](docs/stage4_analysis.md)
- [Visual Standards & Plotting Guidelines](docs/plotting_guidelines.md)
- [Academic Literature & Benchmarks](docs/references.md)

---

## 6. Citation & License

This project is licensed under the [MIT License](LICENSE).

If you use this simulation framework or calibration methodology in your research, please cite:
```bibtex
@mastersthesis{madueno2026v2x,
  author       = {Fabio Madueno},
  title        = {Impact of Format-Preserving Encryption on Vehicular Communication Latency under Realistic Calibrated Traffic},
  school       = {Escola Politécnica da Universidade de São Paulo (POLI-USP)},
  year         = {2026},
  address      = {São Paulo, Brazil}
}
```
