"""Colunas derivadas e estilo de desenho dos níveis de fluxo (observado CET × sintético).

Compartilhado pela etapa 4 (src/) e pelos gráficos (plotting/). Os ids e rótulos dos níveis vêm de flow_levels.py.

Regra visual (ver plotting/CONVENCOES.md):
- nível observado (CET): marcador cheio e segmento de linha opaco;
- nível sintético: marcador vazado (preenchimento branco) e segmento de linha esmaecido (SYNTHETIC_LINE_ALPHA)
  sempre que uma das pontas do segmento for sintética. O estilo da linha (sólida, tracejada...) fica livre para
  codificar o protocolo.
"""
import numpy as np
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D

try:
    from .flow_levels import level_sort_key, parse_level
except ImportError:
    from flow_levels import level_sort_key, parse_level


LEVEL_KIND_COLUMN = "Level kind"
SURVEY_YEAR_COLUMN = "Survey year"
OBSERVED = "observed"
SYNTHETIC = "synthetic"
OBSERVED_LABEL = "observado (CET)"
SYNTHETIC_LABEL = "sintético"
SYNTHETIC_LINE_ALPHA = 0.35
SYNTHETIC_FACE = "white"


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


def synthetic_mask(levels):
    return np.array([level_kind(level) == SYNTHETIC for level in levels], dtype=bool)


def plot_level_series(axis, x, y, levels, *, color, marker="o", linestyle="-", yerr=None, linewidth=1.6,
                      markersize=5, capsize=2, zorder=2):
    """Uma série sobre o eixo de fluxo: pontos ligados em ordem de x, com o estilo observado/sintético."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    synthetic = synthetic_mask(levels)
    err = None if yerr is None else np.asarray(yerr, dtype=float)
    keep = np.isfinite(x) & np.isfinite(y)
    order = np.argsort(x[keep], kind="stable")
    x, y, synthetic = x[keep][order], y[keep][order], synthetic[keep][order]
    if err is not None:
        err = np.nan_to_num(err[keep][order])
    if x.size == 0:
        return

    for start in range(x.size - 1):
        faded = synthetic[start] or synthetic[start + 1]
        axis.plot(x[start:start + 2], y[start:start + 2], color=color, linestyle=linestyle, linewidth=linewidth,
                  alpha=SYNTHETIC_LINE_ALPHA if faded else 1.0, zorder=zorder)
    if err is not None:
        axis.errorbar(x, y, yerr=err, fmt="none", ecolor=color, elinewidth=1.0, capsize=capsize, zorder=zorder)
    axis.plot(x[~synthetic], y[~synthetic], linestyle="", marker=marker, markersize=markersize, color=color,
              zorder=zorder + 1)
    axis.plot(x[synthetic], y[synthetic], linestyle="", marker=marker, markersize=markersize, color=color,
              markerfacecolor=SYNTHETIC_FACE, markeredgewidth=1.3, zorder=zorder + 1)


def scatter_levels(axis, x, y, levels, *, color, marker="o", s=22, alpha=0.7, **kwargs):
    """Dispersão com marcador cheio para nível observado e vazado para sintético."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    synthetic = synthetic_mask(levels)
    axis.scatter(x[~synthetic], y[~synthetic], s=s, alpha=alpha, color=color, marker=marker, **kwargs)
    axis.scatter(x[synthetic], y[synthetic], s=s, alpha=alpha, marker=marker, facecolors=SYNTHETIC_FACE,
                 edgecolors=color, linewidths=1.1, **{k: v for k, v in kwargs.items() if k != "linewidths"})


def level_origin_handles(color="gray", with_line=True):
    """Entradas de legenda 'observado (CET)' e 'sintético' (linha esmaecida só quando with_line)."""
    line = "-" if with_line else ""
    observed = Line2D([], [], color=color, marker="o", linestyle=line, label=OBSERVED_LABEL)
    synthetic = Line2D([], [], color=to_rgba(color, SYNTHETIC_LINE_ALPHA), marker="o", linestyle=line,
                       markerfacecolor=SYNTHETIC_FACE, markeredgecolor=color, markeredgewidth=1.3,
                       label=SYNTHETIC_LABEL)
    return [observed, synthetic]
