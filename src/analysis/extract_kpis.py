"""Per-run KPI extraction from the OMNeT++ scalar files (.sca) written by Stage 3.

Every vehicle's application (LatencyTraCIDemo11p) records running totals and counters at the end of the
run (see LatencyTraCIDemo11p::finish). This module sums those scalars over all vehicles of a run and turns
them into the long-format table consumed by Stage 4 (one row per run x KPI):

    run, Topology, Protocol, Scenario, KPI, Value

The .sca files are parsed directly (no scavetool export), one process per file.
"""

import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis.flow_levels import is_current_config, split_level
from src.simulation.generate_configs import MEASUREMENT_S

TOPOLOGIES = ["highway", "urbanRoad", "intersection"]

# Mirrors LatencyTraCIDemo11p.h
PROCESSING_BINS = 200          # bins 0..199 of 10 us, bin 200 = overflow (>= 2 ms)
PROCESSING_BIN_MS = 0.01
PDR_DISTANCE_BINS = 6
PDR_DISTANCE_BIN_M = 50

PROC_BIN_PREFIX = "procBin_"
SUMMED_SCALARS = {
    "packetLatencyTotalMs", "packetLatencySamples",
    "processingLatencyTotalMs", "networkLatencyTotalMs",
    "pdrExpectedReceptions", "pdrReceivedReceptions",
    "meanChannelBusyRatio",
    "measurementTimeS", "cpuBusyTimeS",
    "cpuQueueWaitTotalS", "cpuQueueSamples", "processingDeadlineMisses",
    "cryptoTxDelayTotalMs", "cryptoTxOperationCount",
    "cryptoRxDelayTotalMs", "cryptoRxOperationCount",
    "camBytesTotal", "camSentCount",
    "ff1DecryptFailures",
} | {f"pdr{kind}_d{i}" for kind in ("Expected", "Received") for i in range(PDR_DISTANCE_BINS)}

SCALAR_LINE = re.compile(r"^scalar \S+ (\S+) (\S+)$")


def parse_config(config_name):
    """'Config_highway_FF1_AES_s015' -> ('highway', 'FF1_AES', 's015'); None if not a current config."""
    if not is_current_config(config_name):
        return None
    clean, level = split_level(config_name.removeprefix("Config_"))
    topology = next((t for t in TOPOLOGIES if clean.startswith(t + "_")), None)
    if topology is None or level is None:
        return None
    return topology, clean[len(topology) + 1:], level


def read_sca(path):
    """Sum (and count) every relevant scalar over all modules of one .sca file."""
    run_id, config_name = None, None
    sums, counts = {}, {}
    with open(path, "r", encoding="utf-8", errors="replace") as source:
        for line in source:
            if line.startswith("scalar "):
                match = SCALAR_LINE.match(line.rstrip("\n"))
                if match is None:
                    continue
                name, raw = match.groups()
                if name not in SUMMED_SCALARS and not name.startswith(PROC_BIN_PREFIX):
                    continue
                value = float(raw)
                if not np.isfinite(value):
                    continue
                sums[name] = sums.get(name, 0.0) + value
                counts[name] = counts.get(name, 0) + 1
            elif line.startswith("run "):
                run_id = line.split(" ", 1)[1].strip()
            elif line.startswith("attr configname "):
                config_name = line.split(" ", 2)[2].strip()
    return run_id, config_name, sums, counts


def _ratio(numerator, denominator, scale=1.0):
    return scale * numerator / denominator if denominator > 0 else np.nan


def _histogram_percentile(bins, q):
    """Percentile (ms) of the processing-latency histogram, taken at the bin centre."""
    total = bins.sum()
    if total <= 0:
        return np.nan
    index = int(np.searchsorted(np.cumsum(bins), q * total, side="left"))
    return (min(index, PROCESSING_BINS) + 0.5) * PROCESSING_BIN_MS


def run_kpis(sums, counts):
    """Per-run KPIs from the summed scalars (definitions follow LatencyTraCIDemo11p.cc)."""
    s = lambda name: sums.get(name, 0.0)  # noqa: E731
    proc_bins = np.array([s(f"{PROC_BIN_PREFIX}{i}") for i in range(PROCESSING_BINS + 1)])
    received_cams = proc_bins.sum()  # one processing sample per received CAM

    kpis = {
        # E2E: reception time - CAM creation time, averaged over every received CAM of the run.
        "Latency (ms)": _ratio(s("packetLatencyTotalMs"), s("packetLatencySamples")),
        # Processing = TX (sign + encrypt) + RX (verify + decrypt), including CPU queueing; network = E2E - processing.
        "Processing latency (ms)": _ratio(s("processingLatencyTotalMs"), received_cams),
        "Processing latency p95 (ms)": _histogram_percentile(proc_bins, 0.95),
        "Processing latency p99 (ms)": _histogram_percentile(proc_bins, 0.99),
        "Network latency (ms)": _ratio(s("networkLatencyTotalMs"), received_cams),
        "PDR (%)": _ratio(s("pdrReceivedReceptions"), s("pdrExpectedReceptions"), 100.0),
        # Mean over vehicles of the CBR measured by each vehicle during the measurement window.
        "Channel Busy Rate (%)": _ratio(s("meanChannelBusyRatio"), counts.get("meanChannelBusyRatio", 0), 100.0),
        # Share of vehicle-time the single (FIFO) crypto core was busy.
        "CPU utilization (%)": _ratio(s("cpuBusyTimeS"), s("measurementTimeS"), 100.0),
        "Mean queue wait (ms)": _ratio(s("cpuQueueWaitTotalS"), s("cpuQueueSamples"), 1000.0),
        "Deadline misses (%)": _ratio(s("processingDeadlineMisses"), s("cpuQueueSamples"), 100.0),
        "Crypto TX processing (ms)": _ratio(s("cryptoTxDelayTotalMs"), s("cryptoTxOperationCount")),
        "Crypto RX processing (ms)": _ratio(s("cryptoRxDelayTotalMs"), s("cryptoRxOperationCount")),
        "Bytes per CAM": _ratio(s("camBytesTotal"), s("camSentCount")),
        "FF1 decrypt failures": s("ff1DecryptFailures"),
        # Mean number of vehicles present during the measurement window.
        "Active vehicles": s("measurementTimeS") / MEASUREMENT_S,
    }
    for i in range(PDR_DISTANCE_BINS):
        low, high = i * PDR_DISTANCE_BIN_M, (i + 1) * PDR_DISTANCE_BIN_M
        kpis[f"PDR {low}-{high} m (%)"] = _ratio(s(f"pdrReceived_d{i}"), s(f"pdrExpected_d{i}"), 100.0)
    return kpis


def _extract_one(path):
    run_id, config_name, sums, counts = read_sca(path)
    parsed = parse_config(config_name) if config_name else None
    if run_id is None or parsed is None:
        return path, []
    topology, protocol, scenario = parsed
    base = {"run": run_id, "Topology": topology, "Protocol": protocol, "Scenario": scenario}
    return path, [{**base, "KPI": kpi, "Value": value} for kpi, value in run_kpis(sums, counts).items()]


def extract_run_values(raw_dir, workers=None):
    """Parse every .sca of the current flow levels in raw_dir into the long-format per-run KPI table."""
    sca_files = sorted(Path(raw_dir).glob("*.sca"))
    if not sca_files:
        raise FileNotFoundError(f"Nenhum arquivo .sca encontrado em '{raw_dir}'. Execute a Etapa 3 primeiro.")

    print(f"[INFO] Lendo {len(sca_files)} arquivos .sca de {raw_dir}...")
    rows, ignored = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for done, (path, run_rows) in enumerate(pool.map(_extract_one, sca_files, chunksize=4), start=1):
            if run_rows:
                rows.extend(run_rows)
            else:
                ignored.append(path.name)
            if done % 100 == 0:
                print(f"  [{done}/{len(sca_files)}] arquivos processados")

    if ignored:
        print(f"[AVISO] Ignorados {len(ignored)} .sca fora de config/flow_levels.json, ex.: {ignored[:3]}")
    if not rows:
        raise ValueError(f"Nenhuma execução dos níveis atuais encontrada em '{raw_dir}'.")

    run_values = pd.DataFrame(rows)
    print(f"[OK] {run_values['run'].nunique()} execuções extraídas "
          f"({run_values['KPI'].nunique()} KPIs por execução).")
    return run_values
