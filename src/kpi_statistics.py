"""Paired comparison against the baseline and equivalence tests for the FF1 protocol study.

Runs of different protocols share a seed (the "-#N" repetition in the run id), so every protocol run
is compared with the baseline run of the same topology, flow and repetition. Differences are
summarised with a t-based 95% CI. "No harm" KPIs are tested for equivalence with TOST against the
margins in conf/kpi_margins.json, and Holm-adjusted across all tests.
"""

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


BASELINE = "baseline"
MARGINS_PATH = Path(__file__).resolve().parent.parent / "conf" / "kpi_margins.json"
GROUP = ["Topology", "Scenario", "Protocol"]
ALPHA = 0.05


# OMNeT++ run ids look like "<config>-<repetition>-<yyyymmdd>-<hh:mm:ss>-<pid>".
RUN_ID_PATTERN = re.compile(r"-(\d+)-\d{8}-")
SUMMARY_COLUMNS = GROUP + ["KPI", "N", "MeanDiff", "SD", "CI95"]
EQUIVALENCE_COLUMNS = GROUP + ["KPI", "N", "MeanDiff", "Margin", "P_TOST", "P_Holm", "Equivalent"]


def repetition_of(run_id):
    match = RUN_ID_PATTERN.search(str(run_id))
    if match is None:
        raise ValueError(f"Cannot read the repetition from run id '{run_id}'")
    return match.group(1)


def paired_differences(run_values, kpi):
    rows = run_values[run_values["KPI"] == kpi].copy()
    rows["Repetition"] = rows["run"].map(repetition_of)
    baseline = (
        rows[rows["Protocol"] == BASELINE][["Topology", "Scenario", "Repetition", "Value"]]
        .rename(columns={"Value": "Baseline"})
    )
    paired = rows[rows["Protocol"] != BASELINE].merge(
        baseline, on=["Topology", "Scenario", "Repetition"], how="inner"
    )
    paired["Difference"] = paired["Value"] - paired["Baseline"]
    paired["KPI"] = kpi
    return paired[GROUP + ["KPI", "Repetition", "Baseline", "Value", "Difference"]]


def _standard_error(differences):
    n = len(differences)
    if n < 2:
        return np.nan
    return differences.std(ddof=1) / np.sqrt(n)


def summarize_differences(paired):
    rows = []
    for (topology, scenario, protocol, kpi), group in paired.groupby(GROUP + ["KPI"]):
        differences = group["Difference"].to_numpy()
        n = len(differences)
        standard_error = _standard_error(group["Difference"])
        t_critical = stats.t.ppf(1 - ALPHA / 2, n - 1) if n > 1 else np.nan
        rows.append({
            "Topology": topology,
            "Scenario": scenario,
            "Protocol": protocol,
            "KPI": kpi,
            "N": n,
            "MeanDiff": differences.mean(),
            "SD": differences.std(ddof=1) if n > 1 else np.nan,
            "CI95": t_critical * standard_error,
        })
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def _tost_p_value(mean, standard_error, margin, degrees_of_freedom):
    if not np.isfinite(standard_error) or standard_error == 0:
        # Identical paired values (e.g. bytes per CAM): equivalent iff the mean lies inside the margin.
        return 0.0 if abs(mean) < margin else 1.0
    lower = stats.t.sf((mean + margin) / standard_error, degrees_of_freedom)
    upper = stats.t.cdf((mean - margin) / standard_error, degrees_of_freedom)
    return max(lower, upper)


def holm_adjust(p_values):
    p_values = np.asarray(p_values, dtype=float)
    m = len(p_values)
    order = np.argsort(p_values)
    adjusted = np.empty(m)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, (m - rank) * p_values[index])
        adjusted[index] = min(running, 1.0)
    return adjusted


def load_margins(path=MARGINS_PATH):
    with Path(path).open("r", encoding="utf-8") as source:
        return {kpi: entry["margin"] for kpi, entry in json.load(source).items()}


def equivalence_tests(paired, margins):
    rows = []
    for (topology, scenario, protocol, kpi), group in paired.groupby(GROUP + ["KPI"]):
        if kpi not in margins:
            continue
        differences = group["Difference"].to_numpy()
        n = len(differences)
        if n < 2:
            continue
        mean = differences.mean()
        p_value = _tost_p_value(mean, _standard_error(group["Difference"]), margins[kpi], n - 1)
        rows.append({
            "Topology": topology,
            "Scenario": scenario,
            "Protocol": protocol,
            "KPI": kpi,
            "N": n,
            "MeanDiff": mean,
            "Margin": margins[kpi],
            "P_TOST": p_value,
        })
    result = pd.DataFrame(rows, columns=EQUIVALENCE_COLUMNS)
    if result.empty:
        return result
    result["P_Holm"] = holm_adjust(result["P_TOST"])
    result["Equivalent"] = result["P_Holm"] < ALPHA
    return result
