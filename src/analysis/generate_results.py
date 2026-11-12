#!/usr/bin/env python3
"""Unified Results & Discussion Generator for POLI-USP VANET Simulation.

Generates the curated publication-grade figures (.png at 300 DPI) and
comparative summary tables (.csv, .md, and LaTeX .tex) specifically tailored
for the Results and Discussion chapter of the Master's thesis / paper.

Usage:
    python -m src.analysis.generate_results              # reuses results/processed/*.csv if newer than the .sca, else processes them
    python -m src.analysis.generate_results --reprocess  # always re-extracts the KPIs from the .sca files
    python -m src.analysis.generate_results --demo   # synthetic data only (no simulation results needed)
"""

import argparse
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from scipy import stats

from src.analysis.flow_levels import flow_per_lane, is_current_config, level_label, level_los, split_level
from src.analysis.level_style import (
    add_level_columns,
    annotate_los,
    plot_level_series,
    safe_level_sort_key,
)
from src.analysis.statistics import (
    equivalence_tests,
    load_margins,
    paired_differences,
    summarize_differences,
)
from src.analysis.extract_kpis import extract_run_values
from src.env import CONFIG_DIR, PROCESSED_DATA_DIR, RAW_DATA_DIR, RESULTS_DIR
from src.simulation.protocols import load_protocols

# ==============================================================================
# Configuration & Paths
# ==============================================================================
TOPOLOGIES = ["highway", "urbanRoad", "intersection"]
PROTOCOLS = list(load_protocols())
BASELINE = "baseline"
FLOW_X = "Flow per lane (veh/h/lane)"

# Academic publication style (IEEE / Springer / Poli-USP)
plt.style.use("seaborn-v0_8-paper" if "seaborn-v0_8-paper" in plt.style.available else "default")
plt.rcParams.update({
    "font.family": "serif",
    "font.size": 10,
    "axes.labelsize": 11,
    "axes.titlesize": 12,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 9,
    "figure.titlesize": 13,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "lines.linewidth": 1.5,
})

PROTOCOL_COLORS = {
    "baseline": "#2b5c8f",    # Classic Navy
    "FF1_AES": "#d95f02",     # Vermilion / Orange
    "FF1_LEA": "#7570b3",     # Purple
    "FF1_SPECK": "#1b9e77",   # Teal / Green
}
PROTOCOL_MARKERS = {
    "baseline": "o",
    "FF1_AES": "s",
    "FF1_LEA": "^",
    "FF1_SPECK": "D",
}


PROCESSED_FILES = ("kpi_run_values.csv", "kpi_run_summary.csv", "paired_summary.csv", "equivalence_tests.csv")


def processed_csvs_are_current(raw_dir, out_dir):
    """True if every processed CSV exists and is newer than the newest .sca in raw_dir."""
    csv_paths = [out_dir / name for name in PROCESSED_FILES]
    if not all(path.is_file() for path in csv_paths):
        return False
    sca_mtimes = [path.stat().st_mtime for path in raw_dir.glob("*.sca")]
    if not sca_mtimes:
        return True  # nothing to reprocess from; reuse what exists
    return min(path.stat().st_mtime for path in csv_paths) > max(sca_mtimes)


def load_processed_results(out_dir):
    print(f"\n[PASSO 0/2] Reutilizando CSVs processados de: {out_dir} (use --reprocess para refazer)")
    return tuple(pd.read_csv(out_dir / name) for name in PROCESSED_FILES)


def process_simulation_results(raw_dir, out_dir):
    """Stage 4 data processing: .sca files -> per-run KPIs -> summaries, paired differences and TOST.

    Writes kpi_run_values.csv, kpi_run_summary.csv, paired_summary.csv and equivalence_tests.csv to out_dir
    so the processed data can be inspected or reused outside this script.
    """
    print(f"\n[PASSO 0/2] Processando resultados das simulações em: {raw_dir}")
    run_values = extract_run_values(raw_dir, 6)  # 6 workers for parallel processing
    summary, paired_summary, equivalence = compute_summaries(run_values)

    frames = (run_values, summary, paired_summary, equivalence)
    for name, frame in zip(PROCESSED_FILES, frames):
        frame.to_csv(out_dir / name, index=False)
        print(f"  [OK] Gerado: {out_dir / name}")
    return frames


# ==============================================================================
# Demo / Fallback Data Generator (Based on Calibrated Matrix in docs/ & config/)
# ==============================================================================
def generate_demo_dataset():
    """Generates synthetic run values adhering to the calibrated parameters for offline analysis."""
    print("[INFO] Reconstruindo dados calibrados da matriz experimental (560 execuções)...")

    # Load crypto benchmark timings
    crypto_file = CONFIG_DIR / "crypto_timings.json"
    crypto_data = {}
    if crypto_file.is_file():
        with open(crypto_file) as f:
            c_json = json.load(f)
            for proto in PROTOCOLS:
                tx_val = c_json.get(proto, {}).get("tx_delay_us")
                rx_val = c_json.get(proto, {}).get("rx_delay_us")
                if tx_val is None or rx_val is None:
                    stats_json = c_json.get("run_statistics", {})
                    tx_val = stats_json.get(proto, {}).get("tx_delay_us", {}).get("median_us", 234.0 if proto == "baseline" else 330.0)
                    rx_val = stats_json.get(proto, {}).get("rx_delay_us", {}).get("median_us", 621.0 if proto == "baseline" else 715.0)
                crypto_data[proto] = {"tx_ms": tx_val / 1000.0, "rx_ms": rx_val / 1000.0}
    else:
        crypto_data = {
            "baseline": {"tx_ms": 0.234, "rx_ms": 0.621},
            "FF1_AES": {"tx_ms": 0.335, "rx_ms": 0.722},
            "FF1_LEA": {"tx_ms": 0.321, "rx_ms": 0.708},
            "FF1_SPECK": {"tx_ms": 0.319, "rx_ms": 0.707},
        }

    # Load flow levels
    flow_file = CONFIG_DIR / "flow_levels.json"
    with open(flow_file) as f:
        flow_json = json.load(f)

    np.random.seed(42)
    rows = []
    seeds = range(10)

    for topology in TOPOLOGIES:
        topo_levels = flow_json["topologies"].get(topology, {})
        for scenario, s_info in topo_levels.items():
            los = s_info.get("los", "C")
            factor = s_info.get("factor", 1.0)
            flow_lane = s_info.get("veh_h_per_lane", 500.0)

            # Baseline traffic dynamics
            if topology == "highway":
                base_cbr = 2.9 + 17.5 * (factor / 1.2)
                base_pdr = 98.3 - 12.8 * (factor / 1.2)
                base_e2e = 1.25 + 0.35 * (factor / 1.2)
                active_veh = int(308 + (2008 - 308) * (factor / 1.2))
            elif topology == "urbanRoad":
                base_cbr = 4.7 + 8.4 * (factor / 1.0)
                base_pdr = 97.9 - 6.0 * (factor / 1.0)
                base_e2e = 1.30 + 0.25 * (factor / 1.0)
                active_veh = int(281 + (746 - 281) * (factor / 1.0))
            else: # intersection
                base_cbr = 1.4 + 5.5 * (factor / 1.2)
                base_pdr = 99.5 - 3.1 * (factor / 1.2)
                base_e2e = 1.15 + 0.20 * (factor / 1.2)
                active_veh = int(104 + (485 - 104) * (factor / 1.2))

            for seed in seeds:
                run_id = f"Config_{topology}_{scenario}-{seed}-20261107-120000-1000"
                noise_cbr = np.random.normal(0, 0.25)
                noise_pdr = np.random.normal(0, 0.35)
                noise_e2e = np.random.normal(0, 0.03)

                for protocol in PROTOCOLS:
                    base_dict = {"run": run_id, "Topology": topology, "Protocol": protocol, "Scenario": scenario}
                    c_info = crypto_data[protocol]
                    crypto_total = c_info["tx_ms"] + c_info["rx_ms"]
                    delta_crypto = crypto_total - (crypto_data["baseline"]["tx_ms"] + crypto_data["baseline"]["rx_ms"])

                    # Exact metrics
                    pdr_val = max(50.0, min(100.0, base_pdr + noise_pdr + np.random.normal(0, 0.05)))
                    cbr_val = max(1.0, min(90.0, base_cbr + noise_cbr + np.random.normal(0, 0.04)))
                    e2e_val = max(0.5, base_e2e + delta_crypto + noise_e2e)
                    proc_val = crypto_total + np.random.normal(0, 0.005)
                    net_val = max(0.2, e2e_val - proc_val)
                    cpu_val = max(1.0, min(100.0, (proc_val * 10.0) * (cbr_val / 10.0) * 1.5 + np.random.normal(0, 0.5)))
                    queue_wait = max(0.01, 0.05 + 0.2 * (cbr_val / 20.0)**2 + (0.05 if "FF1" in protocol else 0.0))

                    rows.extend([
                        {**base_dict, "KPI": "Latency (ms)", "Value": e2e_val},
                        {**base_dict, "KPI": "Processing latency (ms)", "Value": proc_val},
                        {**base_dict, "KPI": "Processing latency p95 (ms)", "Value": proc_val * 1.15},
                        {**base_dict, "KPI": "Processing latency p99 (ms)", "Value": proc_val * 1.35},
                        {**base_dict, "KPI": "Network latency (ms)", "Value": net_val},
                        {**base_dict, "KPI": "PDR (%)", "Value": pdr_val},
                        {**base_dict, "KPI": "Channel Busy Rate (%)", "Value": cbr_val},
                        {**base_dict, "KPI": "CPU utilization (%)", "Value": cpu_val},
                        {**base_dict, "KPI": "Mean queue wait (ms)", "Value": queue_wait},
                        {**base_dict, "KPI": "Deadline misses (%)", "Value": 0.0},
                        {**base_dict, "KPI": "Crypto TX processing (ms)", "Value": c_info["tx_ms"]},
                        {**base_dict, "KPI": "Crypto RX processing (ms)", "Value": c_info["rx_ms"]},
                        {**base_dict, "KPI": "Bytes per CAM", "Value": 250.0},
                        {**base_dict, "KPI": "FF1 decrypt failures", "Value": 0.0},
                        {**base_dict, "KPI": "Active vehicles", "Value": active_veh},
                    ])

                    # Distance bins for PDR
                    if topology == "highway" and scenario == "s100":  # Highway Peak LOS F
                        dist_drops = [96.6, 92.4, 86.9, 82.1, 78.5, 76.0]
                        for b_idx, drop in enumerate(dist_drops):
                            b_low, b_high = b_idx * 50, (b_idx + 1) * 50
                            rows.append({
                                **base_dict,
                                "KPI": f"PDR {b_low}-{b_high} m (%)",
                                "Value": max(10.0, drop + np.random.normal(0, 0.4)),
                            })

    run_values = pd.DataFrame(rows)
    return run_values


# ==============================================================================
# Metric Summarization & Statistical Tests
# ==============================================================================
def compute_summaries(run_values):
    """Aggregate per-run values into summary statistics and paired differences."""
    summary = run_values.groupby(
        ["Topology", "Protocol", "Scenario", "KPI"], as_index=False
    )["Value"].agg(Mean="mean", Std="std", N="count")
    summary["CI95"] = 1.96 * summary["Std"].fillna(0) / np.sqrt(summary["N"])

    flows = {}
    for key in summary[["Topology", "Scenario"]].drop_duplicates().itertuples(index=False):
        try:
            flows[key] = flow_per_lane(key[0], key[1])
        except Exception:
            flows[key] = np.nan
    summary[FLOW_X] = [flows.get(key, np.nan) for key in zip(summary["Topology"], summary["Scenario"])]
    summary = add_level_columns(summary)
    summary = summary.sort_values(
        ["Topology", "Protocol", "Scenario", "KPI"],
        key=lambda col: col.map(safe_level_sort_key) if col.name == "Scenario" else col,
    ).reset_index(drop=True)

    # Paired differences vs Baseline
    paired_kpis = [
        "Latency (ms)", "Processing latency (ms)", "Network latency (ms)",
        "PDR (%)", "Channel Busy Rate (%)", "CPU utilization (%)",
        "Mean queue wait (ms)", "Deadline misses (%)", "Bytes per CAM"
    ]
    present_kpis = set(run_values["KPI"].unique())
    paired_list = [paired_differences(run_values, kpi) for kpi in paired_kpis if kpi in present_kpis]
    paired = pd.concat(paired_list, ignore_index=True) if paired_list else pd.DataFrame()
    paired_summary = summarize_differences(paired) if not paired.empty else pd.DataFrame()

    # TOST Equivalence Tests
    margins = load_margins()
    equivalence = equivalence_tests(paired, margins) if not paired.empty else pd.DataFrame()

    return summary, paired_summary, equivalence


# ==============================================================================
# Figure 1: Cryptographic Latency Breakdown
# ==============================================================================
def plot_fig1_crypto_breakdown(summary, out_dir):
    fig, ax = plt.subplots(figsize=(8, 4.8))
    crypto_kpis = summary[summary["KPI"].isin(["Crypto TX processing (ms)", "Crypto RX processing (ms)"])]
    if crypto_kpis.empty:
        plt.close(fig)
        return

    # Average across all scenarios to get true cipher cost
    cipher_stats = crypto_kpis.groupby(["Protocol", "KPI"])["Mean"].mean().unstack("KPI")
    tx_vals = (cipher_stats["Crypto TX processing (ms)"] * 1000).reindex(PROTOCOLS)  # µs
    rx_vals = (cipher_stats["Crypto RX processing (ms)"] * 1000).reindex(PROTOCOLS)  # µs

    x = np.arange(len(PROTOCOLS))
    width = 0.55

    bars_tx = ax.bar(x, tx_vals, width, label="TX Delay (Assinatura ECDSA + Cifra FF1)", color="#2b5c8f", edgecolor="black", alpha=0.9)
    bars_rx = ax.bar(x, rx_vals, width, bottom=tx_vals, label="RX Delay (Verificação ECDSA + Decifra FF1)", color="#e76f51", edgecolor="black", alpha=0.9)

    ax.set_ylabel("Tempo de Processamento Criptográfico (µs)")
    ax.set_title("Decomposição do Overhead Criptográfico por Protocolo de CAM", pad=12, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([p.replace("_", "-") for p in PROTOCOLS], fontweight="bold")
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    # Legend floats inside the plot; the raised y-limit below reserves headroom so it
    # never overlaps the overhead labels drawn above the bars
    ax.legend(loc="upper left", fontsize=9, framealpha=0.95)

    # Add labels on bars
    for idx, (tx, rx) in enumerate(zip(tx_vals, rx_vals)):
        tot = tx + rx
        ax.text(idx, tx / 2, f"{tx:.0f} µs", ha="center", va="center", color="white", fontsize=8, fontweight="bold")
        ax.text(idx, tx + rx / 2, f"{rx:.0f} µs", ha="center", va="center", color="white", fontsize=8, fontweight="bold")
        overhead_pct = ((tot / (tx_vals.iloc[0] + rx_vals.iloc[0])) - 1) * 100
        ov_text = "Ref. (0%)" if idx == 0 else f"+{overhead_pct:.1f}% ({tot:.0f} µs)"
        ax.annotate(ov_text, (idx, tot), xytext=(0, 4), textcoords="offset points",
                    ha="center", va="bottom", fontsize=9, fontweight="bold")

    ax.set_ylim(0, max(tx_vals + rx_vals) * 1.35)
    fig.tight_layout()
    path = out_dir / "fig1_crypto_latency_breakdown.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"  [OK] Gerado: {path.name}")


# ==============================================================================
# Figure 2: End-to-End Latency vs. Flow per Lane
# ==============================================================================
def _padded_limits(low, high, pad=0.08, floor=None, ceil=None):
    """Data-driven axis limits with a small relative padding, optionally clamped."""
    span = high - low
    if not np.isfinite(span) or span <= 0:
        span = max(abs(high), 1.0) * 0.1
    low, high = low - pad * span, high + pad * span
    if floor is not None:
        low = max(low, floor)
    if ceil is not None:
        high = min(high, ceil)
    return low, high


def plot_flow_with_deltas(summary, paired_summary, out_dir, *, kpi, ylabel, delta_ylabel, title, filename,
                          threshold=None, always_show_threshold=False, margin=None, floor=None, ceil=None):
    """Two-row flow figure: absolute KPI per protocol (top) and paired Δ vs. baseline with 95% CI (bottom).

    The top row uses data-driven y-limits; the bottom row zooms on the differences, which are too small to
    tell apart on the absolute scale. `threshold` is a (value, label) reference line drawn on the top row
    when it lies near the data (or always, with `always_show_threshold`); `margin` is the TOST equivalence margin drawn on the bottom row.
    """
    data = summary[summary["KPI"] == kpi].dropna(subset=[FLOW_X, "Mean"])
    if data.empty:
        return
    deltas = pd.DataFrame()
    if paired_summary is not None and not paired_summary.empty:
        deltas = paired_summary[paired_summary["KPI"] == kpi].copy()
        deltas[FLOW_X] = [flow_per_lane(t, sc) for t, sc in zip(deltas["Topology"], deltas["Scenario"])]
        deltas = deltas.dropna(subset=[FLOW_X, "MeanDiff"])

    fig, axes = plt.subplots(2, len(TOPOLOGIES), figsize=(15, 6.8), sharey="row", sharex="col",
                             gridspec_kw={"height_ratios": [2.2, 1.3], "hspace": 0.08})
    top_axes, delta_axes = axes
    candidates = [p for p in PROTOCOLS if p != BASELINE]
    legend_handles = {}

    for col, topology in enumerate(TOPOLOGIES):
        ax, dax = top_axes[col], delta_axes[col]
        t_data = data[data["Topology"] == topology]
        for protocol in PROTOCOLS:
            p_data = t_data[t_data["Protocol"] == protocol].sort_values(FLOW_X)
            if p_data.empty:
                continue
            color, marker = PROTOCOL_COLORS[protocol], PROTOCOL_MARKERS[protocol]
            plot_level_series(ax, p_data[FLOW_X], p_data["Mean"], p_data["Scenario"],
                              color=color, marker=marker, yerr=p_data["CI95"], markersize=5)
            legend_handles[protocol] = Line2D([], [], color=color, marker=marker, label=protocol.replace("_", "-"))

        levels = t_data["Scenario"].drop_duplicates()
        annotate_los(ax, topology, [flow_per_lane(topology, f) for f in levels], levels)
        ax.set_title(f"Topologia: {topology.capitalize()}", pad=14, fontweight="bold")
        ax.grid(True, linestyle=":", alpha=0.6)

        # Bottom row: paired Δ, slightly dodged on x so the three ciphers do not hide each other
        t_delta = deltas[deltas["Topology"] == topology] if not deltas.empty else deltas
        x_span = np.ptp(t_data[FLOW_X]) if len(t_data) else 0.0
        dodge = 0.012 * (x_span if x_span > 0 else 1.0)
        for idx, protocol in enumerate(candidates):
            p_delta = t_delta[t_delta["Protocol"] == protocol].sort_values(FLOW_X) if not t_delta.empty else t_delta
            if p_delta.empty:
                continue
            plot_level_series(dax, p_delta[FLOW_X] + (idx - 1) * dodge, p_delta["MeanDiff"], p_delta["Scenario"],
                              color=PROTOCOL_COLORS[protocol], marker=PROTOCOL_MARKERS[protocol],
                              yerr=p_delta["CI95"], markersize=4, linewidth=1.2)
        dax.axhline(0, color=PROTOCOL_COLORS[BASELINE], linewidth=1.0, zorder=1)
        dax.set_xlabel("Volume por Faixa (veh/h/faixa)")
        dax.grid(True, linestyle=":", alpha=0.6)

    # Top row: tight limits around mean ± CI, extended to the reference line only when it is near the data
    low = (data["Mean"] - data["CI95"]).min()
    high = (data["Mean"] + data["CI95"]).max()
    if threshold is not None and (always_show_threshold or threshold[0] <= high + 0.5 * (high - low)):
        high = max(high, threshold[0])
        for ax in top_axes:
            ax.axhline(threshold[0], color="#d90429", linestyle="--", linewidth=1.2, alpha=0.75)
        legend_handles["threshold"] = Line2D([], [], color="#d90429", linestyle="--", label=threshold[1])
    top_axes[0].set_ylim(*_padded_limits(low, high, floor=floor, ceil=ceil))
    top_axes[0].set_ylabel(ylabel)

    # Bottom row: limits sized by Δ ± CI, always including zero (one-sided when every Δ has the same sign)
    if not deltas.empty:
        ci = deltas["CI95"].fillna(0)
        low = min(0.0, (deltas["MeanDiff"] - ci).min())
        high = max(0.0, (deltas["MeanDiff"] + ci).max())
        if margin is not None:
            extent = max(abs(low), abs(high))
            if margin <= extent * 1.6:
                low, high = min(low, -margin if low < 0 else low), max(high, margin if high > 0 else high)
                for dax in delta_axes:
                    dax.axhspan(-margin, margin, color="#999999", alpha=0.12, zorder=0)
                legend_handles["margin"] = plt.Rectangle((0, 0), 1, 1, color="#999999", alpha=0.25,
                                                         label=f"Margem de equivalência TOST (±{margin:g})")
            else:
                delta_axes[-1].text(0.98, 0.04, f"Margem TOST ±{margin:g} fora da escala",
                                    transform=delta_axes[-1].transAxes, ha="right", va="bottom",
                                    fontsize=8, color="#555555")
        delta_axes[0].set_ylim(*_padded_limits(low, high, pad=0.15))
    delta_axes[0].set_ylabel(delta_ylabel)

    handles = list(legend_handles.values())
    fig.legend(handles=handles, loc="upper center", ncol=len(handles), bbox_to_anchor=(0.5, 0.97), fontsize=9)
    fig.suptitle(title, y=1.01, fontweight="bold")
    path = out_dir / filename
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"  [OK] Gerado: {path.name}")


def plot_fig2_e2e_latency_vs_flow(summary, paired_summary, out_dir):
    plot_flow_with_deltas(
        summary, paired_summary, out_dir,
        kpi="Latency (ms)",
        ylabel="Latência Fim a Fim $T_{E2E}$ (ms)",
        delta_ylabel=r"$\Delta$ vs. baseline (ms)",
        title=r"Latência Fim a Fim vs. Volume de Tráfego por Topologia (Média, $\Delta$ Pareado e IC 95%)",
        filename="fig2_e2e_latency_vs_flow.png",
        threshold=(10.0, "Limite Platooning (10 ms)"),
        floor=0.0,
    )


# ==============================================================================
# Figure 3: E2E Latency Boxplots Across Topologies
# ==============================================================================
def plot_fig3_latency_boxplots(run_values, out_dir):
    data = run_values[run_values["KPI"] == "Latency (ms)"]
    if data.empty:
        return

    fig, axes = plt.subplots(1, len(TOPOLOGIES), figsize=(14, 4.5), sharey=True)

    for ax, topology in zip(axes, TOPOLOGIES):
        t_data = data[data["Topology"] == topology]
        vals_by_proto = [t_data.loc[t_data["Protocol"] == p, "Value"].dropna().values for p in PROTOCOLS]

        bp = ax.boxplot(vals_by_proto, tick_labels=[p.replace("_", "-") for p in PROTOCOLS], patch_artist=True, showfliers=False)
        for patch, proto in zip(bp['boxes'], PROTOCOLS):
            patch.set_facecolor(PROTOCOL_COLORS[proto])
            patch.set_alpha(0.7)
        for median in bp['medians']:
            median.set(color='black', linewidth=1.8)

        ax.set_title(f"Topologia: {topology.capitalize()}", fontweight="bold")
        ax.set_xlabel("Protocolo")
        ax.grid(axis="y", linestyle=":", alpha=0.6)

    axes[0].set_ylabel("Latência Fim a Fim $T_{E2E}$ (ms)")
    fig.suptitle("Distribuição da Latência Fim a Fim entre Repetições e Regimes", fontweight="bold", y=0.98)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = out_dir / "fig3_latency_distribution_boxplots.png"
    fig.savefig(path)
    plt.close(fig)
    print(f"  [OK] Gerado: {path.name}")


# ==============================================================================
# Figure 4: Packet Delivery Ratio (PDR) vs. Flow per Lane
# ==============================================================================
def plot_fig4_pdr_vs_flow(summary, paired_summary, out_dir):
    plot_flow_with_deltas(
        summary, paired_summary, out_dir,
        kpi="PDR (%)",
        ylabel="Taxa de Entrega de Pacotes - PDR (%)",
        delta_ylabel=r"$\Delta$ vs. baseline (p.p.)",
        title="Taxa de Entrega de Pacotes (PDR) vs. Volume de Tráfego por Topologia",
        filename="fig4_pdr_vs_flow.png",
        margin=load_margins().get("PDR (%)"),
        ceil=100.5,
    )


# ==============================================================================
# Figure 5: PDR Distance Decay on Highway LOS F
# ==============================================================================
def plot_fig5_pdr_distance_decay(summary, out_dir):
    dist_kpis = [f"PDR {i*50}-{(i+1)*50} m (%)" for i in range(6)]
    data = summary[summary["KPI"].isin(dist_kpis) & (summary["Topology"] == "highway")]
    if data.empty:
        return

    # Choose peak congestion scenario (s100 or highest flow)
    scenarios = data["Scenario"].unique()
    peak_scenario = "s100" if "s100" in scenarios else scenarios[-1]
    peak_data = data[data["Scenario"] == peak_scenario]

    fig, ax = plt.subplots(figsize=(8, 4.8))
    bins = [f"{i*50}-{(i+1)*50}" for i in range(6)]
    x = np.arange(len(bins))
    width = 0.2

    for idx, proto in enumerate(PROTOCOLS):
        proto_data = peak_data[peak_data["Protocol"] == proto].set_index("KPI")
        means = [proto_data.loc[k, "Mean"] if k in proto_data.index else np.nan for k in dist_kpis]
        errs = [proto_data.loc[k, "CI95"] if k in proto_data.index else 0 for k in dist_kpis]

        ax.bar(
            x + (idx - 1.5) * width, means, width,
            yerr=errs, capsize=3, label=proto.replace("_", "-"),
            color=PROTOCOL_COLORS[proto], edgecolor="black", alpha=0.85
        )

    ax.set_ylabel("PDR Médio (%)")
    ax.set_xlabel("Faixa de Distância Transmissor-Receptor (m)")
    ax.set_title("Degradação Espacial do PDR por Distância (Highway - LOS F Saturação)", fontweight="bold", pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels(bins)
    ax.set_ylim(60, 102)
    ax.grid(axis="y", linestyle=":", alpha=0.6)
    ax.legend(loc="lower left")

    fig.tight_layout()
    path = out_dir / "fig5_pdr_distance_decay_highway.png"
    fig.savefig(path)
    plt.close(fig)
    print(f"  [OK] Gerado: {path.name}")


# ==============================================================================
# Figure 6: Channel Busy Ratio (CBR) vs. Flow
# ==============================================================================
def plot_fig6_cbr_vs_flow(summary, paired_summary, out_dir):
    margins = load_margins()
    plot_flow_with_deltas(
        summary, paired_summary, out_dir,
        kpi="Channel Busy Rate (%)",
        ylabel="Ocupação do Canal - CBR (%)",
        delta_ylabel=r"$\Delta$ vs. baseline (p.p.)",
        title="Taxa de Ocupação do Canal de Rádio (CBR) vs. Volume de Tráfego",
        filename="fig6_cbr_vs_flow.png",
        threshold=(30.0, "Início do DCC (CBR 30%)"),
        always_show_threshold=True,
        margin=margins.get("Channel Busy Rate (%)", margins.get("CBR (%)")),
        floor=0.0,
    )


# ==============================================================================
# Figure 7: Paired Differences (Deltas vs. Baseline)
# ==============================================================================
def pooled_mean_ci(levels):
    """Mean of the per-level paired Δ and its 95% CI, from paired_summary rows (one per demand level).

    Levels are independent simulations, so Var(mean) = Σ(SD_i²/N_i)/k²; the degrees of freedom follow
    Welch–Satterthwaite. Averaging the per-level CI95 instead would report the width of a single level's
    interval, not the uncertainty of the pooled mean.
    """
    levels = levels.dropna(subset=["MeanDiff"])
    if levels.empty:
        return np.nan, np.nan
    k = len(levels)
    variances = (levels["SD"] ** 2 / levels["N"]).to_numpy(dtype=float)
    mean = levels["MeanDiff"].mean()
    if not np.all(np.isfinite(variances)) or variances.sum() == 0:
        return mean, 0.0
    standard_error = np.sqrt(variances.sum()) / k
    dof_terms = variances ** 2 / (levels["N"].to_numpy(dtype=float) - 1)
    dof = variances.sum() ** 2 / dof_terms.sum()
    return mean, stats.t.ppf(0.975, dof) * standard_error


def plot_fig7_paired_deltas(paired_summary, out_dir):
    if paired_summary.empty:
        return

    target_kpis = ["Latency (ms)", "Processing latency (ms)", "PDR (%)"]
    fig, axes = plt.subplots(1, len(target_kpis), figsize=(16, 5))

    proto_candidates = [p for p in PROTOCOLS if p != BASELINE]
    x = np.arange(len(proto_candidates))
    width = 0.25

    titles = {
        "Latency (ms)": r"$\Delta$ Latência Fim a Fim (ms)",
        "Processing latency (ms)": r"$\Delta$ Latência de Processamento (ms)",
        "PDR (%)": r"$\Delta$ PDR (pontos percentuais)",
    }

    for ax, kpi in zip(axes, target_kpis):
        k_data = paired_summary[paired_summary["KPI"] == kpi]
        for t_idx, topology in enumerate(TOPOLOGIES):
            t_data = k_data[k_data["Topology"] == topology]
            means, errs = [], []
            for proto in proto_candidates:
                mean, ci = pooled_mean_ci(t_data[t_data["Protocol"] == proto])
                means.append(mean)
                errs.append(ci)

            ax.bar(
                x + (t_idx - 1) * width, means, width, yerr=errs, capsize=3,
                label=topology.capitalize(), alpha=0.85, edgecolor="black"
            )

        ax.axhline(0, color="black", linestyle="-", linewidth=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels([p.replace("_", "-") for p in proto_candidates], fontweight="bold")
        ax.set_title(titles.get(kpi, kpi), fontweight="bold", pad=10)
        ax.grid(axis="y", linestyle=":", alpha=0.6)

    axes[0].legend(loc="upper left")
    fig.suptitle(r"Diferenças Pareadas em Relação ao Baseline ($\Delta = \mathrm{Protocolo} - \mathrm{Baseline}$, média dos níveis de demanda e IC 95%)", y=0.98, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = out_dir / "fig7_paired_deltas_vs_baseline.png"
    fig.savefig(path)
    plt.close(fig)
    print(f"  [OK] Gerado: {path.name}")


# ==============================================================================
# Figure 8: Comprehensive Executive Heatmap
# ==============================================================================
def plot_fig8_executive_heatmap(summary, paired_summary, out_dir):
    fig, axes = plt.subplots(2, 3, figsize=(15, 9))
    axes = axes.flatten()

    def get_matrix(kpi, df, col="Mean"):
        sub = df[df["KPI"] == kpi]
        table = sub.pivot_table(index="Topology", columns="Protocol", values=col, aggfunc="mean")
        return table.reindex(index=TOPOLOGIES, columns=PROTOCOLS)

    def get_paired_matrix(kpi):
        if paired_summary.empty:
            return pd.DataFrame()
        sub = paired_summary[paired_summary["KPI"] == kpi]
        table = sub.pivot_table(index="Topology", columns="Protocol", values="MeanDiff", aggfunc="mean")
        return table.reindex(index=TOPOLOGIES, columns=[p for p in PROTOCOLS if p != BASELINE])

    panels = [
        ("Latência Fim a Fim (ms)", get_matrix("Latency (ms)", summary), "Blues", "{:.2f}"),
        ("Taxa de Entrega - PDR (%)", get_matrix("PDR (%)", summary), "Greens", "{:.1f}%"),
        ("Ocupação do Canal - CBR (%)", get_matrix("Channel Busy Rate (%)", summary), "YlOrRd", "{:.1f}%"),
        (r"$\Delta$ Processamento vs Baseline (ms)", get_paired_matrix("Processing latency (ms)"), "RdPu", "+{:.3f}"),
        (r"$\Delta$ Latência Fim a Fim (ms)", get_paired_matrix("Latency (ms)"), "RdPu", "+{:.3f}"),
        (r"$\Delta$ PDR vs Baseline (p.p.)", get_paired_matrix("PDR (%)"), "coolwarm", "{:+.3f}"),
    ]

    for ax, (title, df_mat, cmap, fmt) in zip(axes, panels):
        if df_mat.empty or df_mat.dropna(how="all").empty:
            ax.set_visible(False)
            continue
        cols = [c.replace("_", "-") for c in df_mat.columns]
        # Diverging maps must be centred on zero so the neutral colour means "no change".
        limits = {}
        if cmap == "coolwarm":
            lim = np.nanmax(np.abs(df_mat.values.astype(float)))
            if lim > 0:
                limits = {"vmin": -lim, "vmax": lim}
        im = ax.imshow(df_mat.values, cmap=cmap, aspect="auto", **limits)
        ax.set_xticks(range(len(cols)))
        ax.set_xticklabels(cols, rotation=20, ha="right", fontsize=9)
        ax.set_yticks(range(len(TOPOLOGIES)))
        ax.set_yticklabels([t.capitalize() for t in TOPOLOGIES], fontweight="bold")
        ax.set_title(title, fontweight="bold", pad=8, fontsize=10)

        for i in range(df_mat.shape[0]):
            for j in range(df_mat.shape[1]):
                val = df_mat.values[i, j]
                if np.isfinite(val):
                    r, g, b, _ = im.cmap(im.norm(val))
                    luminance = 0.299 * r + 0.587 * g + 0.114 * b
                    text_color = "black" if luminance > 0.5 else "white"
                    ax.text(j, i, fmt.format(val), ha="center", va="center", color=text_color, fontsize=9, fontweight="bold")

        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    fig.suptitle("Mapa Térmico Executivo: Desempenho Global e Impacto Criptográfico", fontweight="bold", y=0.99, fontsize=13)
    fig.tight_layout()
    path = out_dir / "fig8_executive_heatmaps.png"
    fig.savefig(path)
    plt.close(fig)
    print(f"  [OK] Gerado: {path.name}")


# ==============================================================================
# Summary Tables Generators (CSV, Markdown, LaTeX)
# ==============================================================================
def dataframe_to_markdown(df):
    """Formats DataFrame as a GitHub Flavored Markdown table without external dependencies."""
    headers = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(val) for val in row.values) + " |")
    return "\n".join(lines)


def dataframe_to_latex(df):
    """Formats DataFrame as a clean booktabs LaTeX table without Jinja2 dependency."""
    headers = [str(c) for c in df.columns]
    col_align = "l" + "c" * (len(headers) - 1)
    lines = [
        f"\\begin{{tabular}}{{{col_align}}}",
        "\\toprule",
        " & ".join(headers) + " \\\\",
        "\\midrule",
    ]
    for _, row in df.iterrows():
        lines.append(" & ".join(str(val) for val in row.values) + " \\\\")
    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    return "\n".join(lines)


def save_table_formats(df, base_path, caption, label):
    """Saves dataframe into CSV, formatted Markdown, and LaTeX .tex formats."""
    # CSV
    df.to_csv(f"{base_path}.csv", index=False)

    # Markdown
    with open(f"{base_path}.md", "w", encoding="utf-8") as f:
        f.write(f"### {caption}\n\n")
        f.write(dataframe_to_markdown(df))
        f.write("\n")

    # LaTeX Table
    with open(f"{base_path}.tex", "w", encoding="utf-8") as f:
        latex_str = dataframe_to_latex(df)
        f.write(f"% Table: {caption}\n")
        f.write("\\begin{table}[htbp]\n")
        f.write("\\centering\n")
        f.write(f"\\caption{{{caption}}}\n")
        f.write(f"\\label{{{label}}}\n")
        f.write(latex_str + "\n")
        f.write("\\end{table}\n")


def generate_tables(summary, paired_summary, equivalence, tables_dir):
    """Generates 4 publication tables for the Results and Discussion chapter."""
    print("[INFO] Gerando tabelas comparativas formatadas (.csv, .md, .tex)...")

    # Table 1: Global Performance Summary across Topologies and Protocols
    t1_rows = []
    for topology in TOPOLOGIES:
        for proto in PROTOCOLS:
            sub = summary[(summary["Topology"] == topology) & (summary["Protocol"] == proto)]
            def get_kpi_stat(kpi_name):
                row = sub[sub["KPI"] == kpi_name]
                if row.empty:
                    return "-"
                m = row["Mean"].mean()
                ci = row["CI95"].mean()
                return f"{m:.2f} $\\pm$ {ci:.2f}"

            t1_rows.append({
                "Topology": topology.capitalize(),
                "Protocol": proto.replace("_", "-"),
                "PDR (%)": get_kpi_stat("PDR (%)"),
                "E2E Latency (ms)": get_kpi_stat("Latency (ms)"),
                "Processing (ms)": get_kpi_stat("Processing latency (ms)"),
                "Network (ms)": get_kpi_stat("Network latency (ms)"),
                "CBR (%)": get_kpi_stat("Channel Busy Rate (%)"),
                "CPU (%)": get_kpi_stat("CPU utilization (%)"),
            })
    df_t1 = pd.DataFrame(t1_rows)
    save_table_formats(
        df_t1,
        tables_dir / "table1_global_performance_summary",
        "Resumo Global de Desempenho por Topologia e Protocolo (Média $\\pm$ IC 95\\%)",
        "tab:global_performance"
    )

    # Table 2: Cryptographic Overhead Breakdown
    t2_rows = []
    for proto in PROTOCOLS:
        sub = summary[summary["Protocol"] == proto]
        tx_us = sub[sub["KPI"] == "Crypto TX processing (ms)"]["Mean"].mean() * 1000
        rx_us = sub[sub["KPI"] == "Crypto RX processing (ms)"]["Mean"].mean() * 1000
        total_us = tx_us + rx_us
        p95_ms = sub[sub["KPI"] == "Processing latency p95 (ms)"]["Mean"].mean()
        p99_ms = sub[sub["KPI"] == "Processing latency p99 (ms)"]["Mean"].mean()

        base_sub = summary[summary["Protocol"] == BASELINE]
        base_tot = (base_sub[base_sub["KPI"] == "Crypto TX processing (ms)"]["Mean"].mean() +
                    base_sub[base_sub["KPI"] == "Crypto RX processing (ms)"]["Mean"].mean()) * 1000
        delta_us = total_us - base_tot
        delta_pct = (delta_us / base_tot) * 100 if base_tot > 0 else 0

        t2_rows.append({
            "Protocol": proto.replace("_", "-"),
            "TX Delay (µs)": f"{tx_us:.1f}",
            "RX Delay (µs)": f"{rx_us:.1f}",
            "Total Crypto (µs)": f"{total_us:.1f}",
            r"$\Delta$ Overhead (µs)": f"{delta_us:+.1f}",
            "Overhead Relativo": "Referência" if proto == BASELINE else f"{delta_pct:+.1f}\\%",
            "Proc. Latency p95 (ms)": f"{p95_ms:.3f}",
            "Proc. Latency p99 (ms)": f"{p99_ms:.3f}",
        })
    df_t2 = pd.DataFrame(t2_rows)
    save_table_formats(
        df_t2,
        tables_dir / "table2_crypto_overhead_breakdown",
        "Decomposição do Custo Computacional e Latência de Processamento por Protocolo",
        "tab:crypto_overhead"
    )

    # Table 3: TOST Equivalence Tests Summary
    if not equivalence.empty:
        t3_rows = []
        for (kpi, proto), group in equivalence.groupby(["KPI", "Protocol"]):
            margin = group["Margin"].iloc[0]
            mean_diff = group["MeanDiff"].mean()
            ci = 1.96 * group["MeanDiff"].std() / np.sqrt(len(group)) if len(group) > 1 else 0.0
            p_holm = group["P_Holm"].max()
            equiv_pass = (group["Equivalent"] == 1).all()

            t3_rows.append({
                "Métrica Avaliada": kpi,
                "Comparação": f"{proto.replace('_', '-')} vs Baseline",
                "Margem Equivalência": f"$\\pm {margin:g}$",
                r"$\Delta$ Médio Pareado": f"{mean_diff:+.3f} $\\pm$ {ci:.3f}",
                "Valor-p (Holm)": f"{p_holm:.4e}" if p_holm < 0.001 else f"{p_holm:.3f}",
                "Equivalência Estatística": "\\textbf{Aprovado}" if equiv_pass else "Reprovado",
            })
        df_t3 = pd.DataFrame(t3_rows)
        save_table_formats(
            df_t3,
            tables_dir / "table3_statistical_equivalence_tost",
            "Resultados dos Testes de Equivalência TOST Ajustados por Holm (Confirmação de Invariância)",
            "tab:tost_equivalence"
        )

    # Table 4: Highway LOS F Distance Binned PDR
    dist_kpis = [f"PDR {i*50}-{(i+1)*50} m (%)" for i in range(6)]
    hwy_f = summary[(summary["Topology"] == "highway") & (summary["Scenario"].isin(["s120", "s100"]))]
    if not hwy_f.empty:
        t4_rows = []
        for i in range(6):
            k = f"PDR {i*50}-{(i+1)*50} m (%)"
            row = {"Faixa de Distância": f"{i*50} a {(i+1)*50} m"}
            for proto in PROTOCOLS:
                sub = hwy_f[(hwy_f["Protocol"] == proto) & (hwy_f["KPI"] == k)]
                val = sub["Mean"].mean() if not sub.empty else np.nan
                row[proto.replace("_", "-")] = f"{val:.1f}\\%" if pd.notna(val) else "-"
            t4_rows.append(row)
        df_t4 = pd.DataFrame(t4_rows)
        save_table_formats(
            df_t4,
            tables_dir / "table4_pdr_by_distance_los_f",
            "Taxa de Entrega (PDR) por Faixa de Distância em Alta Congestão (Highway - LOS F)",
            "tab:pdr_distance_decay"
        )

    # Table 5: Traffic Regimes & Physical Channel Load by Topology
    regimes_file = CONFIG_DIR / "flow_levels.json"
    if regimes_file.is_file():
        with open(regimes_file) as f:
            f_json = json.load(f)
        t5_rows = []
        for topology in TOPOLOGIES:
            topo_dict = f_json.get("topologies", {}).get(topology, {})
            for scen, s_data in topo_dict.items():
                los = s_data.get("los", "-")
                factor = s_data.get("factor", 1.0)
                flow_lane = s_data.get("veh_h_per_lane", 0)
                tot_flow = s_data.get("total_veh_h", 0)

                # Get simulated CBR and active vehicles from summary
                sub = summary[(summary["Topology"] == topology) & (summary["Scenario"] == scen) & (summary["Protocol"] == BASELINE)]
                cbr_val = sub[sub["KPI"] == "Channel Busy Rate (%)"]["Mean"].mean() if not sub.empty else np.nan
                veh_val = sub[sub["KPI"] == "Active vehicles"]["Mean"].mean() if not sub.empty else np.nan

                t5_rows.append({
                    "Topologia": topology.capitalize(),
                    "Cenário": scen,
                    "LOS (HCM)": los,
                    "Fator Demanda": f"{factor:.2f}x",
                    "Fluxo por Faixa (veh/h)": f"{flow_lane:.0f}",
                    "Fluxo Total (veh/h)": f"{tot_flow:,}",
                    "Veículos Ativos": f"{veh_val:.0f}" if pd.notna(veh_val) else "-",
                    "CBR Médio": f"{cbr_val:.1f}\\%" if pd.notna(cbr_val) else "-",
                })
        df_t5 = pd.DataFrame(t5_rows)
        save_table_formats(
            df_t5,
            tables_dir / "table5_traffic_regimes_summary",
            "Regimes de Tráfego Calibrados e Ocupação do Canal de Rádio por Cenário",
            "tab:traffic_regimes"
        )

    print(f"  [OK] Tabelas salvas em formato .csv, .md e .tex em: {tables_dir}")


# ==============================================================================
# Main Orchestrator
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Gera todos os gráficos e tabelas essenciais para a seção de Resultados e Discussão."
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Usa dados SINTÉTICOS (gerados a partir da matriz calibrada) em vez dos .sca das simulações",
    )
    parser.add_argument(
        "--tables-only",
        action="store_true",
        help="Gera apenas as tabelas comparativas",
    )
    parser.add_argument(
        "--plots-only",
        action="store_true",
        help="Gera apenas os gráficos .png",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Reextrai os KPIs dos .sca mesmo se os CSVs em results/processed/ estiverem atualizados",
    )
    args = parser.parse_args()

    plots_dir = RESULTS_DIR / "plots" / "results_discussion"
    tables_dir = RESULTS_DIR / "tables"
    plots_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(" GERADOR DE RESULTADOS E DISCUSSÃO (POLI-USP VANET FF1 STUDY)")
    print(f" Diretório de Gráficos (.png): {plots_dir}")
    print(f" Diretório de Tabelas (.csv, .md, .tex): {tables_dir}")
    print("=" * 70)

    # 1. Dados: simulações reais (.sca) por padrão; dados sintéticos apenas com --demo.
    if args.demo:
        print("[AVISO] Modo --demo: usando dados SINTÉTICOS, não os resultados das simulações.")
        run_values = generate_demo_dataset()
        summary, paired_summary, equivalence = compute_summaries(run_values)
    elif not args.reprocess and processed_csvs_are_current(RAW_DATA_DIR, PROCESSED_DATA_DIR):
        run_values, summary, paired_summary, equivalence = load_processed_results(PROCESSED_DATA_DIR)
    else:
        try:
            run_values, summary, paired_summary, equivalence = process_simulation_results(RAW_DATA_DIR, PROCESSED_DATA_DIR)
        except (FileNotFoundError, ValueError) as error:
            raise SystemExit(f"[ERRO] {error}\n        Use --demo para gerar os artefatos com dados sintéticos.")

    # 2. Geração dos Gráficos Relevantes (.png 300 DPI)
    if not args.tables_only:
        print("\n[PASSO 1/2] Gerando figuras científicas essenciais (.png 300 DPI)...")
        plot_fig1_crypto_breakdown(summary, plots_dir)
        plot_fig2_e2e_latency_vs_flow(summary, paired_summary, plots_dir)
        if not run_values.empty:
            plot_fig3_latency_boxplots(run_values, plots_dir)
        plot_fig4_pdr_vs_flow(summary, paired_summary, plots_dir)
        plot_fig5_pdr_distance_decay(summary, plots_dir)
        plot_fig6_cbr_vs_flow(summary, paired_summary, plots_dir)
        plot_fig7_paired_deltas(paired_summary, plots_dir)
        plot_fig8_executive_heatmap(summary, paired_summary, plots_dir)

    # 3. Geração das Tabelas Comparativas (.csv, .md, .tex)
    if not args.plots_only:
        print("\n[PASSO 2/2] Gerando tabelas comparativas para dissertação...")
        generate_tables(summary, paired_summary, equivalence, tables_dir)

    print("\n" + "=" * 70)
    print(" SUCESSO! Todos os artefatos para a seção de Resultados e Discussão foram gerados:")
    print(f" -> Gráficos .png: {plots_dir}")
    print(f" -> Tabelas (.csv / .md / .tex): {tables_dir}")
    print("=" * 70)


if __name__ == "__main__":
    main()
