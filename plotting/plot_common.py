"""Constantes e utilidades compartilhadas pelos scripts de gráficos (ver CONVENCOES.md)."""
import os
import sys

import matplotlib.pyplot as plt

SSD_DIR = os.path.dirname("/Volumes/PSSD T7/simulationData/")
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(SSD_DIR):
    RESULTS_DIR = os.path.join(SSD_DIR, "results")
else:
    RESULTS_DIR = os.path.join(PROJECT_DIR, "results")
RAW_DATA_DIR = os.path.join(RESULTS_DIR, "rawData")

sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))
from flow_levels import level_label  # noqa: E402
from level_style import (annotate_los, extrapolated_mask, is_extrapolated, level_origin_handles,  # noqa: E402,F401
                         los_of, plot_level_series, scatter_levels, sort_levels)
from protocols import load_protocols  # noqa: E402

TOPOLOGIES = ["highway", "urbanRoad", "intersection"]
PROTOCOLS = list(load_protocols())

# Níveis de fluxo (ver src/flow_levels.py): um por LOS do HCM em cada topologia (s045 = 0,45× do pico CET; s100 =
# pico CET). O eixo de fluxo é o volume numérico por faixa; o rótulo vem de level_label() e o LOS de los_of().
# Demanda CET × extrapolada: src/level_style.py.
FLOW_X = "Flow per lane (veh/h/lane)"
FLOW_AXIS_LABEL = "Volume por faixa (veh/h/faixa)"

METRIC_AXIS_LABELS = {
    "PDR (%)": "Taxa de entrega de pacotes (%)",
    "CBR (%)": "Taxa de ocupação do canal (%)",
    "Latency E2E (ms)": "Latência fim a fim (ms)",
    "Active vehicles": "Veículos ativos simultâneos (média)",
}
METRIC_TITLES = {"PDR (%)": "PDR", "CBR (%)": "CBR", "Latency E2E (ms)": "Latência E2E"}
METRIC_FILE_NAMES = {"PDR (%)": "pdr", "CBR (%)": "cbr", "Latency E2E (ms)": "latencia_e2e"}

COLORS = {"highway": "#1f77b4", "urbanRoad": "#ff7f0e", "intersection": "#2ca02c"}
MARKERS = ["o", "s", "^", "D", "v", "P"]


def protocol_marker(protocol):
    return MARKERS[PROTOCOLS.index(protocol) % len(MARKERS)]


def flow_levels_present(values):
    """Níveis presentes nos dados, em ordem de level_sort_key (observados por ano/hora, depois sintéticos)."""
    return sort_levels(values)


def save(figure, plots_dir, file_name):
    os.makedirs(plots_dir, exist_ok=True)
    path = os.path.join(plots_dir, file_name)
    figure.savefig(path, dpi=200)
    plt.close(figure)
    print(f"[OK] Gráfico salvo em: '{path}'")
