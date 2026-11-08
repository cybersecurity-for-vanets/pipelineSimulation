import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.analysis.flow_levels import level_label
from src.analysis.level_style import is_extrapolated, los_of
from src.analysis.plot_kpis import (
    CPU_KPI, DEADLINE_KPI, LATENCY_KPI, PDR_KPI, PROCESSING_KPI, PROCESSING_P99_KPI,
    PAIRED_SUMMARY_CSV, SUMMARY_CSV, scenario_flow_per_lane,
)
from src.env import DEFAULT_EXTERNAL_DIR, ROOT_DIR
from src.simulation.protocols import load_protocols

def _resolve_project_dir():
    return DEFAULT_EXTERNAL_DIR if DEFAULT_EXTERNAL_DIR.is_dir() else ROOT_DIR

PROJECT_DIR = _resolve_project_dir()
VALUES_CSV = os.path.join(PROJECT_DIR, "results", "rawData", "kpi_heatmap_values.csv")
PLOTS_DIR = os.path.join(PROJECT_DIR, "results", "plots")
OUTPUT_PNG = os.path.join(PLOTS_DIR, "kpi_heatmaps.png")
OUTPUT_PDF = os.path.join(PLOTS_DIR, "kpi_heatmaps.pdf")

TOPOLOGY_ORDER = ["highway", "urbanRoad", "intersection"]
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


# Uma linha por (topologia, nível): com um nível por LOS do HCM (4–6 por topologia, ver map_los.py), cada linha é
# um regime de tráfego. O rótulo traz o LOS, o volume e se a demanda é a do pico CET ou extrapolada. Níveis sem LOS
# (resultados de rodadas antigas) usam o rótulo do nível.
def level_bands(frames):
    """{(topologia, nível): rótulo da linha} e a ordem das linhas, a partir dos níveis presentes nos dados."""
    pairs = pd.concat([frame[["Topology", "Scenario"]] for frame in frames]).drop_duplicates()
    pairs = pairs.assign(x=[scenario_flow_per_lane(t, level) for t, level in zip(pairs["Topology"], pairs["Scenario"])])
    pairs = pairs.dropna(subset=["x"])

    band_of, row_labels = {}, []
    for topology in TOPOLOGY_ORDER:
        levels = pairs[pairs["Topology"] == topology].sort_values("x")
        for level, per_lane in zip(levels["Scenario"], levels["x"]):
            los = los_of(topology, level)
            name = f"LOS {los}" if los else level_label(level)
            origin = "extrapolada" if is_extrapolated(level) else "pico CET"
            label = f"{topology} / {name}\n{per_lane:.0f} veh/h/faixa ({origin})"
            row_labels.append(label)
            band_of[(topology, level)] = label
    return band_of, row_labels


def build_panels(summary, paired):
    band_of, row_labels = level_bands([summary, paired])

    def banded(frame, column):
        rows = frame.assign(Row=[band_of.get(key) for key in zip(frame["Topology"], frame["Scenario"])])
        rows = rows.dropna(subset=["Row"])
        return rows.pivot_table(index="Row", columns="Protocol", values=column, aggfunc="mean")

    def run_level(kpi):
        return banded(summary[summary["KPI"] == kpi], "Mean")

    def paired_level(kpi):
        return banded(paired[paired["KPI"] == kpi], "MeanDiff")

    return row_labels, [
        ("Latência de processamento p99 (ms)", run_level(PROCESSING_P99_KPI), "magma", False),
        ("Utilização de CPU (%)", run_level(CPU_KPI), "YlOrRd", False),
        ("Perdas de prazo (% das operações de CAM)", run_level(DEADLINE_KPI), "Reds", False),
        ("Δ latência de processamento vs baseline (ms)", paired_level(PROCESSING_KPI), "RdBu_r", True),
        ("Δ latência fim a fim vs baseline (ms)", paired_level(LATENCY_KPI), "RdBu_r", True),
        ("Δ PDR vs baseline (p.p.)", paired_level(PDR_KPI), "RdBu_r", True),
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

    figure, axes = plt.subplots(2, 3, figsize=(22, max(11, 0.7 * len(row_labels) * 2 + 2)), constrained_layout=True)
    for ax, (title, values, cmap, symmetric) in zip(axes.flat, panels):
        draw_heatmap(ax, values, title, row_labels, protocols, cmap, symmetric)
    figure.suptitle("Protocolos FF1 × baseline: custo de processamento e diferenças pareadas "
                    "(média dos níveis de cada faixa de volume da topologia)", fontsize=14)
    figure.savefig(OUTPUT_PNG, dpi=220, bbox_inches="tight")
    figure.savefig(OUTPUT_PDF, bbox_inches="tight")
    plt.close(figure)

    print(f"[OK] Heatmap PNG: {OUTPUT_PNG}")
    print(f"[OK] Heatmap PDF: {OUTPUT_PDF}")
    print(f"[OK] Valores dos heatmaps (CSV): {VALUES_CSV}")


def main():
    summary = pd.read_csv(SUMMARY_CSV)
    paired = pd.read_csv(PAIRED_SUMMARY_CSV)
    generate_heatmaps(summary, paired)


if __name__ == "__main__":
    main()