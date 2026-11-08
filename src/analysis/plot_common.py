"""Constantes e utilidades compartilhadas pelos scripts de gráficos (ver CONVENCOES.md)."""
import os
from pathlib import Path

import matplotlib.pyplot as plt

from src.analysis.flow_levels import level_label
from src.analysis.level_style import (
    annotate_los,
    extrapolated_mask,
    is_extrapolated,
    level_origin_handles,
    los_of,
    plot_level_series,
    scatter_levels,
    sort_levels,
)
from src.env import DEFAULT_EXTERNAL_DIR, ROOT_DIR
from src.simulation.protocols import load_protocols

def _resolve_results_dir():
    ssd_path = DEFAULT_EXTERNAL_DIR / "results"
    local_path = ROOT_DIR / "results"
    return ssd_path if ssd_path.is_dir() else local_path

RESULTS_DIR = _resolve_results_dir()
RAW_DATA_DIR = RESULTS_DIR / "rawData"

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
