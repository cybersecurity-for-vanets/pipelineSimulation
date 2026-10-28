import argparse
import os

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D

from plot_common import (COLORS, FLOW_LABELS, FLOWS, MARKERS, METRIC_AXIS_LABELS, METRIC_FILE_NAMES,
                         METRIC_TITLES, PROTOCOLS, RAW_DATA_DIR, RESULTS_DIR, TOPOLOGIES)
from plot_common import save as save_figure

# ==============================================================================
# 1. DIRETÓRIOS E CONFIGURAÇÕES
# ==============================================================================
DATA_CSV = os.path.join(RAW_DATA_DIR, "flow_kpis_per_seed.csv")
PLOTS_DIR = os.path.join(RESULTS_DIR, "plots", "scatter")

METRICS = ["PDR (%)", "CBR (%)", "Latency E2E (ms)"]

# ==============================================================================
# 2. DESENHO
# ==============================================================================
def draw_metric(axis, data, metric):
    """Pontos por semente: cor = topologia, marcador = protocolo. Marca preta = média de cada grupo."""
    flow_position = {flow: index for index, flow in enumerate(FLOWS)}
    topologies = [t for t in TOPOLOGIES if t in set(data["Topology"])]
    spread = 0.2
    for topology_index, topology in enumerate(topologies):
        topology_offset = (topology_index - (len(topologies) - 1) / 2) * spread
        for protocol_index, protocol in enumerate(PROTOCOLS):
            group = data[(data["Topology"] == topology) & (data["Protocol"] == protocol)]
            if group.empty:
                continue
            x = group["Scenario"].map(flow_position) + topology_offset
            axis.scatter(x, group[metric], s=22, alpha=0.7, color=COLORS[topology],
                         marker=MARKERS[protocol_index % len(MARKERS)])

            means = group.groupby("Scenario")[metric].mean()
            mean_x = [flow_position[flow] + topology_offset for flow in means.index]
            axis.scatter(mean_x, means.values, marker="_", s=400, linewidths=2, color="black", zorder=3)

    axis.set_xticks(range(len(FLOWS)))
    axis.set_xticklabels([FLOW_LABELS[flow] for flow in FLOWS])
    axis.set_title(METRIC_TITLES[metric])
    axis.set_xlabel("Nível de fluxo")
    axis.set_ylabel(METRIC_AXIS_LABELS[metric])
    axis.grid(True, linestyle=":", alpha=0.6)


def legend_handles(data):
    topology_handles = [Line2D([], [], marker="o", linestyle="", color=COLORS[t], label=t)
                        for t in TOPOLOGIES if t in set(data["Topology"])]
    protocol_handles = [Line2D([], [], marker=MARKERS[i % len(MARKERS)], linestyle="", color="gray", label=p)
                        for i, p in enumerate(PROTOCOLS) if p in set(data["Protocol"])]
    return topology_handles, protocol_handles


def add_legends(figure, data):
    """Legendas abaixo dos gráficos, para não cobrir pontos."""
    topology_handles, protocol_handles = legend_handles(data)
    mean_handle = Line2D([], [], marker="_", markersize=14, markeredgewidth=2, linestyle="", color="black",
                         label="Marca preta = média de cada grupo")
    figure.legend(handles=topology_handles, title="Topologia", fontsize=8, ncol=len(topology_handles),
                  loc="lower right", bbox_to_anchor=(0.5, 0.07))
    figure.legend(handles=protocol_handles, title="Protocolo", fontsize=8, ncol=len(protocol_handles),
                  loc="lower left", bbox_to_anchor=(0.5, 0.07))
    figure.legend(handles=[mean_handle], fontsize=8, loc="lower center", bbox_to_anchor=(0.5, 0.0))
    figure.tight_layout(rect=(0, 0.2, 1, 1))


def save(figure, file_name):
    save_figure(figure, PLOTS_DIR, file_name)

# ==============================================================================
# 3. EXECUÇÃO
# ==============================================================================
def plot_single(data):
    figure, axes = plt.subplots(1, len(METRICS), figsize=(5 * len(METRICS), 5))
    for axis, metric in zip(axes, METRICS):
        draw_metric(axis, data, metric)
    # figure.suptitle("Valores por semente")
    add_legends(figure, data)
    save(figure, "scatter_metricas.png")


def plot_separate(data):
    for metric in METRICS:
        figure, axis = plt.subplots(figsize=(7, 5))
        draw_metric(axis, data, metric)
        # figure.suptitle("Valores por semente")
        add_legends(figure, data)
        save(figure, f"scatter_{METRIC_FILE_NAMES[metric]}.png")


def main():
    parser = argparse.ArgumentParser(description="Gráficos de dispersão por semente (PDR, CBR e latência E2E).")
    parser.add_argument("--modo", choices=["unico", "separado"], default="unico",
                        help="'unico': os 3 gráficos numa imagem só; 'separado': uma imagem por métrica.")
    args = parser.parse_args()

    data = pd.read_csv(DATA_CSV)
    if args.modo == "unico":
        plot_single(data)
    else:
        plot_separate(data)


if __name__ == "__main__":
    main()
