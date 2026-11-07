import argparse
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import NullFormatter, ScalarFormatter

from plot_common import (COLORS, FLOW_X, METRIC_AXIS_LABELS, METRIC_FILE_NAMES, METRIC_TITLES,
                         PROTOCOLS, RAW_DATA_DIR, RESULTS_DIR, TOPOLOGIES, is_extrapolated, level_origin_handles,
                         los_of, plot_level_series, protocol_marker, save, scatter_levels)
from level_style import EXTRAPOLATED_FACE, EXTRAPOLATED_LINE_ALPHA

# ==============================================================================
# 1. DIRETÓRIOS E CONFIGURAÇÕES
# ==============================================================================
SEED_CSV = os.path.join(RAW_DATA_DIR, "flow_kpis_per_seed.csv")
SUMMARY_CSV = os.path.join(RAW_DATA_DIR, "flow_kpis_by_topology.csv")
DISTANCE_CSV = os.path.join(RAW_DATA_DIR, "flow_kpis_by_distance.csv")
PLOTS_DIR = os.path.join(RESULTS_DIR, "plots", "flow_kpis")

DENSITY_X = "Active vehicles"
CBR_X = "CBR (%)"
DENSITY_METRICS = ["PDR (%)", "CBR (%)", "Latency E2E (ms)"]
CBR_METRICS = ["PDR (%)", "Latency E2E (ms)"]

# Protocolo: estilo da linha de tendência (o marcador já identifica os pontos).
PROTOCOL_LINESTYLES = ["-", "--", ":", "-.", (0, (5, 1)), (0, (3, 1, 1, 1))]
# Curvas por distância: até quatro níveis por topologia, escolhidos pelo papel no regime de tráfego (ver
# representative_levels); o estilo da linha identifica o papel e o título do painel diz o LOS de cada um.
REPRESENTATIVE_LEVELS = [("LOS mais leve", ":"), ("LOS intermediário", "-."), ("pico CET", "--"),
                         ("acima do pico", "-")]
PEAK_LEVEL = "s100"
CBR_BINS = 12           # faixas de CBR da curva média (todas as topologias juntas)
MIN_POINTS_PER_BIN = 3  # faixas com menos execuções ficam de fora da curva
DISTANCE_BIN_M = 50     # largura das faixas de distância (LatencyTraCIDemo11p.h)
LEGEND_MAX_COLUMNS = 3  # colunas por legenda: legendas maiores quebram em mais linhas e cabem no modo separado


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
    """Um ponto por execução: cor = topologia, marcador = protocolo; vazado = demanda extrapolada (fora do pico CET)."""
    for topology in present(data["Topology"], TOPOLOGIES):
        for protocol in present(data["Protocol"], PROTOCOLS):
            group = data[(data["Topology"] == topology) & (data["Protocol"] == protocol)]
            if group.empty:
                continue
            scatter_levels(axis, group[x_column], group[metric], group["Scenario"], s=18, alpha=0.55,
                           color=COLORS[topology], marker=protocol_marker(protocol))


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


def draw_flow_per_lane(axis, data, metric):
    """G: média e IC 95% de cada nível × veh/h/faixa; uma linha por topologia e protocolo, ligando os níveis em ordem
    de volume. Demanda extrapolada: marcador vazado e segmento esmaecido."""
    for topology in present(data["Topology"], TOPOLOGIES):
        for protocol in present(data["Protocol"], PROTOCOLS):
            group = data[(data["Topology"] == topology) & (data["Protocol"] == protocol)]
            if group.empty:
                continue
            plot_level_series(axis, group[FLOW_X], group[metric], group["Scenario"], yerr=group[f"{metric} CI95"],
                              color=COLORS[topology], marker=protocol_marker(protocol),
                              linestyle=protocol_linestyle(protocol), markersize=5, linewidth=1.6)

    # Escala log: highway (centenas a milhares de veh/h/faixa) e as vias urbanas (dezenas) no mesmo eixo.
    axis.set_xscale("log")
    axis.xaxis.set_major_formatter(ScalarFormatter())
    axis.xaxis.set_minor_formatter(NullFormatter())
    axis.set_title(METRIC_TITLES[metric])
    axis.set_xlabel("Volume por faixa (veh/h/faixa, escala log)")
    axis.set_ylabel(METRIC_AXIS_LABELS[metric])
    axis.grid(True, which="both", linestyle=":", alpha=0.6)


def representative_levels(data, topology):
    """{papel: (nível, veh/h/faixa)} para os papéis de REPRESENTATIVE_LEVELS presentes na topologia.

    LOS mais leve = menor volume; pico CET = s100; acima do pico = maior volume, se passar do pico; LOS intermediário =
    o nível do meio entre os que sobram abaixo do pico. Sem s100 (rodadas antigas), o pico é o maior volume.
    """
    flows = (data[data["Topology"] == topology][["Scenario", FLOW_X]].drop_duplicates("Scenario")
             .dropna(subset=[FLOW_X]).sort_values(FLOW_X))
    if flows.empty:
        return {}
    rows = list(zip(flows["Scenario"], flows[FLOW_X]))
    peak = next((row for row in rows if row[0] == PEAK_LEVEL), rows[-1])
    picks = {"LOS mais leve": rows[0], "pico CET": peak}
    if rows[-1][1] > peak[1]:
        picks["acima do pico"] = rows[-1]
    middle = [row for row in rows if row[1] < peak[1] and row != rows[0]]
    if middle:
        picks["LOS intermediário"] = middle[(len(middle) - 1) // 2]
    return {role: picks[role] for role, _ in REPRESENTATIVE_LEVELS if role in picks}


def draw_distance(axis, data, topology):
    """F: PDR × distância de uma topologia nos níveis representativos; estilo da linha = papel do nível (LOS mais
    leve, intermediário, pico CET, acima do pico), marcador = protocolo, barras = IC 95%; demanda extrapolada vazada
    e esmaecida."""
    data = data[data["Topology"] == topology]
    protocols = present(data["Protocol"], PROTOCOLS)
    bins = sorted(data["Distance bin"].unique())
    spread = DISTANCE_BIN_M * 0.06
    picks = representative_levels(data, topology)
    linestyles = dict(REPRESENTATIVE_LEVELS)
    for role, (flow, _) in picks.items():
        linestyle = linestyles[role]
        extrapolated = is_extrapolated(flow)
        for offset, protocol in enumerate(protocols):
            group = data[(data["Scenario"] == flow) & (data["Protocol"] == protocol)].sort_values("Distance bin")
            if group.empty:
                continue
            x = group["Distance min (m)"] + DISTANCE_BIN_M / 2 + (offset - (len(protocols) - 1) / 2) * spread
            axis.errorbar(x, group["PDR (%)"], yerr=group["PDR (%) CI95"], color=COLORS[topology],
                          linestyle=linestyle, marker=protocol_marker(protocol), markersize=5, linewidth=1.6,
                          capsize=2, alpha=EXTRAPOLATED_LINE_ALPHA if extrapolated else 1.0)
            if extrapolated:
                axis.plot(x, group["PDR (%)"], linestyle="", marker=protocol_marker(protocol), markersize=5,
                          color=COLORS[topology], markerfacecolor=EXTRAPOLATED_FACE, markeredgewidth=1.3)

    centers = [index * DISTANCE_BIN_M + DISTANCE_BIN_M / 2 for index in bins]
    labels = [f"{index * DISTANCE_BIN_M}–{(index + 1) * DISTANCE_BIN_M}" for index in bins]
    if labels:
        labels[-1] = f"≥ {bins[-1] * DISTANCE_BIN_M}"
    axis.set_xticks(centers)
    axis.set_xticklabels(labels)
    # Título: topologia e, na segunda linha, o LOS e o volume de cada nível desenhado, na ordem dos papéis.
    described = " · ".join(f"{los_of(topology, flow) or flow} {per_lane:.0f}" for flow, per_lane in picks.values())
    axis.set_title(f"{topology}\n{described} veh/h/faixa", fontsize=9)
    axis.set_xlabel("Distância emissor–receptor (m)")
    axis.set_ylabel(METRIC_AXIS_LABELS["PDR (%)"])
    axis.grid(True, linestyle=":", alpha=0.6)


def add_legends(figure, upper_left, upper_right, statistic):
    """Duas legendas lado a lado; abaixo delas, a origem da demanda (pico CET/extrapolada) e, por último, a que
    explica a marca estatística (statistic[0]). Todas abaixo dos painéis."""
    title_left, handles_left = upper_left
    title_right, handles_right = upper_right
    explanation, origin = statistic[:1], statistic[1:]
    # Altura (fração da figura) de uma linha de legenda sem título; menor nas figuras mais altas.
    row = 0.045 * 5 / figure.get_figheight()
    upper_y = 0.07 + (row if origin else 0)
    figure.legend(handles=handles_left, title=title_left, fontsize=8, ncol=min(len(handles_left), LEGEND_MAX_COLUMNS),
                  loc="lower right", bbox_to_anchor=(0.5, upper_y))
    figure.legend(handles=handles_right, title=title_right, fontsize=8, ncol=min(len(handles_right), LEGEND_MAX_COLUMNS),
                  loc="lower left", bbox_to_anchor=(0.5, upper_y))
    if origin:
        figure.legend(handles=origin, fontsize=8, loc="lower center", bbox_to_anchor=(0.5, row), ncol=len(origin))
    figure.legend(handles=explanation, fontsize=8, loc="lower center", bbox_to_anchor=(0.5, 0.0))
    rows = max(-(-len(handles) // LEGEND_MAX_COLUMNS) for handles in (handles_left, handles_right))
    figure.tight_layout(rect=(0, upper_y + 0.13 + 0.05 * (rows - 1), 1, 1))


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
    statistic += level_origin_handles(with_line=False)
    plot_metric_curves(data, DENSITY_METRICS, draw_density, statistic, True, "curva_densidade", modo)


def plot_cbr(data, modo):
    statistic = [Line2D([], [], color="black", linewidth=1.8,
                        label="Linha preta = média por faixa de CBR, todas as topologias (estilo = protocolo)")]
    statistic += level_origin_handles(with_line=False)
    plot_metric_curves(data, CBR_METRICS, draw_cbr, statistic, True, "curva_cbr", modo)


def plot_flow_per_lane(data, modo):
    statistic = [Line2D([], [], color="gray", marker="o", linewidth=1.6,
                        label="Ponto = média entre sementes; barra vertical = IC 95%; linha liga os níveis em ordem "
                              "de volume")] + level_origin_handles()
    plot_metric_curves(data, DENSITY_METRICS, draw_flow_per_lane, statistic, True, "curva_fluxo_faixa", modo)


def plot_distance(data, modo):
    topologies = present(data["Topology"], TOPOLOGIES)
    flow_handles = [Line2D([], [], color="#404040", linestyle=linestyle, linewidth=1.8, label=name)
                    for name, linestyle in REPRESENTATIVE_LEVELS]
    legends = (("Nível (papel no regime de tráfego)", flow_handles), ("Protocolo", protocol_handles(data, False)))
    statistic = [Line2D([], [], color="gray", marker="|", markersize=10, linestyle="",
                        label="Barra vertical = IC 95% entre sementes; cor = topologia")]
    statistic += level_origin_handles()
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
        description="Curvas de KPI com mais pontos que os níveis de fluxo: veículos ativos (D), CBR (E), "
                    "distância (F) e volume por faixa (G).")
    parser.add_argument("--grafico", choices=["densidade", "cbr", "distancia", "fluxo", "todos"], default="todos",
                        help="'densidade': métricas × veículos ativos por execução; 'cbr': PDR e latência × CBR "
                             "da execução; 'distancia': PDR × distância nos níveis de menor, mediano e maior volume; 'fluxo': métricas × veh/h/faixa; "
                             "'todos': os quatro.")
    parser.add_argument("--modo", choices=["unico", "separado"], default="unico",
                        help="'unico': os painéis de cada gráfico numa imagem só; 'separado': uma imagem por painel.")
    args = parser.parse_args()

    if args.grafico in ("densidade", "cbr", "todos"):
        seeds = read_csv(SEED_CSV, ["Topology", "Scenario", "Protocol", DENSITY_X] + DENSITY_METRICS)
        if args.grafico in ("densidade", "todos"):
            plot_density(seeds, args.modo)
        if args.grafico in ("cbr", "todos"):
            plot_cbr(seeds, args.modo)
    if args.grafico in ("fluxo", "todos"):
        summary = read_csv(SUMMARY_CSV, ["Topology", "Scenario", "Protocol", FLOW_X] + DENSITY_METRICS
                           + [f"{metric} CI95" for metric in DENSITY_METRICS])
        plot_flow_per_lane(summary, args.modo)
    if args.grafico in ("distancia", "todos"):
        distance = read_csv(DISTANCE_CSV, ["Topology", "Scenario", "Protocol", "Distance bin", "Distance min (m)",
                                           "PDR (%)", "PDR (%) CI95", FLOW_X])
        plot_distance(distance, args.modo)


if __name__ == "__main__":
    main()
