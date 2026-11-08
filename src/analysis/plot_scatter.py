import argparse
import os

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import NullFormatter, ScalarFormatter

from src.analysis.plot_common import (COLORS, FLOW_X, MARKERS, METRIC_AXIS_LABELS, METRIC_FILE_NAMES,
                         METRIC_TITLES, PROTOCOLS, RAW_DATA_DIR, RESULTS_DIR, TOPOLOGIES, level_origin_handles,
                         scatter_levels)
from src.analysis.plot_common import save as save_figure

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
    """Pontos por semente sobre o volume do nível (veh/h/faixa): cor = topologia, marcador = protocolo.

    Marcador vazado = demanda extrapolada (fora do pico CET). Marca preta = média de cada grupo (topologia, protocolo, nível).
    """
    topologies = [t for t in TOPOLOGIES if t in set(data["Topology"])]
    for topology in topologies:
        for protocol_index, protocol in enumerate(PROTOCOLS):
            group = data[(data["Topology"] == topology) & (data["Protocol"] == protocol)].dropna(subset=[FLOW_X])
            if group.empty:
                continue
            scatter_levels(axis, group[FLOW_X], group[metric], group["Scenario"], s=22, alpha=0.7,
                           color=COLORS[topology], marker=MARKERS[protocol_index % len(MARKERS)])

            means = group.groupby(FLOW_X)[metric].mean()
            axis.scatter(means.index, means.values, marker="_", s=400, linewidths=2, color="black", zorder=3)

    # Escala log: highway (centenas a milhares de veh/h/faixa) e as vias urbanas (dezenas) no mesmo eixo.
    axis.set_xscale("log")
    axis.xaxis.set_major_formatter(ScalarFormatter())
    axis.xaxis.set_minor_formatter(NullFormatter())
    axis.set_title(METRIC_TITLES[metric])
    axis.set_xlabel("Volume por faixa (veh/h/faixa, escala log)")
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
    bottom = [mean_handle] + level_origin_handles(with_line=False)
    figure.legend(handles=bottom, fontsize=8, loc="lower center", bbox_to_anchor=(0.5, 0.0), ncol=len(bottom))
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

    if not os.path.isfile(DATA_CSV):
        raise FileNotFoundError(f"Não encontrei {DATA_CSV}. Execute src/flow_kpis_by_topology.py para gerá-lo.")
    data = pd.read_csv(DATA_CSV)
    if not {FLOW_X, "Scenario"} <= set(data.columns):
        raise ValueError(f"{DATA_CSV} não tem as colunas '{FLOW_X}' e 'Scenario'. "
                         "Execute src/flow_kpis_by_topology.py de novo para regerá-lo.")
    if args.modo == "unico":
        plot_single(data)
    else:
        plot_separate(data)


if __name__ == "__main__":
    main()
