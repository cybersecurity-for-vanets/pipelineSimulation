"""Stage 3: Parallel OMNeT++ / Veins simulation orchestrator.

Executes the experimental matrix across topologies, flow levels, and cryptographic protocols.
Supports full execution, single protocol (--protocol), baseline only (--baseline-only),
and configurable parallel workers.
"""

import argparse
import concurrent.futures
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from src.env import (
    CONFIG_DIR,
    DATA_STORAGE_DIR,
    DEFAULT_EXTERNAL_DIR,
    LOGS_DIR,
    OPP_MAKEMAKE,
    OPP_RUN,
    RAW_DATA_DIR,
    ROOT_DIR,
    SIM_DIR,
    SUMO_BINARY,
    VEINS_HOME,
    VEINS_LAUNCHD,
    VEINS_LIB,
    VEINS_SRC,
)
from src.simulation.protocols import load_protocols

INI_FILE = SIM_DIR / "omnetpp.ini"
SCENARIOS_CONF_PATH = CONFIG_DIR / "coordinates_scenaries.json"
LATENCY_APP_DIR = SIM_DIR / "src"
FULL_NED_PATH = f"{VEINS_HOME}/ned:{SIM_DIR}/ned"

LATENCY_PLUGIN_LIB = None
ACTIVE_LOGS_DIR = LOGS_DIR
ACTIVE_RESULTS_DIR = RAW_DATA_DIR


def get_free_space_gb(path):
    target = Path(path)
    while not target.exists() and target.parent != target:
        target = target.parent
    try:
        return shutil.disk_usage(target).free / (1024 ** 3)
    except Exception:
        return 0.0


def setup_storage(storage_preference="auto", min_gb_required=10.0):
    global ACTIVE_LOGS_DIR, ACTIVE_RESULTS_DIR

    ssd_mounted = DEFAULT_EXTERNAL_DIR.is_dir()
    ssd_free_gb = get_free_space_gb(DEFAULT_EXTERNAL_DIR) if ssd_mounted else 0.0
    local_free_gb = get_free_space_gb(ROOT_DIR)

    if storage_preference == "ssd":
        if not ssd_mounted:
            raise FileNotFoundError(f"External SSD is not mounted at '{DEFAULT_EXTERNAL_DIR}'.")
        if ssd_free_gb < min_gb_required:
            raise OSError(
                f"SSD '{DEFAULT_EXTERNAL_DIR}' has only {ssd_free_gb*1024:.1f} MB free (min required: {min_gb_required:.1f} GB)."
            )
        chosen = DEFAULT_EXTERNAL_DIR
    elif storage_preference == "local":
        if local_free_gb < min_gb_required:
            raise OSError(
                f"Local storage has only {local_free_gb:.1f} GB free (min required: {min_gb_required:.1f} GB)."
            )
        chosen = ROOT_DIR
    else:  # auto
        if ssd_mounted and ssd_free_gb >= min_gb_required:
            chosen = DEFAULT_EXTERNAL_DIR
        elif local_free_gb >= min_gb_required:
            if ssd_mounted and ssd_free_gb < min_gb_required:
                print(f"[WARN] External SSD is nearly full ({ssd_free_gb*1024:.1f} MB free). Falling back to local storage.")
            chosen = ROOT_DIR
        else:
            raise OSError(
                f"Insufficient disk space in all destinations! (SSD: {ssd_free_gb*1024:.1f} MB, Local: {local_free_gb:.1f} GB). "
                f"Minimum required: {min_gb_required:.1f} GB."
            )

    ACTIVE_LOGS_DIR = chosen / "logs"
    ACTIVE_RESULTS_DIR = chosen / "results" / "rawData"

    ACTIVE_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[STORAGE] Target: {chosen}")
    print(f"          LOGS_DIR:    {ACTIVE_LOGS_DIR}")
    print(f"          RESULTS_DIR: {ACTIVE_RESULTS_DIR}")
    print(f"          Free space:  {get_free_space_gb(ACTIVE_RESULTS_DIR):.2f} GB")
    return chosen


def load_known_topologies():
    if SCENARIOS_CONF_PATH.is_file():
        with open(SCENARIOS_CONF_PATH, "r", encoding="utf-8") as f:
            return list(json.load(f).keys())
    return ["highway", "urbanRoad", "intersection"]


def parse_omnetpp_ini(ini_path=INI_FILE):
    if not Path(ini_path).is_file():
        raise FileNotFoundError(f"Simulation configuration not found: {ini_path}; run generate_configs first")

    with open(ini_path, "r", encoding="utf-8") as f:
        content = f.read()

    repeat_match = re.search(r"^\s*repeat\s*=\s*(\d+)", content, re.MULTILINE)
    repeat_count = int(repeat_match.group(1)) if repeat_match else 10

    config_matches = re.findall(r"^\s*\[Config\s+(Config_[^\]]+)\]", content, re.MULTILINE)
    configs = sorted(list(set(config_matches)))

    return configs, repeat_count


def filter_configs_by_protocol(configs, topologies, target_protocol):
    if not target_protocol:
        return configs
    selected = []
    for config_name in configs:
        top_id = resolve_topology_id(config_name, topologies)
        remainder = config_name.replace("Config_", "", 1)
        if remainder.startswith(f"{top_id}_{target_protocol}_"):
            selected.append(config_name)
    return selected


def resolve_topology_id(config_name, known_topologies):
    clean_name = config_name.replace("Config_", "")
    for top_id in known_topologies:
        if clean_name.startswith(top_id):
            return top_id
    return "general"


def find_latency_plugin():
    candidates = glob.glob(
        str(LATENCY_APP_DIR / "out" / "**" / "libLatencyPlugin.dylib"),
        recursive=True,
    )
    if not candidates:
        raise FileNotFoundError("LatencyPlugin.dylib not found; run without --skip-build")
    return candidates[0]


def build_latency_plugin():
    subprocess.run(
        [
            str(OPP_MAKEMAKE),
            "--make-so",
            "-f",
            "-o",
            "LatencyPlugin",
            "-I",
            str(VEINS_SRC),
            "-L",
            str(VEINS_SRC),
            "-l",
            "veins",
        ],
        cwd=str(LATENCY_APP_DIR),
        check=True,
    )
    subprocess.run(["make", "MODE=release"], cwd=str(LATENCY_APP_DIR), check=True)
    return find_latency_plugin()


def start_launchd():
    print("[LAUNCHD] Starting veins_launchd daemon on port 9999...")
    launchd_log_path = ACTIVE_LOGS_DIR / "veins_launchd.log"
    ACTIVE_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(launchd_log_path, "a", encoding="utf-8")
    cmd = [sys.executable, str(VEINS_LAUNCHD), "-v", "-p", "9999", "-c", str(SUMO_BINARY)]
    process = subprocess.Popen(cmd, cwd=str(SIM_DIR), stdout=log_file, stderr=log_file)
    time.sleep(2)
    return process, log_file


def run_simulation(args):
    config_name, seed, top_id = args

    out_log_dir = ACTIVE_LOGS_DIR / top_id / config_name
    try:
        out_log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"[FAIL] {config_name} | Seed {seed:02d} (Error creating dir: {e})"

    cmd = [
        str(OPP_RUN),
        "-l", str(VEINS_LIB),
        "-l", str(LATENCY_PLUGIN_LIB),
        "-n", FULL_NED_PATH,
        "-u", "Cmdenv",
        "-c", config_name,
        "-r", str(seed),
        f"--result-dir={ACTIVE_RESULTS_DIR}",
        str(INI_FILE),
    ]

    stdout_path = out_log_dir / f"run_{seed}.out"
    stderr_path = out_log_dir / f"run_{seed}.err"

    try:
        with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
            proc = subprocess.run(cmd, stdout=out, stderr=err, cwd=str(SIM_DIR))
    except OSError as e:
        return f"[FAIL] {config_name} | Seed {seed:02d} (I/O Error: {e})"

    if proc.returncode == 0:
        return f"[OK] {config_name} | Seed {seed:02d}"
    else:
        return f"[FAIL] {config_name} | Seed {seed:02d} (Code {proc.returncode})"


def main():
    global LATENCY_PLUGIN_LIB

    available_protocols = list(load_protocols().keys())
    parser = argparse.ArgumentParser(description="OMNeT++ / Veins Stage 3 Orchestrator")
    parser.add_argument(
        "--storage",
        choices=["auto", "ssd", "local"],
        default="auto",
        help="Data storage destination (default: 'auto')",
    )
    parser.add_argument("--workers", type=int, default=6, help="Concurrent worker threads (default: 6)")
    parser.add_argument("--min-gb", type=float, default=10.0, help="Minimum free GB required (default: 10.0)")
    parser.add_argument(
        "--protocol",
        choices=available_protocols,
        default=None,
        help="Restrict simulation to a specific protocol",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Convenience alias to simulate only the 'baseline' protocol",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Reuse existing compiled Veins LatencyPlugin without rebuilding",
    )
    args = parser.parse_args()

    target_protocol = "baseline" if args.baseline_only else args.protocol

    setup_storage(storage_preference=args.storage, min_gb_required=args.min_gb)

    if args.skip_build:
        LATENCY_PLUGIN_LIB = find_latency_plugin()
    else:
        print("[BUILD] Compiling C++ Latency plugin...")
        LATENCY_PLUGIN_LIB = build_latency_plugin()
    print(f"[BUILD] Plugin library: {LATENCY_PLUGIN_LIB}")

    topologies = load_known_topologies()
    all_configs, repeat_count = parse_omnetpp_ini()
    configs = filter_configs_by_protocol(all_configs, topologies, target_protocol)

    if not configs:
        msg = f"No configs found for protocol '{target_protocol}' in omnetpp.ini." if target_protocol else "No configs found in omnetpp.ini."
        print(f"[ERROR] {msg}")
        return 1

    tasks = []
    for config_name in configs:
        top_id = resolve_topology_id(config_name, topologies)
        for seed in range(repeat_count):
            tasks.append((config_name, seed, top_id))

    print(f"=== [Stage 3] OMNeT++ / Veins Simulation Execution ===")
    print(f"Filter Protocol: {target_protocol or 'ALL'}")
    print(f"Active Configs:  {len(configs)} (out of {len(all_configs)})")
    print(f"Seeds / Config:  {repeat_count}")
    print(f"Total Runs:      {len(tasks)}")
    print(f"Parallel Workers:{args.workers}")

    launchd_proc, log_file = start_launchd()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(run_simulation, task) for task in tasks]
            for future in concurrent.futures.as_completed(futures):
                try:
                    print(future.result())
                except Exception as e:
                    print(f"[FAIL] Simulation exception: {e}")
    finally:
        print("[LAUNCHD] Stopping veins_launchd daemon...")
        launchd_proc.terminate()
        log_file.close()

    print("[OK] Stage 3 finished successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
