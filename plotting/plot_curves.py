import argparse
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

from plot_common import (COLORS, FLOW_LABELS, FLOWS, METRIC_AXIS_LABELS, METRIC_FILE_NAMES, METRIC_TITLES,
                         PROTOCOLS, RAW_DATA_DIR, RESULTS_DIR, TOPOLOGIES, protocol_marker, save)

# ==============================================================================
# 1. DIRETÓRIOS E CONFIGURAÇÕES
# ==============================================================================
SEED_CSV = os.path.join(RAW_DATA_DIR, "flow_kpis_per_seed.csv")
DISTANCE_CSV = os.path.join(RAW_DATA_DIR, "flow_kpis_by_distance.csv")
PLOTS_DIR = os.path.join(RESULTS_DIR, "plots", "flow_kpis")

DENSITY_X = "Active vehicles"
CBR_X = "CBR (%)"
DENSITY_METRICS = ["PDR (%)", "CBR (%)", "Latency E2E (ms)"]
CBR_METRICS = ["PDR (%)", "Latency E2E (ms)"]

# Protocolo: estilo da linha de tendência (o marcador já identifica os pontos).
PROTOCOL_LINESTYLES = ["-", "--", ":", "-.", (0, (5, 1)), (0, (3, 1, 1, 1))]
# Fluxo: estilo da linha nas curvas por distância.
FLOW_LINESTYLES = {"low_flow": ":", "medium_flow": "--", "high_flow": "-"}
CBR_BINS = 12           # faixas de CBR da curva média (todas as topologias juntas)
MIN_POINTS_PER_BIN = 3  # faixas com menos execuções ficam de fora da curva
DISTANCE_BIN_M = 50     # largura das faixas de distância (LatencyTraCIDemo11p.h)


def protocol_linestyle(protocol):
    return PROTOCOL_LINESTYLES[PROTOCOLS.index(protocol) % len(PROTOCOL_LINESTYLES)]


def present(values, order):
    return [value for value in order if value in set(values)]

# ==============================================================================
# 2. DESENHO
# ==============================================================================
def quadratic_trend(x, y):
    """Ajuste de 2º grau sobre os pontos; None se houver menos de 3 valores distintos de x."""
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(np.unique(x)) < 3:
        return None
    grid = np.linspace(x.min(), x.max(), 50)
    return grid, np.polyval(np.polyfit(x, y, 2), grid)


def binned_mean(x, y, bins):
    """Média de x e de y em cada faixa de x, só nas faixas com MIN_POINTS_PER_BIN pontos ou mais."""
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    index = np.digitize(x, bins[1:-1])
    centers, means = [], []
    for bin_index in range(len(bins) - 1):
        in_bin = index == bin_index
        if in_bin.sum() >= MIN_POINTS_PER_BIN:
            centers.append(x[in_bin].mean())
            means.append(y[in_bin].mean())
    return centers, means


def draw_points(axis, data, x_column, metric):
    """Um ponto por execução: cor = topologia, marcador = protocolo."""
    for topology in present(data["Topology"], TOPOLOGIES):
        for protocol in present(data["Protocol"], PROTOCOLS):
            group = data[(data["Topology"] == topology) & (data["Protocol"] == protocol)]
            if group.empty:
                continue
            axis.scatter(group[x_column], group[metric], s=18, alpha=0.55, color=COLORS[topology],
                         marker=protocol_marker(protocol), linewidths=0)


def draw_density(axis, data, metric):
    """D: métrica × veículos ativos por execução, com ajuste quadrático por topologia e protocolo."""
    draw_points(axis, data, DENSITY_X, metric)
    for topology in present(data["Topology"], TOPOLOGIES):
        for protocol in present(data["Protocol"], PROTOCOLS):
            group = data[(data["Topology"] == topology) & (data["Protocol"] == protocol)]
            trend = quadratic_trend(group[DENSITY_X].to_numpy(float), group[metric].to_numpy(float))
            if trend is not None:
                axis.plot(*trend, color=COLORS[topology], linestyle=protocol_linestyle(protocol), linewidth=1.8)

    axis.set_title(METRIC_TITLES[metric])
    axis.set_xlabel(METRIC_AXIS_LABELS[DENSITY_X])
    axis.set_ylabel(METRIC_AXIS_LABELS[metric])
    axis.grid(True, linestyle=":", alpha=0.6)


def draw_cbr(axis, data, metric):
    """E: métrica × CBR da execução, com a média por faixa de CBR (todas as topologias e fluxos juntos)."""
    draw_points(axis, data, CBR_X, metric)
    cbr = data[CBR_X].to_numpy(float)
    finite = cbr[np.isfinite(cbr)]
    if finite.size:
        bins = np.linspace(finite.min(), finite.max(), CBR_BINS + 1)
        for protocol in present(data["Protocol"], PROTOCOLS):
            group = data[data["Protocol"] == protocol]
            centers, means = binned_mean(group[CBR_X].to_numpy(float), group[metric].to_numpy(float), bins)
            axis.plot(centers, means, color="black", linestyle=protocol_linestyle(protocol), linewidth=1.8,
                      marker=protocol_marker(protocol), markersize=4, zorder=3)

    axis.set_title(METRIC_TITLES[metric])
    axis.set_xlabel(METRIC_AXIS_LABELS[CBR_X])
    axis.set_ylabel(METRIC_AXIS_LABELS[metric])
    axis.grid(True, linestyle=":", alpha=0.6)


def draw_distance(axis, data, topology):
    """F: PDR × distância de uma topologia; estilo de linha = fluxo, marcador = protocolo, barras = IC 95%."""
    data = data[data["Topology"] == topology]
    protocols = present(data["Protocol"], PROTOCOLS)
    bins = sorted(data["Distance bin"].unique())
    spread = DISTANCE_BIN_M * 0.06
    for flow in present(data["Scenario"], FLOWS):
        for offset, protocol in enumerate(protocols):
            group = data[(data["Scenario"] == flow) & (data["Protocol"] == protocol)].sort_values("Distance bin")
            if group.empty:
                continue
            x = group["Distance min (m)"] + DISTANCE_BIN_M / 2 + (offset - (len(protocols) - 1) / 2) * spread
            axis.errorbar(x, group["PDR (%)"], yerr=group["PDR (%) CI95"], color=COLORS[topology],
                          linestyle=FLOW_LINESTYLES[flow], marker=protocol_marker(protocol), markersize=5,
                          linewidth=1.6, capsize=2)

    centers = [index * DISTANCE_BIN_M + DISTANCE_BIN_M / 2 for index in bins]
    labels = [f"{index * DISTANCE_BIN_M}–{(index + 1) * DISTANCE_BIN_M}" for index in bins]
    if labels:
        labels[-1] = f"≥ {bins[-1] * DISTANCE_BIN_M}"
    axis.set_xticks(centers)
    axis.set_xticklabels(labels)
    axis.set_title(topology)
    axis.set_xlabel("Distância emissor–receptor (m)")
    axis.set_ylabel(METRIC_AXIS_LABELS["PDR (%)"])
    axis.grid(True, linestyle=":", alpha=0.6)


def add_legends(figure, upper_left, upper_right, statistic):
    """Duas legendas lado a lado e, abaixo delas, a que explica a marca estatística. Todas abaixo dos painéis."""
    title_left, handles_left = upper_left
    title_right, handles_right = upper_right
    figure.legend(handles=handles_left, title=title_left, fontsize=8, ncol=len(handles_left),
                  loc="lower right", bbox_to_anchor=(0.5, 0.07))
    figure.legend(handles=handles_right, title=title_right, fontsize=8, ncol=len(handles_right),
                  loc="lower left", bbox_to_anchor=(0.5, 0.07))
    figure.legend(handles=statistic, fontsize=8, loc="lower center", bbox_to_anchor=(0.5, 0.0))
    figure.tight_layout(rect=(0, 0.2, 1, 1))


def topology_handles(data):
    return [Line2D([], [], marker="o", linestyle="", color=COLORS[t], label=t)
            for t in present(data["Topology"], TOPOLOGIES)]


def protocol_handles(data, with_line):
    return [Line2D([], [], marker=protocol_marker(p), color="gray", label=p,
                   linestyle=protocol_linestyle(p) if with_line else "")
            for p in present(data["Protocol"], PROTOCOLS)]

# ==============================================================================
# 3. EXECUÇÃO
# ==============================================================================
def figure_for(panels, modo):
    if modo == "unico":
        figure, axes = plt.subplots(1, panels, figsize=(5 * panels, 5), squeeze=False)
        return figure, list(axes[0])
    figure, axis = plt.subplots(figsize=(7, 5))
    return figure, [axis]


def plot_metric_curves(data, metrics, draw, legend_statistic, protocol_with_line, prefix, modo):
    """Monta D ou E: no modo único, um painel por métrica; no separado, uma imagem por métrica."""
    legends = (("Topologia", topology_handles(data)), ("Protocolo", protocol_handles(data, protocol_with_line)))
    groups = [metrics] if modo == "unico" else [[metric] for metric in metrics]
    for group in groups:
        figure, axes = figure_for(len(group), modo)
        for axis, metric in zip(axes, group):
            draw(axis, data, metric)
        add_legends(figure, *legends, legend_statistic)
        name = "metricas" if modo == "unico" else METRIC_FILE_NAMES[group[0]]
        save(figure, PLOTS_DIR, f"{prefix}_{name}.png")


def plot_density(data, modo):
    statistic = [Line2D([], [], color="gray", linewidth=1.8,
                        label="Linha = ajuste quadrático por topologia (cor) e protocolo (estilo)")]
    plot_metric_curves(data, DENSITY_METRICS, draw_density, statistic, True, "curva_densidade", modo)


def plot_cbr(data, modo):
    statistic = [Line2D([], [], color="black", linewidth=1.8,
                        label="Linha preta = média por faixa de CBR, todas as topologias (estilo = protocolo)")]
    plot_metric_curves(data, CBR_METRICS, draw_cbr, statistic, True, "curva_cbr", modo)


def plot_distance(data, modo):
    topologies = present(data["Topology"], TOPOLOGIES)
    flow_handles = [Line2D([], [], color="gray", linestyle=FLOW_LINESTYLES[f], label=FLOW_LABELS[f])
                    for f in present(data["Scenario"], FLOWS)]
    legends = (("Fluxo", flow_handles), ("Protocolo", protocol_handles(data, False)))
    statistic = [Line2D([], [], color="gray", marker="|", markersize=10, linestyle="",
                        label="Barra vertical = IC 95% entre sementes; cor = topologia")]
    groups = [topologies] if modo == "unico" else [[topology] for topology in topologies]
    for group in groups:
        figure, axes = figure_for(len(group), modo)
        cells = data[data["Topology"].isin(group)]
        shared = (cells["PDR (%)"] + cells["PDR (%) CI95"]).max()
        for axis, topology in zip(axes, group):
            draw_distance(axis, data, topology)
            axis.set_ylim(0, max(100, shared) + 2)
        add_legends(figure, *legends, statistic)
        name = "curva_distancia_pdr" if modo == "unico" else f"curva_distancia_pdr_{group[0]}"
        save(figure, PLOTS_DIR, f"{name}.png")


def read_csv(path, required):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Não encontrei {path}. Execute src/flow_kpis_by_topology.py para gerá-lo.")
    data = pd.read_csv(path)
    missing = set(required) - set(data.columns)
    if missing:
        raise ValueError(f"{path} não tem as colunas {sorted(missing)}. "
                         "Execute src/flow_kpis_by_topology.py de novo para regerá-lo.")
    return data


def main():
    parser = argparse.ArgumentParser(
        description="Curvas de KPI com mais pontos que os 3 níveis de fluxo: veículos ativos (D), CBR (E) e "
                    "distância (F).")
    parser.add_argument("--grafico", choices=["densidade", "cbr", "distancia", "todos"], default="todos",
                        help="'densidade': métricas × veículos ativos por execução; 'cbr': PDR e latência × CBR "
                             "da execução; 'distancia': PDR × distância por fluxo; 'todos': os três.")
    parser.add_argument("--modo", choices=["unico", "separado"], default="unico",
                        help="'unico': os painéis de cada gráfico numa imagem só; 'separado': uma imagem por painel.")
    args = parser.parse_args()

    if args.grafico in ("densidade", "cbr", "todos"):
        seeds = read_csv(SEED_CSV, ["Topology", "Protocol", DENSITY_X] + DENSITY_METRICS)
        if args.grafico in ("densidade", "todos"):
            plot_density(seeds, args.modo)
        if args.grafico in ("cbr", "todos"):
            plot_cbr(seeds, args.modo)
    if args.grafico in ("distancia", "todos"):
        distance = read_csv(DISTANCE_CSV, ["Topology", "Scenario", "Protocol", "Distance bin", "Distance min (m)",
                                           "PDR (%)", "PDR (%) CI95"])
        plot_distance(distance, args.modo)


if __name__ == "__main__":
    main()
