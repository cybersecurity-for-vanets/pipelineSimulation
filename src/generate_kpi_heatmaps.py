import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from .plot_kpi_charts import (
        CPU_KPI, DEADLINE_KPI, LATENCY_KPI, PDR_KPI, PROCESSING_KPI, PROCESSING_P99_KPI,
        PAIRED_SUMMARY_CSV, SUMMARY_CSV,
    )
    from .protocols import load_protocols
except ImportError:
    from plot_kpi_charts import (
        CPU_KPI, DEADLINE_KPI, LATENCY_KPI, PDR_KPI, PROCESSING_KPI, PROCESSING_P99_KPI,
        PAIRED_SUMMARY_CSV, SUMMARY_CSV,
    )
    from protocols import load_protocols


SSD_DIR = os.path.dirname("/Volumes/PSSD T7/simulationData/")
if os.path.isdir(SSD_DIR):
    PROJECT_DIR = SSD_DIR
else:
    PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALUES_CSV = os.path.join(PROJECT_DIR, "results", "rawData", "kpi_heatmap_values.csv")
PLOTS_DIR = os.path.join(PROJECT_DIR, "results", "plots")
OUTPUT_PNG = os.path.join(PLOTS_DIR, "kpi_heatmaps.png")
OUTPUT_PDF = os.path.join(PLOTS_DIR, "kpi_heatmaps.pdf")

TOPOLOGY_ORDER = ["highway", "urbanRoad", "intersection"]
FLOW_ORDER = ["low_flow", "medium_flow", "high_flow"]
PROTOCOL_ORDER = list(load_protocols())


def draw_heatmap(ax, values, title, row_labels, protocols, cmap, symmetric=False):
    matrix = values.reindex(index=row_labels, columns=protocols).to_numpy(dtype=float)
    finite = matrix[np.isfinite(matrix)]
    limit = np.max(np.abs(finite)) if finite.size and symmetric else None
    image = ax.imshow(
        np.ma.masked_invalid(matrix), aspect="auto", cmap=cmap,
        vmin=-limit if limit else None, vmax=limit,
    )
    ax.set_xticks(range(len(protocols)), protocols, rotation=20, ha="right")
    ax.set_yticks(range(len(row_labels)), row_labels)
    ax.set_title(title, fontsize=10)

    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            cell = matrix[row, column]
            if not np.isfinite(cell):
                ax.text(column, row, "N/D", ha="center", va="center", fontsize=7, color="#777777")
                continue
            r, g, b, _ = image.cmap(image.norm(cell))
            text_color = "black" if 0.299 * r + 0.587 * g + 0.114 * b > 0.5 else "white"
            ax.text(column, row, f"{cell:.4g}", ha="center", va="center", fontsize=8, color=text_color)

    if finite.size:
        ax.figure.colorbar(image, ax=ax, fraction=0.035, pad=0.025)


def build_panels(summary, paired):
    row_labels = [f"{topology} / {flow}" for topology in TOPOLOGY_ORDER for flow in FLOW_ORDER]

    def run_level(kpi):
        rows = summary[summary["KPI"] == kpi].assign(Row=lambda d: d["Topology"] + " / " + d["Scenario"])
        return rows.pivot_table(index="Row", columns="Protocol", values="Mean", aggfunc="first")

    def paired_level(kpi):
        rows = paired[paired["KPI"] == kpi].assign(Row=lambda d: d["Topology"] + " / " + d["Scenario"])
        return rows.pivot_table(index="Row", columns="Protocol", values="MeanDiff", aggfunc="first")

    return row_labels, [
        ("p99 processing latency (ms)", run_level(PROCESSING_P99_KPI), "magma", False),
        ("CPU utilization (%)", run_level(CPU_KPI), "YlOrRd", False),
        ("Deadline misses (% of CAM operations)", run_level(DEADLINE_KPI), "Reds", False),
        ("Δ processing latency vs baseline (ms)", paired_level(PROCESSING_KPI), "RdBu_r", True),
        ("Δ end-to-end latency vs baseline (ms)", paired_level(LATENCY_KPI), "RdBu_r", True),
        ("Δ PDR vs baseline (pp)", paired_level(PDR_KPI), "RdBu_r", True),
    ]


def generate_heatmaps(summary, paired):
    os.makedirs(PLOTS_DIR, exist_ok=True)
    row_labels, panels = build_panels(summary, paired)
    protocols = PROTOCOL_ORDER

    exported = [
        values.assign(Panel=title).reset_index()
        for title, values, _, _ in panels if not values.empty
    ]
    if exported:
        pd.concat(exported, ignore_index=True).to_csv(VALUES_CSV, index=False)

    figure, axes = plt.subplots(2, 3, figsize=(20, 11), constrained_layout=True)
    for ax, (title, values, cmap, symmetric) in zip(axes.flat, panels):
        draw_heatmap(ax, values, title, row_labels, protocols, cmap, symmetric)
    figure.suptitle("FF1 protocols versus baseline: processing cost, channel load and paired deltas", fontsize=14)
    figure.savefig(OUTPUT_PNG, dpi=220, bbox_inches="tight")
    figure.savefig(OUTPUT_PDF, bbox_inches="tight")
    plt.close(figure)

    print(f"[OK] Heatmap PNG: {OUTPUT_PNG}")
    print(f"[OK] Heatmap PDF: {OUTPUT_PDF}")
    print(f"[OK] KPI values CSV: {VALUES_CSV}")


def main():
    summary = pd.read_csv(SUMMARY_CSV)
    paired = pd.read_csv(PAIRED_SUMMARY_CSV)
    generate_heatmaps(summary, paired)


if __name__ == "__main__":
    main()