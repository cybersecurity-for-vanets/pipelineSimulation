"""Colunas derivadas e estilo de desenho dos níveis de fluxo (demanda CET × demanda extrapolada) e rótulos de LOS.

Compartilhado pela etapa 4 (src/) e pelos gráficos (plotting/). Os ids e rótulos dos níveis vêm de flow_levels.py.

Regra visual: plot_level_series() desenha todos os níveis com o mesmo estilo (sem diferenciar demanda CET de
demanda extrapolada); scatter_levels() e level_origin_handles() ainda mantêm essa diferenciação.
- annotate_los() escreve a letra do LOS do HCM de cada nível no topo do painel.
"""
import numpy as np
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D

from src.analysis.flow_levels import level_los, level_sort_key, parse_level
from matplotlib.transforms import blended_transform_factory


LEVEL_KIND_COLUMN = "Level kind"
SURVEY_YEAR_COLUMN = "Survey year"
OBSERVED = "observed"
SYNTHETIC = "synthetic"
ANCHORED_LABEL = "demanda CET (pico)"
EXTRAPOLATED_LABEL = "demanda extrapolada"
EXTRAPOLATED_LINE_ALPHA = 0.35
EXTRAPOLATED_FACE = "white"


def _parse(level):
    try:
        return parse_level(str(level))
    except ValueError:
        return None


def safe_level_sort_key(level):
    """level_sort_key(); ids desconhecidos (ex. 'Unknown') vão para o fim."""
    return level_sort_key(level) if _parse(level) else (2, 0, 0, 0.0)


def sort_levels(levels):
    return sorted(set(levels), key=safe_level_sort_key)


def level_kind(level):
    info = _parse(level)
    return info["kind"] if info else np.nan


def survey_year(level):
    info = _parse(level)
    return info.get("year", np.nan) if info else np.nan


def add_level_columns(frame, column="Scenario"):
    """Acrescenta 'Level kind' (observed/synthetic) e 'Survey year' (NaN para sintético) a partir do id do nível."""
    levels = frame[column].astype(str)
    return frame.assign(**{
        LEVEL_KIND_COLUMN: levels.map(level_kind),
        SURVEY_YEAR_COLUMN: levels.map(survey_year).astype("Int64"),
    })


def is_extrapolated(level):
    """True para nível sintético fora do pico CET (fator ≠ 1,00); o pico e os níveis observados são demanda CET."""
    info = _parse(level)
    return bool(info) and info["kind"] == SYNTHETIC and info["factor"] != 1.0


def extrapolated_mask(levels):
    return np.array([is_extrapolated(level) for level in levels], dtype=bool)


def los_of(topology, level):
    """Letra do LOS do nível na topologia (conf/flow_levels.json), ou None."""
    try:
        return level_los(topology, str(level))
    except (KeyError, FileNotFoundError, ValueError):
        return None


def annotate_los(axis, topology, x, levels, color="#404040", fontsize=7):
    """Letra do LOS de cada nível no topo do painel, na posição x do nível (eixo x em dados, y em fração do eixo)."""
    transform = blended_transform_factory(axis.transData, axis.transAxes)
    seen = set()
    for value, level in zip(np.asarray(x, dtype=float), levels):
        los = los_of(topology, level)
        if los is None or not np.isfinite(value) or level in seen:
            continue
        seen.add(level)
        axis.text(value, 1.01, los, transform=transform, ha="center", va="bottom", fontsize=fontsize, color=color)


def plot_level_series(axis, x, y, levels, *, color, marker="o", linestyle="-", yerr=None, linewidth=1.6,
                      markersize=5, capsize=2, zorder=2):
    """Uma série sobre o eixo de fluxo: pontos ligados em ordem de x, todos com o mesmo estilo.

    `levels` é mantido na assinatura por compatibilidade; demanda CET e extrapolada não são diferenciadas.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    err = None if yerr is None else np.asarray(yerr, dtype=float)
    keep = np.isfinite(x) & np.isfinite(y)
    order = np.argsort(x[keep], kind="stable")
    x, y = x[keep][order], y[keep][order]
    if err is not None:
        err = np.nan_to_num(err[keep][order])
    if x.size == 0:
        return

    axis.plot(x, y, color=color, linestyle=linestyle, linewidth=linewidth, marker=marker, markersize=markersize,
              zorder=zorder)
    if err is not None:
        axis.errorbar(x, y, yerr=err, fmt="none", ecolor=color, elinewidth=1.0, capsize=capsize, zorder=zorder)


def scatter_levels(axis, x, y, levels, *, color, marker="o", s=22, alpha=0.7, **kwargs):
    """Dispersão com marcador cheio para demanda CET e vazado para demanda extrapolada."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    synthetic = extrapolated_mask(levels)
    axis.scatter(x[~synthetic], y[~synthetic], s=s, alpha=alpha, color=color, marker=marker, **kwargs)
    axis.scatter(x[synthetic], y[synthetic], s=s, alpha=alpha, marker=marker, facecolors=EXTRAPOLATED_FACE,
                 edgecolors=color, linewidths=1.1, **{k: v for k, v in kwargs.items() if k != "linewidths"})


def level_origin_handles(color="gray", with_line=True):
    """Entradas de legenda 'demanda CET (pico)' e 'demanda extrapolada' (linha esmaecida só quando with_line)."""
    line = "-" if with_line else ""
    anchored = Line2D([], [], color=color, marker="o", linestyle=line, label=ANCHORED_LABEL)
    extrapolated = Line2D([], [], color=to_rgba(color, EXTRAPOLATED_LINE_ALPHA), marker="o", linestyle=line,
                          markerfacecolor=EXTRAPOLATED_FACE, markeredgecolor=color, markeredgewidth=1.3,
                          label=EXTRAPOLATED_LABEL)
    return [anchored, extrapolated]
