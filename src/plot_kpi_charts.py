import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

try:
    from .kpi_statistics import (
        equivalence_tests, load_margins, paired_differences, summarize_differences,
    )
    from .protocols import load_protocols
except ImportError:
    from kpi_statistics import (
        equivalence_tests, load_margins, paired_differences, summarize_differences,
    )
    from protocols import load_protocols


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SSD_DIR = "/Volumes/PSSD T7/simulationData"
# Same location rule as analyze_stage4_results.py (the export lives on the SSD when it is mounted).
RESULTS_DIR = os.path.join(SSD_DIR if os.path.isdir(SSD_DIR) else PROJECT_DIR, "results", "rawData")
SCAVETOOL_CSV = os.path.join(RESULTS_DIR, "scavetool_export.csv")
PLOTS_DIR = os.path.join(PROJECT_DIR, "results", "plots", "kpi_charts")
RUN_VALUES_CSV = os.path.join(RESULTS_DIR, "kpi_run_values.csv")
SUMMARY_CSV = os.path.join(RESULTS_DIR, "kpi_run_summary.csv")
PAIRED_CSV = os.path.join(RESULTS_DIR, "paired_differences.csv")
PAIRED_SUMMARY_CSV = os.path.join(RESULTS_DIR, "paired_summary.csv")
EQUIVALENCE_CSV = os.path.join(RESULTS_DIR, "equivalence_tests.csv")

TOPOLOGIES = ["highway", "urbanRoad", "intersection"]
FLOWS = ["low_flow", "medium_flow", "high_flow"]
PROTOCOLS = list(load_protocols())
BASELINE_PROTOCOL = "baseline"

# KPI names (also the row labels in the CSVs and the chart titles).
LATENCY_KPI = "Latency (ms)"
PROCESSING_KPI = "Processing latency (ms)"
PROCESSING_P95_KPI = "Processing latency p95 (ms)"
PROCESSING_P99_KPI = "Processing latency p99 (ms)"
NETWORK_KPI = "Network latency (ms)"
PDR_KPI = "PDR (%)"
CBR_KPI = "Channel Busy Rate (%)"
CPU_KPI = "CPU utilization (%)"
QUEUE_WAIT_KPI = "Mean queue wait (ms)"
MAX_QUEUE_KPI = "Max queue length"
DEADLINE_KPI = "Deadline misses (%)"
IPG_KPI = "Inter-packet gap (ms)"
PAOI_KPI = "Peak AoI (ms)"
CAM_RATE_KPI = "CAM rate (Hz)"
BYTES_KPI = "Bytes per CAM"
DECRYPT_KPI = "FF1 decrypt failures"
PAIRED_KPIS = [
    LATENCY_KPI, PROCESSING_KPI, NETWORK_KPI, PDR_KPI, CBR_KPI, CPU_KPI, QUEUE_WAIT_KPI, DEADLINE_KPI,
    IPG_KPI, PAOI_KPI, BYTES_KPI, CAM_RATE_KPI,
]

# Must match LatencyTraCIDemo11p.h: processing histogram bins (10 us, 0-2 ms, last bin = overflow).
PROCESSING_BIN_MS = 0.01
PROCESSING_BINS = 200
PDR_DISTANCE_BIN_M = 50
PDR_DISTANCE_BINS = 6

plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
plt.rcParams.update({"font.family": "serif", "font.size": 9, "axes.titlesize": 11})


def scalar_value(series, name, default=0.0):
    raw = series.get(name, default)
    return default if pd.isna(raw) else float(raw)


def percentile_from_bins(counts, quantile):
    """Upper edge (ms) of the bin where the cumulative count reaches the quantile; overflow reports the cap."""
    total = counts.sum()
    if total <= 0:
        return np.nan
    index = int(np.searchsorted(np.cumsum(counts), quantile * total))
    if index >= PROCESSING_BINS:
        return PROCESSING_BINS * PROCESSING_BIN_MS
    return (index + 1) * PROCESSING_BIN_MS


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
    """Stream the (possibly tens of GB) scavetool CSV, keeping only per-(run, scalar name) sum/count/max."""
    columns = ["run", "type", "module", "name", "attrname", "attrvalue", "value"]
    required = {"run", "type", "module", "name", "value", "attrname", "attrvalue"}
    header = set(pd.read_csv(SCAVETOOL_CSV, nrows=0).columns)
    if required - header:
        raise ValueError(f"Colunas ausentes no scavetool export: {', '.join(sorted(required - header))}")

    partial, lifetimes, configs = [], [], {}
    reader = pd.read_csv(SCAVETOOL_CSV, usecols=columns, dtype=str, chunksize=chunk_rows)
    for index, chunk in enumerate(reader, start=1):
        runattr = chunk[(chunk["type"] == "runattr") & (chunk["attrname"] == "configname")]
        configs.update(zip(runattr["run"], runattr["attrvalue"]))

        scalars = chunk.loc[chunk["type"] == "scalar", ["run", "module", "name", "value"]]
        scalars = scalars.assign(value=pd.to_numeric(scalars["value"], errors="coerce")).dropna(subset=["value"])
        if scalars.empty:
            continue
        partial.append(scalars.groupby(["run", "name"])["value"].agg(["sum", "count", "max"]))
        mobility = scalars[(scalars["name"] == "totalTime")
                           & scalars["module"].astype(str).str.contains("veinsmobility", regex=False)]
        if not mobility.empty:
            lifetimes.append(mobility.groupby("run")["value"].sum())
        if index % 20 == 0:
            print(f"[INFO] {index * chunk_rows:,} linhas lidas...")

    if not partial:
        raise ValueError("Nenhum escalar encontrado no scavetool export.")
    combined = pd.concat(partial).groupby(level=["run", "name"]).agg({"sum": "sum", "count": "sum", "max": "max"})
    vehicle_time = pd.concat(lifetimes).groupby(level=0).sum() if lifetimes else pd.Series(dtype=float)
    sums = combined["sum"]
    means = combined["sum"] / combined["count"]
    return sums, means, combined["max"], vehicle_time, pd.Series(configs)


def load_run_kpis():
    if not os.path.isfile(SCAVETOOL_CSV):
        raise FileNotFoundError(
            f"Não encontrei {SCAVETOOL_CSV}. Execute analyze_stage4_results.py primeiro."
        )

    sums_by_run, means_by_run, max_by_run, vehicle_time, config_names = aggregate_export()

    run_keys = {}
    for run, config in config_names.items():
        parsed = parse_config(config)
        if parsed is not None:
            run_keys[run] = (run, *parsed)

    def keyed(series):
        """Re-index a (run, name) series by (run, Topology, Protocol, Scenario) and spread names into columns."""
        series = series[series.index.get_level_values("run").isin(run_keys)]
        series.index = pd.MultiIndex.from_tuples(
            [(*run_keys[run], name) for run, name in series.index],
            names=["run", "Topology", "Protocol", "Scenario", "name"],
        )
        return series.unstack("name")

    sums = keyed(sums_by_run)
    means = keyed(means_by_run)
    max_queue = keyed(max_by_run).get("cpuMaxQueueLength", pd.Series(dtype=float))
    pdr_counts = sums_by_run[sums_by_run.index.get_level_values("name").isin(
        {"pdrExpectedReceptions", "pdrReceivedReceptions"})].groupby("run").size()
    if pdr_counts.reindex(sums_by_run.index.get_level_values("run").unique(), fill_value=0).lt(2).any():
        print("[AVISO] Há execuções sem escalares PDR. O resumo usará apenas execuções instrumentadas; reexecute as simulações ausentes.")
    rows = []

    for key, sum_values in sums.iterrows():
        run, topology, protocol, flow = key
        mean_values = means.loc[key]
        base = {"run": run, "Topology": topology, "Protocol": protocol, "Scenario": flow}

        samples = sum_values.get("packetLatencySamples", 0)
        latency_total = sum_values.get("packetLatencyTotalMs", np.nan)
        if pd.notna(latency_total) and samples > 0:
            rows.append({**base, "KPI": "Latency (ms)", "Value": latency_total / samples})

        expected_receptions = sum_values.get("pdrExpectedReceptions", np.nan)
        received_receptions = sum_values.get("pdrReceivedReceptions", np.nan)
        if pd.notna(expected_receptions) and pd.notna(received_receptions) and expected_receptions > 0:
            rows.append({
                **base,
                "KPI": "PDR (%)",
                "Value": received_receptions / expected_receptions * 100,
            })

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

        seconds = vehicle_time.get(run, np.nan)
        if pd.notna(seconds) and seconds > 0:
            busy = scalar_value(sum_values, "cpuBusyTimeS")
            rows.append({**base, "KPI": CPU_KPI, "Value": 100 * busy / seconds})
            if scalar_value(sum_values, "camSentCount") > 0:
                rows.append({**base, "KPI": CAM_RATE_KPI, "Value": scalar_value(sum_values, "camSentCount") / seconds})

        queue_samples = scalar_value(sum_values, "cpuQueueSamples")
        if queue_samples > 0:
            rows.append({**base, "KPI": QUEUE_WAIT_KPI,
                         "Value": 1000 * scalar_value(sum_values, "cpuQueueWaitTotalS") / queue_samples})
            rows.append({**base, "KPI": DEADLINE_KPI,
                         "Value": 100 * scalar_value(sum_values, "processingDeadlineMisses") / queue_samples})
            if pd.notna(max_queue.get(key, np.nan)):
                rows.append({**base, "KPI": MAX_QUEUE_KPI, "Value": max_queue.get(key)})

        if samples > 0:
            rows.append({**base, "KPI": PROCESSING_KPI,
                         "Value": scalar_value(sum_values, "processingLatencyTotalMs") / samples})
            rows.append({**base, "KPI": NETWORK_KPI,
                         "Value": scalar_value(sum_values, "networkLatencyTotalMs") / samples})
            counts = np.array([scalar_value(sum_values, f"procBin_{index}") for index in range(PROCESSING_BINS + 1)])
            for quantile, label in [(0.95, PROCESSING_P95_KPI), (0.99, PROCESSING_P99_KPI)]:
                percentile = percentile_from_bins(counts, quantile)
                if pd.notna(percentile):
                    rows.append({**base, "KPI": label, "Value": percentile})

        ipg_samples = scalar_value(sum_values, "ipgSamples")
        if ipg_samples > 0:
            rows.append({**base, "KPI": IPG_KPI, "Value": 1000 * scalar_value(sum_values, "ipgTotalS") / ipg_samples})
            rows.append({**base, "KPI": PAOI_KPI, "Value": 1000 * scalar_value(sum_values, "peakAoITotalS") / ipg_samples})

        cam_count = scalar_value(sum_values, "camSentCount")
        if cam_count > 0:
            rows.append({**base, "KPI": BYTES_KPI, "Value": scalar_value(sum_values, "camBytesTotal") / cam_count})

        if pd.notna(sum_values.get("ff1DecryptFailures", np.nan)):
            rows.append({**base, "KPI": DECRYPT_KPI, "Value": scalar_value(sum_values, "ff1DecryptFailures")})

        for index in range(PDR_DISTANCE_BINS):
            expected = scalar_value(sum_values, f"pdrExpected_d{index}")
            received = scalar_value(sum_values, f"pdrReceived_d{index}")
            if expected > 0:
                low, high = index * PDR_DISTANCE_BIN_M, (index + 1) * PDR_DISTANCE_BIN_M
                rows.append({**base, "KPI": f"PDR {low}-{high} m (%)", "Value": 100 * received / expected})

    run_values = pd.DataFrame(rows)
    if run_values.empty:
        raise ValueError("Nenhum KPI calculável foi encontrado nos escalares exportados.")

    summary = run_values.groupby(
        ["Topology", "Protocol", "Scenario", "KPI"], as_index=False
    )["Value"].agg(Mean="mean", Std="std", N="count")
    summary["CI95"] = 1.96 * summary["Std"].fillna(0) / np.sqrt(summary["N"])
    return run_values, summary


def delta_rows(paired_summary):
    """Paired mean difference vs baseline, shaped like the summary so the same charts can draw it."""
    columns = ["Topology", "Protocol", "Scenario", "KPI", "Mean", "Std", "N", "CI95"]
    if paired_summary.empty:
        return pd.DataFrame(columns=columns)
    rows = paired_summary.rename(columns={"MeanDiff": "Mean", "SD": "Std"})
    rows = rows.assign(KPI=rows["KPI"] + " vs baseline")
    return rows[columns]


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
    metrics = summary["KPI"].unique()
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
    figure, axes = plt.subplots(1, len(PROTOCOLS), figsize=(4.5 * len(PROTOCOLS), 5), sharex=True, sharey=True, squeeze=False)
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


def report_checks(run_values):
    failures = run_values.loc[run_values["KPI"] == DECRYPT_KPI, "Value"]
    if failures.empty:
        print("[INFO] FF1 decrypt failures: not applicable (no FF1 runs in this set).")
    elif failures.sum() > 0:
        print(f"[ERRO] FF1 decrypt failures: {failures.sum():.0f} (must be 0).")
    else:
        print("[OK] FF1 decrypt failures: 0 in every run.")


def report_equivalence(equivalence):
    if equivalence.empty:
        print("[AVISO] Nenhum teste de equivalência foi calculado (faltam repetições pareadas).")
        return
    for kpi, group in equivalence.groupby("KPI"):
        print(f"[EQUIV] {kpi}: {group['Equivalent'].sum()}/{len(group)} grupos equivalentes ao baseline "
              f"(margem ±{group['Margin'].iloc[0]:g})")


def main():
    run_values, summary = load_run_kpis()
    report_checks(run_values)

    present = set(run_values["KPI"])
    paired = pd.concat(
        [paired_differences(run_values, kpi) for kpi in PAIRED_KPIS if kpi in present],
        ignore_index=True,
    )
    paired_summary = summarize_differences(paired)
    equivalence = equivalence_tests(paired, load_margins())

    os.makedirs(RESULTS_DIR, exist_ok=True)
    run_values.to_csv(RUN_VALUES_CSV, index=False)
    summary.to_csv(SUMMARY_CSV, index=False)
    paired.to_csv(PAIRED_CSV, index=False)
    paired_summary.to_csv(PAIRED_SUMMARY_CSV, index=False)
    equivalence.to_csv(EQUIVALENCE_CSV, index=False)
    report_equivalence(equivalence)

    # Distance bins and per-run detail stay in the CSVs; charts show the overall KPIs and paired deltas.
    chart_summary = pd.concat([summary, delta_rows(paired_summary)], ignore_index=True)
    chart_summary = chart_summary[~chart_summary["KPI"].str.contains(" m (%)", regex=False)]
    plot_bars(chart_summary)
    plot_lines(chart_summary)
    plot_latency_boxplots(run_values)
    plot_cbr_latency_scatter(run_values)
    print(f"[OK] Métricas por repetição: {RUN_VALUES_CSV}")
    print(f"[OK] Resumo com IC 95%: {SUMMARY_CSV}")
    print(f"[OK] Diferenças pareadas vs baseline: {PAIRED_SUMMARY_CSV}")
    print(f"[OK] Testes de equivalência (TOST, Holm): {EQUIVALENCE_CSV}")
    print(f"[OK] Gráficos PNG/PDF: {PLOTS_DIR}")


if __name__ == "__main__":
    main()