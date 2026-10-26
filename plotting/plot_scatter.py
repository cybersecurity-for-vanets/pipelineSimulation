import argparse
import os
import sys

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D

SSD_DIR = os.path.dirname("/Volumes/PSSD T7/simulationData/")
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(SSD_DIR):
    RESULTS_DIR = os.path.join(SSD_DIR, "results")
else:
    RESULTS_DIR = os.path.join(PROJECT_DIR, "results")

sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))
from protocols import load_protocols  # noqa: E402

# ==============================================================================
# 1. DIRETÓRIOS E CONFIGURAÇÕES
# ==============================================================================
DATA_CSV = os.path.join(RESULTS_DIR, "rawData", "flow_kpis_per_seed.csv")
PLOTS_DIR = os.path.join(RESULTS_DIR, "plots", "scatter")

TOPOLOGIES = ["highway", "urbanRoad", "intersection"]
FLOWS = ["low_flow", "medium_flow", "high_flow"]
FLOW_LABELS = {"low_flow": "Baixo", "medium_flow": "Médio", "high_flow": "Alto"}
PROTOCOLS = list(load_protocols())
METRICS = ["PDR (%)", "CBR (%)", "Latency E2E (ms)"]
METRIC_AXIS_LABELS = {
    "PDR (%)": "Taxa de entrega de pacotes (%)",
    "CBR (%)": "Taxa de ocupação do canal (%)",
    "Latency E2E (ms)": "Latência fim a fim (ms)",
}
METRIC_TITLES = {"PDR (%)": "PDR", "CBR (%)": "CBR", "Latency E2E (ms)": "Latência E2E"}
METRIC_FILE_NAMES ={"PDR (%)": "pdr", "CBR (%)": "cbr", "Latency E2E (ms)": "latencia_e2e"}

COLORS = {"highway": "#1f77b4", "urbanRoad": "#ff7f0e", "intersection": "#2ca02c"}
MARKERS = ["o", "s", "^", "D", "v", "P"]

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
    os.makedirs(PLOTS_DIR, exist_ok=True)
    path = os.path.join(PLOTS_DIR, file_name)
    figure.savefig(path, dpi=200)
    plt.close(figure)
    print(f"[OK] Gráfico salvo em: '{path}'")

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
