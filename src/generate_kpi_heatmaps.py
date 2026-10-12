import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY_CSV = os.path.join(PROJECT_DIR, "results", "rawData", "final_metrics_summary.csv")
VALUES_CSV = os.path.join(PROJECT_DIR, "results", "rawData", "kpi_heatmap_values.csv")
PLOTS_DIR = os.path.join(PROJECT_DIR, "results", "plots")
OUTPUT_PNG = os.path.join(PLOTS_DIR, "kpi_heatmaps.png")
OUTPUT_PDF = os.path.join(PLOTS_DIR, "kpi_heatmaps.pdf")

TOPOLOGY_ORDER = ["highway", "urbanRoad", "intersection"]
FLOW_ORDER = ["low_flow", "medium_flow", "high_flow"]
PROTOCOL_ORDER = ["ASCON_128", "Chaskey", "Proposed_V2X", "Ed25519"]
BASE_PAYLOAD_BYTES = 100
PROTOCOL_OVERHEAD_BYTES = {
    "ASCON_128": 16,
    "Chaskey": 16,
    "Proposed_V2X": 12,
    "Ed25519": 64,
}


def load_summary():
    if not os.path.isfile(SUMMARY_CSV):
        raise FileNotFoundError(
            f"No se encontró {SUMMARY_CSV}. Ejecuta primero analyze_stage4_results.py."
        )

    summary = pd.read_csv(SUMMARY_CSV)
    required = {"Topology", "Protocol", "Scenario", "name", "Mean"}
    missing = required - set(summary.columns)
    if missing:
        raise ValueError(f"Faltan columnas en el resumen: {', '.join(sorted(missing))}")
    return summary


def build_kpi_values(summary):
    scalar_means = summary.pivot_table(
        index=["Topology", "Protocol", "Scenario"],
        columns="name",
        values="Mean",
        aggfunc="first",
    )

    required_scalars = {"SentPackets", "TotalLostPackets", "channelBusy:timeavg"}
    missing = required_scalars - set(scalar_means.columns)
    if missing:
        raise ValueError(f"Faltan escalares para los heatmaps: {', '.join(sorted(missing))}")

    sent = scalar_means["SentPackets"]
    packet_delivery = ((sent - scalar_means["TotalLostPackets"]) / sent.where(sent > 0)) * 100
    channel_busy = scalar_means["channelBusy:timeavg"] * 100

    latency_total_name = "packetLatencyTotalMs"
    latency_samples_name = "packetLatencySamples"
    if {latency_total_name, latency_samples_name} <= set(scalar_means.columns):
        latency_samples = scalar_means[latency_samples_name]
        latency_ms = scalar_means[latency_total_name] / latency_samples.where(latency_samples > 0)
        latency_calculation = f"{latency_total_name} / {latency_samples_name} (ms)"
    else:
        latency_names = [
        name for name in scalar_means.columns
        if "latency" in str(name).lower() or "delay" in str(name).lower()
        ]
        if latency_names:
            latency_name = latency_names[0]
            latency_ms = scalar_means[latency_name]
            if not str(latency_name).lower().endswith("ms"):
                latency_ms = latency_ms * 1000
            latency_calculation = f"{latency_name} (converted to ms when needed)"
        else:
            latency_ms = pd.Series(np.nan, index=scalar_means.index)
            latency_calculation = "Unavailable: no latency/delay scalar in summary"
            print("[AVISO] El CSV no contiene latencia. Ejecuta las simulaciones con el módulo instrumentado para registrar packetLatencyTotalMs/packetLatencySamples.")

    rows = []
    for topology in TOPOLOGY_ORDER:
        for flow in FLOW_ORDER:
            for protocol in PROTOCOL_ORDER:
                key = (topology, protocol, flow)
                if key not in scalar_means.index:
                    continue

                overhead_bytes = PROTOCOL_OVERHEAD_BYTES.get(protocol)
                overhead_percent = (
                    overhead_bytes / BASE_PAYLOAD_BYTES * 100
                    if overhead_bytes is not None
                    else np.nan
                )
                rows.extend([
                    {
                        "Topology": topology,
                        "Protocol": protocol,
                        "Scenario": flow,
                        "KPI": "Packet Delivered Rate (%)",
                        "Value": packet_delivery.loc[key],
                        "Calculation": "(SentPackets - TotalLostPackets) / SentPackets * 100; estimated from aggregated counters",
                    },
                    {
                        "Topology": topology,
                        "Protocol": protocol,
                        "Scenario": flow,
                        "KPI": "Channel Busy Rate (%)",
                        "Value": channel_busy.loc[key],
                        "Calculation": "channelBusy:timeavg * 100",
                    },
                    {
                        "Topology": topology,
                        "Protocol": protocol,
                        "Scenario": flow,
                        "KPI": "Latency (ms)",
                        "Value": latency_ms.loc[key],
                        "Calculation": latency_calculation,
                    },
                    {
                        "Topology": topology,
                        "Protocol": protocol,
                        "Scenario": flow,
                        "KPI": "Crypto Overhead (%)",
                        "Value": overhead_percent,
                        "Calculation": f"{overhead_bytes} extra bytes / {BASE_PAYLOAD_BYTES} baseline bytes * 100" if overhead_bytes is not None else "Unavailable: unknown protocol",
                    },
                ])

    return pd.DataFrame(rows)


def draw_heatmap(ax, values, title, row_labels, protocols, cmap, unavailable_note=None):
    matrix = values.reindex(index=row_labels, columns=protocols).to_numpy(dtype=float)
    masked = np.ma.masked_invalid(matrix)

    if masked.count() == 0:
        ax.set_facecolor("#eeeeee")
        ax.text(
            0.5,
            0.5,
            unavailable_note or "N/D: no data in summary CSV",
            ha="center",
            va="center",
            wrap=True,
            transform=ax.transAxes,
        )
        ax.set_title(title)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        return

    colormap = plt.get_cmap(cmap).copy()
    colormap.set_bad("#e5e7eb")
    image = ax.imshow(masked, aspect="auto", cmap=colormap)
    ax.set_title(title)
    ax.set_xticks(np.arange(len(protocols)), protocols, rotation=25, ha="right")
    ax.set_yticks(np.arange(len(row_labels)), row_labels)
    ax.set_xticks(np.arange(-0.5, len(protocols), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(row_labels), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.5)
    ax.tick_params(which="minor", bottom=False, left=False)

    midpoint = (image.norm.vmin + image.norm.vmax) / 2
    for row in range(len(row_labels)):
        for column in range(len(protocols)):
            value = matrix[row, column]
            if np.isnan(value):
                label = "N/D"
                color = "#333333"
            else:
                label = f"{value:.1f}"
                color = "white" if value < midpoint else "#171717"
            ax.text(column, row, label, ha="center", va="center", color=color, fontsize=8)

    ax.figure.colorbar(image, ax=ax, fraction=0.035, pad=0.025)


def generate_heatmaps(kpi_values):
    os.makedirs(PLOTS_DIR, exist_ok=True)
    kpi_values.to_csv(VALUES_CSV, index=False)

    row_labels = [f"{topology} / {flow}" for topology in TOPOLOGY_ORDER for flow in FLOW_ORDER]
    kpi_values = kpi_values.copy()
    kpi_values["Row"] = kpi_values["Topology"] + " / " + kpi_values["Scenario"]
    protocols = [protocol for protocol in PROTOCOL_ORDER if protocol in kpi_values["Protocol"].unique()]

    specifications = [
        ("Packet Delivered Rate (%)", "Packet Delivered Rate (%)", "YlGn", None),
        ("Channel Busy Rate (%)", "Channel Busy Rate (%)", "YlOrRd", None),
        ("Latency (ms)", "Latency (ms)", "magma", "No latency/delay scalar is recorded in the existing summary CSV."),
        ("Crypto Overhead (%)", "Crypto Overhead (%)", "Blues", None),
    ]

    figure, axes = plt.subplots(2, 2, figsize=(16, 11), constrained_layout=True)
    for ax, (kpi_name, title, cmap, unavailable_note) in zip(axes.flat, specifications):
        values = kpi_values[kpi_values["KPI"] == kpi_name].pivot_table(
            index="Row",
            columns="Protocol",
            values="Value",
            aggfunc="first",
        )
        draw_heatmap(ax, values, title, row_labels, protocols, cmap, unavailable_note)

    figure.suptitle("Simulation KPIs by topology, traffic flow, and protocol", fontsize=15)
    figure.savefig(OUTPUT_PNG, dpi=220, bbox_inches="tight")
    figure.savefig(OUTPUT_PDF, bbox_inches="tight")
    plt.close(figure)

    print(f"[OK] Heatmap PNG: {OUTPUT_PNG}")
    print(f"[OK] Heatmap PDF: {OUTPUT_PDF}")
    print(f"[OK] KPI values CSV: {VALUES_CSV}")


def main():
    summary = load_summary()
    kpi_values = build_kpi_values(summary)
    generate_heatmaps(kpi_values)


if __name__ == "__main__":
    main()