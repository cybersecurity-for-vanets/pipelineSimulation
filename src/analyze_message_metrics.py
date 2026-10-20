import argparse
import glob
import os
import subprocess
import sys

import numpy as np
import pandas as pd

# ==============================================================================
# 1. DIRETÓRIOS E CONFIGURAÇÕES (espelha analyze_stage4_results.py)
# ==============================================================================
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SSD_DIR = "/Volumes/PSSD T7/simulationData/"
if os.path.isdir(SSD_DIR):
    RESULTS_DIR = os.path.join(SSD_DIR, "results", "rawData")
else:
    RESULTS_DIR = os.path.join(PROJECT_DIR, "results", "rawData")
SCALAR_CSV = os.path.join(RESULTS_DIR, "scavetool_export.csv")
VECTOR_CSV = os.path.join(RESULTS_DIR, "message_vectors_export.csv")
PER_RUN_CSV = os.path.join(RESULTS_DIR, "message_metrics_per_run.csv")
SUMMARY_CSV = os.path.join(RESULTS_DIR, "message_metrics_summary.csv")

SCAVETOOL_BIN = "/Users/fabiotorres/omnetpp-6.2.0/bin/opp_scavetool"

MEASUREMENT_FALLBACK_S = 200.0  # MEASUREMENT_S em generate_stage2_configs.py; só usado sem o escalar measurementTimeS
BIN_MS = 0.01                 # resolução dos percentis: 10 us
DEADLINES_MS = (10.0, 100.0)  # platooning (10 ms) e ciclo de 10 Hz (100 ms), doc §4
MESSAGE_VECTORS = (
    "latencyEndToEndMs", "processingLatencyMs", "networkLatencyMs",
    "cpuQueueWaitMs", "ipgMs", "aoiMs", "camBits",
)


# ==============================================================================
# 2. EXPORTAÇÃO DOS VETORES POR MENSAGEM VIA SCAVETOOL
# ==============================================================================
def export_message_vectors():
    print("[1/4] Extraindo vetores por mensagem dos arquivos .vec via scavetool...")
    vec_files = glob.glob(os.path.join(RESULTS_DIR, "*.vec"))
    if not vec_files:
        print(f"[ERRO] Nenhum arquivo .vec encontrado em '{RESULTS_DIR}'. Execute a Etapa 3 com recordMessageVectors = true.")
        return False

    # Filtro por nome: só os vetores de mensagem, para não ler os demais vetores do OMNeT++.
    name_filter = " OR ".join(f"name({name})" for name in MESSAGE_VECTORS)
    cmd = [SCAVETOOL_BIN, "x", *vec_files, "-f", name_filter, "-o", VECTOR_CSV, "-F", "CSV-R"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[ERRO] Falha na execução do scavetool:\n{res.stderr}")
        return False
    print(f"[OK] Vetores salvos em: '{VECTOR_CSV}'")
    return True


# ==============================================================================
# 3. AGREGAÇÃO DOS EXPORTS (streaming, sem carregar o CSV inteiro)
# ==============================================================================
def parse_config(config_str):
    if not isinstance(config_str, str):
        return "Unknown", "Unknown", "Unknown"

    # Exemplo: Config_highway_FF1_AES_high_flow
    clean = config_str.replace("Config_", "")

    topologies = ["highway", "urbanRoad", "intersection"]
    flows = ["low_flow", "medium_flow", "high_flow"]

    top_found = "Unknown"
    for t in topologies:
        if clean.startswith(t):
            top_found = t
            clean = clean[len(t) + 1:]
            break

    flow_found = "Unknown"
    for f in flows:
        if clean.endswith(f):
            flow_found = f
            clean = clean[:-len(f) - 1]
            break

    return top_found, clean, flow_found


def aggregate_scalars(chunk_rows=500_000):
    """Soma, contagem e máximo de cada escalar por (run, nome); uma linha por nó no export."""
    columns = ["run", "type", "name", "attrname", "attrvalue", "value"]
    partial, configs = [], {}
    reader = pd.read_csv(SCALAR_CSV, usecols=columns, dtype=str, chunksize=chunk_rows)
    for chunk in reader:
        runattr = chunk[(chunk["type"] == "runattr") & (chunk["attrname"] == "configname")]
        configs.update(zip(runattr["run"], runattr["attrvalue"]))

        scalars = chunk.loc[chunk["type"] == "scalar", ["run", "name", "value"]]
        scalars = scalars.assign(value=pd.to_numeric(scalars["value"], errors="coerce")).dropna(subset=["value"])
        if not scalars.empty:
            partial.append(scalars.groupby(["run", "name"])["value"].agg(["sum", "count", "max"]))

    if not partial:
        return None, configs
    totals = pd.concat(partial).groupby(level=["run", "name"]).agg(
        {"sum": "sum", "count": "sum", "max": "max"})
    return totals, configs


def aggregate_vectors(chunk_rows=200_000):
    """Histograma esparso (passo BIN_MS) e momentos (soma, soma dos quadrados, contagem) por (run, vetor)."""
    histograms, moments = {}, {}
    reader = pd.read_csv(VECTOR_CSV, usecols=["run", "name", "vecvalue"], dtype=str, chunksize=chunk_rows)
    for chunk in reader:
        chunk = chunk[chunk["name"].isin(MESSAGE_VECTORS)]
        for run, name, raw in zip(chunk["run"], chunk["name"], chunk["vecvalue"]):
            if not isinstance(raw, str) or not raw.strip():
                continue
            values = np.array(raw.split(), dtype=float)
            key = (run, name)
            bins = pd.Series(np.floor(values / BIN_MS).astype(np.int64)).value_counts()
            histograms[key] = histograms.get(key, pd.Series(dtype="int64")).add(bins, fill_value=0)
            acc = moments.setdefault(key, [0.0, 0.0, 0])
            acc[0] += float(values.sum())
            acc[1] += float(np.square(values).sum())
            acc[2] += int(values.size)
    return histograms, moments


# ==============================================================================
# 4. MÉTRICAS POR EXECUÇÃO
# ==============================================================================
def percentile(histogram, q):
    histogram = histogram.sort_index()
    cumulative = histogram.cumsum().to_numpy()
    index = np.searchsorted(cumulative, q * cumulative[-1], side="left")
    return (histogram.index[index] + 0.5) * BIN_MS


def share_below(histogram, threshold_ms):
    """Fração de amostras com valor < threshold_ms (resolução de BIN_MS)."""
    limit = int(np.floor(threshold_ms / BIN_MS))
    return histogram[histogram.index < limit].sum() / histogram.sum()


def run_metrics(run, totals, histograms, moments):
    scalars = totals.xs(run, level="run")

    def total(name):
        return float(scalars.loc[name, "sum"]) if name in scalars.index else np.nan

    def mean(name):
        return total(name) / float(scalars.loc[name, "count"]) if name in scalars.index else np.nan

    def maximum(name):
        return float(scalars.loc[name, "max"]) if name in scalars.index else np.nan

    nodes = float(scalars.loc["cpuBusyTimeS", "count"])
    measurement_s = mean("measurementTimeS") if "measurementTimeS" in scalars.index else MEASUREMENT_FALLBACK_S
    rx_ops = total("cryptoRxOperationCount")
    tx_ops = total("cryptoTxOperationCount")
    expected = total("pdrExpectedReceptions")
    received = total("pdrReceivedReceptions")
    pdr = received / expected

    metrics = {
        "Nodes": nodes,
        "PDR": pdr,
        "CpuUtilizationPct": 100.0 * total("cpuBusyTimeS") / (nodes * measurement_s),
        "VerificationsPerNodeS": rx_ops / (nodes * measurement_s),
        "MaxCpuQueueLength": maximum("cpuMaxQueueLength"),
        "DeadlineMissRatio": total("processingDeadlineMisses") / (tx_ops + rx_ops),
        "MeanCamBytes": total("camBytesTotal") / total("camSentCount"),
        "MeanChannelBusyRatio": mean("meanChannelBusyRatio"),
        "FF1DecryptFailures": np.nan_to_num(total("ff1DecryptFailures")),
    }

    # Distribuições por mensagem. Frações são sobre as amostras gravadas; com stride > 1 o PDR com prazo
    # é a fração amostrada multiplicada pelo PDR (não contagem sobre pdrExpectedReceptions).
    latency = histograms.get((run, "latencyEndToEndMs"))
    if latency is not None:
        metrics["LatencyP50Ms"] = percentile(latency, 0.50)
        metrics["LatencyP95Ms"] = percentile(latency, 0.95)
        metrics["LatencyP99Ms"] = percentile(latency, 0.99)
        for deadline in DEADLINES_MS:
            share = share_below(latency, deadline)
            metrics[f"SharePacketsWithin{deadline:g}ms"] = share
            metrics[f"PdrWithin{deadline:g}ms"] = share * pdr

    for label, name in (("Processing", "processingLatencyMs"), ("Network", "networkLatencyMs")):
        histogram = histograms.get((run, name))
        if histogram is not None:
            metrics[f"{label}P95Ms"] = percentile(histogram, 0.95)
            metrics[f"{label}P99Ms"] = percentile(histogram, 0.99)

    queue = histograms.get((run, "cpuQueueWaitMs"))
    if queue is not None:
        metrics["CpuQueueWaitP95Ms"] = percentile(queue, 0.95)
        metrics["CpuQueueWaitP99Ms"] = percentile(queue, 0.99)

    # IPG (jitter e média) e AoI de pico: momentos exatos, sem depender da resolução do histograma.
    ipg = moments.get((run, "ipgMs"))
    if ipg is not None and ipg[2] > 0:
        ipg_mean = ipg[0] / ipg[2]
        metrics["IpgMeanMs"] = ipg_mean
        metrics["IpgJitterMs"] = np.sqrt(max(ipg[1] / ipg[2] - ipg_mean ** 2, 0.0))
    aoi = moments.get((run, "aoiMs"))
    if aoi is not None and aoi[2] > 0:
        metrics["AoIMeanMs"] = aoi[0] / aoi[2]

    return metrics


def summarize(per_run):
    id_columns = ["run", "Topology", "Protocol", "Scenario"]
    metric_columns = [c for c in per_run.columns if c not in id_columns]
    long = per_run.melt(
        id_vars=["Topology", "Protocol", "Scenario"], value_vars=metric_columns,
        var_name="Metric", value_name="Value").dropna(subset=["Value"])
    grouped = long.groupby(["Topology", "Protocol", "Scenario", "Metric"])["Value"].agg(
        Mean="mean", Std="std", Count="count").reset_index()
    grouped["CI_95"] = 1.96 * grouped["Std"] / np.sqrt(grouped["Count"])
    return grouped


# ==============================================================================
# 5. EXECUÇÃO
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Métricas por mensagem (percentis, PDR com prazo, carga de CPU).")
    parser.add_argument("--export", action="store_true",
                        help="roda opp_scavetool nos .vec antes de calcular as distribuições")
    args = parser.parse_args()

    if args.export and not export_message_vectors():
        sys.exit(1)

    if not os.path.exists(SCALAR_CSV):
        print(f"[ERRO] '{SCALAR_CSV}' não encontrado. Execute a Etapa 4 antes.")
        sys.exit(1)

    print("[2/4] Lendo escalares (métricas derivadas)...")
    totals, configs = aggregate_scalars()
    if totals is None:
        print("[ERRO] Nenhum escalar encontrado no export.")
        sys.exit(1)

    if os.path.exists(VECTOR_CSV):
        print("[3/4] Lendo vetores por mensagem (percentis)...")
        histograms, moments = aggregate_vectors()
    else:
        print("[AVISO] Sem message_vectors_export.csv: só métricas de escalares. Use --export.")
        histograms, moments = {}, {}

    print("[4/4] Calculando métricas por execução e resumo entre seeds...")
    rows = []
    for run in totals.index.get_level_values("run").unique():
        config = configs.get(run, str(run).split("-")[0])
        topology, protocol, scenario = parse_config(config)
        rows.append({"run": run, "Topology": topology, "Protocol": protocol, "Scenario": scenario,
                     **run_metrics(run, totals, histograms, moments)})

    per_run = pd.DataFrame(rows)
    per_run.to_csv(PER_RUN_CSV, index=False)
    summarize(per_run).to_csv(SUMMARY_CSV, index=False)
    print(f"[OK] Métricas por execução: '{PER_RUN_CSV}'")
    print(f"[OK] Resumo estatístico: '{SUMMARY_CSV}'")

    failures = per_run["FF1DecryptFailures"].sum()
    if failures > 0:
        print(f"[ALERTA] ff1DecryptFailures = {failures:.0f} (deveria ser 0).")


if __name__ == "__main__":
    main()
