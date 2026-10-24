import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from .protocols import load_protocols
except ImportError:
    from protocols import load_protocols

# ==============================================================================
# 1. DIRETÓRIOS E CONFIGURAÇÕES
# ==============================================================================
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAVETOOL_CSV = os.path.join(PROJECT_DIR, "results", "rawData", "scavetool_export.csv")
OUTPUT_CSV = os.path.join(PROJECT_DIR, "results", "rawData", "flow_kpis_by_topology.csv")
OUTPUT_SEED_CSV = os.path.join(PROJECT_DIR, "results", "rawData", "flow_kpis_per_seed.csv")
PLOTS_DIR = os.path.join(PROJECT_DIR, "results", "plots", "flow_kpis")

TOPOLOGIES = ["highway", "urbanRoad", "intersection"]
FLOWS = ["low_flow", "medium_flow", "high_flow"]
PROTOCOLS = list(load_protocols())
METRICS = ["PDR (%)", "CBR (%)", "Latency E2E (ms)"]

# Escalares usados (ver LatencyTraCIDemo11p.cc). Latência E2E = packetLatencyTotalMs / packetLatencySamples,
# cada amostra é (instante de recepção - instante de criação) do CAM.
SCALARS = {
    "pdrExpectedReceptions", "pdrReceivedReceptions",
    "packetLatencyTotalMs", "packetLatencySamples",
    "channelBusy:timeavg",
}

# ==============================================================================
# 2. LEITURA EM STREAMING DO SCAVETOOL EXPORT
# ==============================================================================
def parse_config(config_name):
    if not isinstance(config_name, str):
        return None

    clean_name = config_name.removeprefix("Config_")
    topology = next((value for value in TOPOLOGIES if clean_name.startswith(value + "_")), None)
    if topology is None:
        return None

    remainder = clean_name[len(topology) + 1:]
    flow = next((value for value in FLOWS if remainder.endswith("_" + value)), None)
    if flow is None:
        return None

    protocol = remainder[:-(len(flow) + 1)]
    return topology, protocol, flow


def aggregate_export(chunk_rows=500_000):
    """Soma (sum, count) por (run, escalar). Cada linha do export é um nó, então agregamos por execução."""
    columns = ["run", "type", "module", "name", "attrname", "attrvalue", "value"]
    partial, configs, seeds = [], {}, {}
    reader = pd.read_csv(SCAVETOOL_CSV, usecols=columns, dtype=str, chunksize=chunk_rows)
    for index, chunk in enumerate(reader, start=1):
        runattr = chunk[chunk["type"] == "runattr"]
        configs.update(zip(runattr.loc[runattr["attrname"] == "configname", "run"],
                           runattr.loc[runattr["attrname"] == "configname", "attrvalue"]))
        seed_attr = runattr[runattr["attrname"] == "seedset"]
        seeds.update(zip(seed_attr["run"], seed_attr["attrvalue"]))

        scalars = chunk[(chunk["type"] == "scalar") & chunk["name"].isin(SCALARS)]
        scalars = scalars.assign(value=pd.to_numeric(scalars["value"], errors="coerce")).dropna(subset=["value"])
        if not scalars.empty:
            partial.append(scalars.groupby(["run", "name"])["value"].agg(["sum", "count"]))
        if index % 20 == 0:
            print(f"[INFO] {index * chunk_rows:,} linhas lidas...")

    if not partial:
        raise ValueError("Nenhum escalar relevante encontrado no scavetool export.")
    combined = pd.concat(partial).groupby(level=["run", "name"]).sum()
    return combined["sum"].unstack("name"), combined["count"].unstack("name"), configs, seeds

# ==============================================================================
# 3. KPIs POR EXECUÇÃO E AGREGAÇÃO (MÉDIA E IC 95%)
# ==============================================================================
def value_of(row, name):
    return row[name] if name in row.index and pd.notna(row[name]) else np.nan


def kpis_per_run(sums, counts, configs, seeds):
    rows = []
    for run, config in configs.items():
        parsed = parse_config(config)
        if parsed is None or run not in sums.index:
            continue
        topology, protocol, flow = parsed
        s, c = sums.loc[run], counts.loc[run]

        expected = value_of(s, "pdrExpectedReceptions")
        received = value_of(s, "pdrReceivedReceptions")
        latency_total = value_of(s, "packetLatencyTotalMs")
        latency_samples = value_of(s, "packetLatencySamples")
        cbr_sum = value_of(s, "channelBusy:timeavg")
        cbr_count = value_of(c, "channelBusy:timeavg")

        rows.append({
            "run": run,
            "Seed": int(seeds[run]) if run in seeds else np.nan,
            "Topology": topology,
            "Scenario": flow,
            "Protocol": protocol,
            # PDR agregado da execução: recepções esperadas e recebidas somadas sobre todos os nós.
            "PDR (%)": 100 * received / expected if expected > 0 else np.nan,
            # CBR da execução: média dos nós (cada nó reporta channelBusy:timeavg).
            "CBR (%)": 100 * cbr_sum / cbr_count if cbr_count > 0 else np.nan,
            "Latency E2E (ms)": latency_total / latency_samples if latency_samples > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def ordered(frame):
    """Aplica a ordem de topologias, fluxos e protocolos (a mesma das listas do script)."""
    frame = frame.copy()
    frame["Topology"] = pd.Categorical(frame["Topology"], TOPOLOGIES, ordered=True)
    frame["Scenario"] = pd.Categorical(frame["Scenario"], FLOWS, ordered=True)
    frame["Protocol"] = pd.Categorical(frame["Protocol"], PROTOCOLS, ordered=True)
    return frame.sort_values(["Topology", "Scenario", "Protocol"]).reset_index(drop=True)


def summarize(run_kpis):
    grouped = run_kpis.groupby(["Topology", "Scenario", "Protocol"])[METRICS]
    summary = grouped.agg(["mean", "std", "count"])
    summary.columns = [f"{metric}|{stat}" for metric, stat in summary.columns]
    summary = summary.reset_index()

    out = summary[["Topology", "Scenario", "Protocol"]].copy()
    out["N"] = summary["PDR (%)|count"]
    for metric in METRICS:
        out[metric] = summary[f"{metric}|mean"]
        out[f"{metric} CI95"] = 1.96 * summary[f"{metric}|std"].fillna(0) / np.sqrt(summary[f"{metric}|count"])
    return ordered(out)

# ==============================================================================
# 4. GRÁFICO DE DISPERSÃO POR SEMENTE
# ==============================================================================
def plot_seed_scatter(run_kpis):
    """Um gráfico por topologia: cada ponto é uma semente; a marca preta é a média do grupo."""
    os.makedirs(PLOTS_DIR, exist_ok=True)
    flow_position = {flow: index for index, flow in enumerate(FLOWS)}
    spread = 0.2 / max(len(PROTOCOLS), 1)

    for topology in TOPOLOGIES:
        data = run_kpis[run_kpis["Topology"] == topology]
        if data.empty:
            continue

        figure, axes = plt.subplots(1, len(METRICS), figsize=(5 * len(METRICS), 4.5))
        for axis, metric in zip(axes, METRICS):
            for offset, protocol in enumerate(PROTOCOLS):
                group = data[data["Protocol"] == protocol]
                if group.empty:
                    continue
                x = group["Scenario"].map(flow_position) + (offset - (len(PROTOCOLS) - 1) / 2) * spread
                scatter = axis.scatter(x, group[metric], s=22, alpha=0.7, label=protocol)

                means = group.groupby("Scenario", observed=True)[metric].mean()
                mean_x = [flow_position[flow] + (offset - (len(PROTOCOLS) - 1) / 2) * spread for flow in means.index]
                axis.scatter(mean_x, means.values, marker="_", s=400, linewidths=2, color="black", zorder=3)

            axis.set_xticks(range(len(FLOWS)))
            axis.set_xticklabels(FLOWS)
            axis.set_title(metric)
            axis.grid(True, linestyle=":", alpha=0.6)

        axes[0].legend(title="Protocolo", fontsize=8)
        figure.suptitle(f"{topology}: valores por semente (marca preta = média)")
        figure.tight_layout()
        figure.savefig(os.path.join(PLOTS_DIR, f"scatter_{topology}.png"), dpi=200)
        plt.close(figure)
        print(f"[OK] Gráfico salvo em: '{os.path.join(PLOTS_DIR, f'scatter_{topology}.png')}'")

# ==============================================================================
# 5. EXECUÇÃO
# ==============================================================================
def main():
    if not os.path.isfile(SCAVETOOL_CSV):
        raise FileNotFoundError(f"Não encontrei {SCAVETOOL_CSV}. Execute analyze_stage4_results.py primeiro.")

    print("[1/4] Lendo o scavetool export...")
    sums, counts, configs, seeds = aggregate_export()

    print("[2/4] Calculando PDR, CBR e latência E2E por execução...")
    run_kpis = kpis_per_run(sums, counts, configs, seeds)
    if run_kpis.empty:
        raise ValueError("Nenhuma execução reconhecida: verifique os nomes de configuração no export.")
    without_pdr = run_kpis["PDR (%)"].isna().sum()
    if without_pdr:
        print(f"[AVISO] {without_pdr} execuções sem recepções PDR; elas ficam de fora da média de PDR.")

    print("[3/4] Agregando por topologia, fluxo e protocolo (média e IC 95%)...")
    summary = summarize(run_kpis)
    summary.to_csv(OUTPUT_CSV, index=False, float_format="%.4f")

    seed_rows = ordered(run_kpis).sort_values(["Topology", "Scenario", "Protocol", "Seed"]).reset_index(drop=True)
    seed_rows.to_csv(OUTPUT_SEED_CSV, index=False, float_format="%.4f")
    print(f"[OK] Valores por semente salvos em: '{OUTPUT_SEED_CSV}'")

    print("[4/4] Gerando gráficos de dispersão por semente...")
    plot_seed_scatter(run_kpis)

    for topology in TOPOLOGIES:
        block = summary[summary["Topology"] == topology].drop(columns="Topology")
        print(f"\n=== {topology} ===")
        print(block.to_string(index=False, float_format=lambda value: f"{value:.3f}"))

    print(f"\n[OK] Resumo salvo em: '{OUTPUT_CSV}'")


if __name__ == "__main__":
    main()
