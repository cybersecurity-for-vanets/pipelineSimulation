"""Master pipeline entry point for the POLI-USP VANET simulation framework.

Runs the entire experimental evaluation end-to-end:
  Stage 1.1: Fetch OpenStreetMap network & convert to SUMO (.net.xml)
  Stage 1.2: Krauss car-following model calibration using CET-SP traffic counts
  Stage 1.3: Demand sweep & HCM Level of Service (LOS) classification
  Stage 1.4: Generate SUMO routes and contract (flow_levels.json)
  Stage 2.0: C++ Cryptographic benchmark (FF1 with AES, LEA, SPECK)
  Stage 2.1: Generate OMNeT++ / Veins configurations (omnetpp.ini, launchd XMLs)
  Stage 3.0: Parallel simulation execution (OMNeT++ / Veins)
  Stage 4.0: Scavetool metric extraction, statistical aggregation & plotting
"""

import argparse
import os
import subprocess
import sys
import time

from src.env import RESULTS_DIR, ROOT_DIR


def run_step(step_name, module_name, extra_args=None):
    print(f"\n{'=' * 60}")
    print(f" [RUNNING] {step_name} (python -m {module_name})")
    print(f"{'=' * 60}")

    cmd = [sys.executable, "-m", module_name]
    if extra_args:
        cmd.extend(extra_args)

    start_time = time.time()
    result = subprocess.run(cmd, cwd=str(ROOT_DIR))
    elapsed = time.time() - start_time

    if result.returncode != 0:
        print(f"\n[CRITICAL ERROR] Step '{step_name}' failed with code {result.returncode}. Aborting.")
        sys.exit(result.returncode)

    print(f"[OK] {step_name} completed in {elapsed:.2f}s.")


def main():
    parser = argparse.ArgumentParser(description="POLI-USP Master's Thesis VANET Simulation Pipeline")
    parser.add_argument(
        "--protocol",
        default=None,
        help="Simulate only a specific protocol in Stage 3 (baseline, FF1_AES, FF1_LEA, FF1_SPECK)",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Simulate only the baseline protocol (no encryption) in Stage 3",
    )
    parser.add_argument(
        "--skip-stage1",
        action="store_true",
        help="Skip Stage 1 traffic calibration (reuse pre-calibrated routes in config/ & trafficXmlFiles/)",
    )
    parser.add_argument(
        "--skip-crypto",
        action="store_true",
        help="Skip Stage 2.0 crypto benchmark (reuse config/crypto_timings.json)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Number of concurrent simulation workers for Stage 3 (default: 6)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print(" POLI-USP VANET SIMULATION PIPELINE (v1.0)")
    print(" Matrix: 3 Topologies x Demand Levels (LOS) x 4 Protocols x 10 Seeds")
    print(" Protocols: baseline, FF1_AES, FF1_LEA, FF1_SPECK")
    print("=" * 60)

    # Stage 1: Traffic Modeling & Empirical Calibration
    if not args.skip_stage1:
        run_step("Stage 1.1 - OpenStreetMap Fetch & Network Building", "src.traffic.fetch_network")
        run_step("Stage 1.2 - Krauss Model Micro-Calibration (CET-SP)", "src.traffic.calibrate_krauss")
        run_step("Stage 1.3 - HCM Level of Service Sweep", "src.traffic.map_los")
        run_step("Stage 1.4 - SUMO Route & Scenario Generation", "src.traffic.generate_routes")
    else:
        print("\n[INFO] Skipping Stage 1 (Traffic Calibration).")

    # Stage 2.0: Cryptographic Benchmarking
    if not args.skip_crypto:
        run_step("Stage 2.0 - FF1 / Block Cipher Hardware Benchmark", "src.crypto.benchmark")
    else:
        print("\n[INFO] Skipping Stage 2.0 (Crypto Benchmark).")

    # Stage 2.1: OMNeT++ / Veins Configuration Generation
    run_step("Stage 2.1 - OMNeT++ / Veins Configuration Generation", "src.simulation.generate_configs")

    # Stage 3.0: Simulation Orchestration
    stage3_args = ["--workers", str(args.workers)]
    if args.baseline_only:
        stage3_args.append("--baseline-only")
    elif args.protocol:
        stage3_args.extend(["--protocol", args.protocol])

    run_step("Stage 3.0 - Parallel Simulation Orchestration", "src.simulation.orchestrate", stage3_args)

    # Stage 4.0: Statistical Analysis & Metric Extraction
    run_step("Stage 4.0 - Metric Extraction & Analysis", "src.analysis.extract_metrics")

    print("\n" + "=" * 60)
    print(" PIPELINE COMPLETED SUCCESSFULLY!")
    print(f" Results, tables, and figures available at: {RESULTS_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
