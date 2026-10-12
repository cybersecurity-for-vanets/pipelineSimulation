import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAVETOOL_CSV = os.path.join(PROJECT_DIR, "results", "rawData", "scavetool_export.csv")
RESULTS_DIR = os.path.join(PROJECT_DIR, "results", "rawData")
PLOTS_DIR = os.path.join(PROJECT_DIR, "results", "plots", "kpi_charts")
RUN_VALUES_CSV = os.path.join(RESULTS_DIR, "kpi_run_values.csv")
SUMMARY_CSV = os.path.join(RESULTS_DIR, "kpi_run_summary.csv")

TOPOLOGIES = ["highway", "urbanRoad", "intersection"]
FLOWS = ["low_flow", "medium_flow", "high_flow"]
PROTOCOLS = ["ASCON_128", "Chaskey", "Ed25519"]
BASE_CAM_BYTES = 64
OVERHEAD_BYTES = {"ASCON_128": 16, "Chaskey": 16, "Ed25519": 64}

plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
plt.rcParams.update({"font.family": "serif", "font.size": 9, "axes.titlesize": 11})


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


def load_run_kpis():
    if not os.path.isfile(SCAVETOOL_CSV):
        raise FileNotFoundError(
            f"Não encontrei {SCAVETOOL_CSV}. Execute analyze_stage4_results.py primeiro."
        )

    exported = pd.read_csv(SCAVETOOL_CSV, low_memory=False)
    required = {"run", "type", "module", "name", "value", "attrname", "attrvalue"}
    missing = required - set(exported.columns)
    if missing:
        raise ValueError(f"Colunas ausentes no scavetool export: {', '.join(sorted(missing))}")

    config_names = exported.loc[
        (exported["type"] == "runattr") & (exported["attrname"] == "configname"),
        ["run", "attrvalue"],
    ].drop_duplicates("run", keep="last").set_index("run")["attrvalue"]

    scalars = exported[exported["type"] == "scalar"].copy()
    scalars["value"] = pd.to_numeric(scalars["value"], errors="coerce")
    scalars = scalars.dropna(subset=["value"])
    scalars["Config"] = scalars["run"].map(config_names)
    parsed = scalars["Config"].apply(parse_config)
    scalars = scalars[parsed.notna()].copy()
    scalars[["Topology", "Protocol", "Scenario"]] = pd.DataFrame(
        parsed[parsed.notna()].tolist(), index=scalars.index
    )

    keys = ["run", "Topology", "Protocol", "Scenario"]
    grouped = scalars.groupby(keys + ["name"])["value"]
    sums = grouped.sum().unstack("name")
    means = grouped.mean().unstack("name")
    rows = []

    for key, sum_values in sums.iterrows():
        run, topology, protocol, flow = key
        mean_values = means.loc[key]
        base = {"run": run, "Topology": topology, "Protocol": protocol, "Scenario": flow}

        samples = sum_values.get("packetLatencySamples", 0)
        latency_total = sum_values.get("packetLatencyTotalMs", np.nan)
        if pd.notna(latency_total) and samples > 0:
            rows.append({**base, "KPI": "Latency (ms)", "Value": latency_total / samples})

        channel_busy = mean_values.get("channelBusy:timeavg", np.nan)
        if pd.notna(channel_busy):
            rows.append({**base, "KPI": "Channel Busy Rate (%)", "Value": channel_busy * 100})

        for scalar, label in [
            ("receivedBSMs", "Received BSMs per vehicle"),
            ("generatedBSMs", "Generated BSMs per vehicle"),
        ]:
            value = mean_values.get(scalar, np.nan)
            if pd.notna(value):
                rows.append({**base, "KPI": label, "Value": value})

        for total_name, count_name, label in [
            ("cryptoTxDelayTotalMs", "cryptoTxOperationCount", "Crypto TX processing (ms)"),
            ("cryptoRxDelayTotalMs", "cryptoRxOperationCount", "Crypto RX processing (ms)"),
        ]:
            total_delay = sum_values.get(total_name, np.nan)
            operation_count = sum_values.get(count_name, 0)
            if pd.notna(total_delay) and operation_count > 0:
                rows.append({**base, "KPI": label, "Value": total_delay / operation_count})

        overhead = OVERHEAD_BYTES.get(protocol)
        if overhead is not None:
            rows.append({
                **base,
            "KPI": "CAM Crypto Overhead (%)",
            "Value": overhead / BASE_CAM_BYTES * 100,
            })

    run_values = pd.DataFrame(rows)
    if run_values.empty:
        raise ValueError("Nenhum KPI calculável foi encontrado nos escalares exportados.")

    summary = run_values.groupby(
        ["Topology", "Protocol", "Scenario", "KPI"], as_index=False
    )["Value"].agg(Mean="mean", Std="std", N="count")
    summary["CI95"] = 1.96 * summary["Std"].fillna(0) / np.sqrt(summary["N"])
    return run_values, summary


def save_figure(figure, filename):
    os.makedirs(PLOTS_DIR, exist_ok=True)
    figure.savefig(os.path.join(PLOTS_DIR, f"{filename}.png"), dpi=220, bbox_inches="tight")
    # figure.savefig(os.path.join(PLOTS_DIR, f"{filename}.pdf"), bbox_inches="tight")
    plt.close(figure)


def plot_bars(summary):
    metrics = summary["KPI"].unique()
    for metric in metrics:
        figure, axes = plt.subplots(1, len(TOPOLOGIES), figsize=(15, 4.5), squeeze=False)
        legend_handles = []
        for ax, topology in zip(axes[0], TOPOLOGIES):
            data = summary[(summary["Topology"] == topology) & (summary["KPI"] == metric)]
            protocols = [value for value in PROTOCOLS if value in data["Protocol"].unique()]
            positions = np.arange(len(FLOWS))
            width = 0.8 / max(len(protocols), 1)
            for protocol_index, protocol in enumerate(protocols):
                values = data[data["Protocol"] == protocol].set_index("Scenario").reindex(FLOWS)
                offset = (protocol_index - (len(protocols) - 1) / 2) * width
                bars = ax.bar(
                    positions + offset,
                    values["Mean"],
                    width,
                    yerr=values["CI95"],
                    capsize=2,
                    label=protocol,
                )
                if topology == TOPOLOGIES[0]:
                    legend_handles.append(bars)
            ax.set_title(topology)
            ax.set_xticks(positions, FLOWS, rotation=15)
            ax.set_ylabel(metric)
            ax.grid(axis="y", alpha=0.3)
        figure.suptitle(f"{metric}: média por repetição com IC 95%")
        figure.legend(legend_handles, [handle.get_label() for handle in legend_handles], title="Protocolo", loc="upper center", ncol=max(len(legend_handles), 1), bbox_to_anchor=(0.5, 0.94), fontsize=8)
        figure.tight_layout(rect=(0, 0, 1, 0.88))
        save_figure(figure, f"bar_{metric.lower().replace(' ', '_').replace('(', '').replace(')', '').replace('%', 'pct')}")


def plot_lines(summary):
    metrics = [value for value in summary["KPI"].unique() if value != "CAM Crypto Overhead (%)"]
    for metric in metrics:
        figure, axes = plt.subplots(1, len(TOPOLOGIES), figsize=(15, 4.5), squeeze=False)
        legend_handles = []
        for ax, topology in zip(axes[0], TOPOLOGIES):
            data = summary[(summary["Topology"] == topology) & (summary["KPI"] == metric)]
            for protocol in PROTOCOLS:
                values = data[data["Protocol"] == protocol].set_index("Scenario").reindex(FLOWS)
                if values["Mean"].notna().any():
                    line = ax.errorbar(FLOWS, values["Mean"], yerr=values["CI95"], marker="o", capsize=2, label=protocol)
                    if topology == TOPOLOGIES[0]:
                        legend_handles.append(line[0])
            ax.set_title(topology)
            ax.set_ylabel(metric)
            ax.grid(alpha=0.3)
        figure.suptitle(f"Tendência por fluxo: {metric}")
        figure.legend(legend_handles, [handle.get_label() for handle in legend_handles], title="Protocolo", loc="upper center", ncol=max(len(legend_handles), 1), bbox_to_anchor=(0.5, 0.94), fontsize=8)
        figure.tight_layout(rect=(0, 0, 1, 0.88))
        save_figure(figure, f"line_{metric.lower().replace(' ', '_').replace('(', '').replace(')', '').replace('%', 'pct')}")


def plot_latency_boxplots(run_values):
    data = run_values[run_values["KPI"] == "Latency (ms)"]
    figure, axes = plt.subplots(1, len(TOPOLOGIES), figsize=(14, 4.5), squeeze=False)
    for ax, topology in zip(axes[0], TOPOLOGIES):
        topology_data = data[data["Topology"] == topology]
        groups = [topology_data.loc[topology_data["Protocol"] == protocol, "Value"].dropna() for protocol in PROTOCOLS]
        present = [(protocol, values) for protocol, values in zip(PROTOCOLS, groups) if not values.empty]
        if present:
            ax.boxplot([values for _, values in present], tick_labels=[protocol for protocol, _ in present], showfliers=False)
        ax.set_title(topology)
        ax.set_ylabel("Latency (ms)")
        ax.set_xlabel("Protocolo; fluxos agrupados")
        ax.grid(axis="y", alpha=0.3)
    figure.suptitle("Distribuição de latência entre repetições")
    save_figure(figure, "box_latency")


def plot_cbr_latency_scatter(run_values):
    values = run_values.pivot_table(
        index=["run", "Topology", "Protocol", "Scenario"], columns="KPI", values="Value", aggfunc="first"
    ).reset_index()
    values = values.dropna(subset=["Channel Busy Rate (%)", "Latency (ms)"])
    figure, axes = plt.subplots(1, len(PROTOCOLS), figsize=(15, 5), sharex=True, sharey=True, squeeze=False)
    markers = {topology: marker for topology, marker in zip(TOPOLOGIES, ["o", "s", "^"])}
    for ax, protocol in zip(axes[0], PROTOCOLS):
        protocol_values = values[values["Protocol"] == protocol]
        for topology in TOPOLOGIES:
            data = protocol_values[protocol_values["Topology"] == topology]
            if data.empty:
                continue
            ax.scatter(
                data["Channel Busy Rate (%)"],
                data["Latency (ms)"],
                marker=markers[topology],
                alpha=0.6,
                s=26,
            )
        ax.set_title(protocol)
        ax.set_xlabel("Channel Busy Rate (%)")
        ax.grid(alpha=0.3)
    axes[0][0].set_ylabel("Latency (ms)")
    topology_handles = [
        Line2D([], [], marker=markers[topology], linestyle="", color="#555555", label=topology)
        for topology in TOPOLOGIES
    ]
    figure.suptitle("Latência versus ocupação do canal por repetição; um painel por protocolo")
    figure.legend(handles=topology_handles, title="Topologia", loc="upper center", ncol=len(TOPOLOGIES), bbox_to_anchor=(0.5, 0.9), fontsize=8)
    figure.tight_layout(rect=(0, 0, 1, 0.82))
    save_figure(figure, "scatter_cbr_vs_latency")


def main():
    run_values, summary = load_run_kpis()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    run_values.to_csv(RUN_VALUES_CSV, index=False)
    summary.to_csv(SUMMARY_CSV, index=False)
    plot_bars(summary)
    plot_lines(summary)
    plot_latency_boxplots(run_values)
    plot_cbr_latency_scatter(run_values)
    print(f"[OK] Métricas por repetição: {RUN_VALUES_CSV}")
    print(f"[OK] Resumo com IC 95%: {SUMMARY_CSV}")
    print(f"[OK] Gráficos PNG/PDF: {PLOTS_DIR}")
    print("[INFO] PDR não foi calculado: os escalares disponíveis não identificam pares válidos de pacotes enviados e recebidos.")


if __name__ == "__main__":
    main()