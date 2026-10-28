import os
import glob
import argparse
import subprocess
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

try:
    from .protocols import load_protocols
except ImportError:
    from protocols import load_protocols

# ==============================================================================
# 1. DIRETÓRIOS E CONFIGURAÇÕES
# ==============================================================================
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SSD_DIR = os.path.dirname("/Volumes/PSSD T7/simulationData/")
if os.path.isdir(SSD_DIR):
    RESULTS_DIR = os.path.join(SSD_DIR, "results", "rawData")
else:
    RESULTS_DIR = os.path.join(PROJECT_DIR, "results", "rawData")
OUTPUT_CSV = os.path.join(RESULTS_DIR, "scavetool_export.csv")
SUMMARY_CSV = os.path.join(RESULTS_DIR, "final_metrics_summary.csv")
PLOTS_DIR = os.path.join(RESULTS_DIR, "../plots")

# Histogram of processing latency: procBin_k is the count in bin k (see LatencyTraCIDemo11p.h)
PROCESSING_BIN_PREFIX = "procBin_"
PROCESSING_BINS = 200
PROCESSING_BIN_MS = 0.01

SCAVETOOL_BIN = "/Users/fabiotorres/omnetpp-6.2.0/bin/opp_scavetool"

os.makedirs(PLOTS_DIR, exist_ok=True)

# Set de estilo acadêmico para gráficos (IEEE / Springer)
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
    "figure.dpi": 300
})

# ==============================================================================
# 2. EXPORTAÇÃO DOS DADOS DO OMNeT++ VIA SCAVETOOL
# ==============================================================================
def export_omnet_results():
    print("[1/4] Extraindo métricas dos arquivos .sca/.vec via scavetool...")
    sca_files = glob.glob(os.path.join(RESULTS_DIR, "*.sca"))
    
    if not sca_files:
        print(f"[ERRO] Nenhum arquivo .sca encontrado em '{RESULTS_DIR}'. Execute a Etapa 3 primeiro.")
        return False

    cmd = [
        SCAVETOOL_BIN, "x",
        *sca_files,
        "-o", OUTPUT_CSV,
        "-F", "CSV-R"
    ]
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[ERRO] Falha na execução do scavetool:\n{res.stderr}")
        return False
        
    print(f"[OK] Dados consolidados salvos em: '{OUTPUT_CSV}'")
    return True

# ==============================================================================
# 3. PROCESSAMENTO E ESTATÍSTICA (MÉDIA E IC 95%)
# ==============================================================================
def parse_config(config_str):
    if not isinstance(config_str, str):
        return "Unknown", "Unknown", "Unknown"
    
    # Exemplo: Config_highway_FF1_AES_high_flow
    clean = config_str.replace("Config_", "")
    
    topologies = ["highway", "urbanRoad", "intersection"]
    flows = ["low_flow", "medium_flow", "high_flow"]
    
    top_found = "Unknown"
    for t in topologies:
        if clean.startswith(t):
            top_found = t
            clean = clean[len(t)+1:]
            break
            
    flow_found = "Unknown"
    for f in flows:
        if clean.endswith(f):
            flow_found = f
            clean = clean[:-len(f)-1]
            break
            
    protocol_found = clean  # Restante é o protocolo (ex: baseline, FF1_AES)
    return top_found, protocol_found, flow_found

def aggregate_export(chunk_rows=500_000):
    """Stream the (possibly tens of GB) scavetool CSV keeping only per-(run, scalar) sum, sum of squares and count."""
    columns = ["run", "type", "name", "attrname", "attrvalue", "value"]
    missing = set(columns) - set(pd.read_csv(OUTPUT_CSV, nrows=0).columns)
    if missing:
        raise ValueError(f"Colunas ausentes no scavetool export: {', '.join(sorted(missing))}")

    partial, configs = [], {}
    reader = pd.read_csv(OUTPUT_CSV, usecols=columns, dtype=str, chunksize=chunk_rows)
    for index, chunk in enumerate(reader, start=1):
        runattr = chunk[(chunk["type"] == "runattr") & (chunk["attrname"] == "configname")]
        configs.update(zip(runattr["run"], runattr["attrvalue"]))

        scalars = chunk.loc[chunk["type"] == "scalar", ["run", "name", "value"]]
        scalars = scalars.assign(value=pd.to_numeric(scalars["value"], errors="coerce")).dropna(subset=["value"])
        if not scalars.empty:
            scalars = scalars.assign(square=scalars["value"] ** 2)
            partial.append(scalars.groupby(["run", "name"]).agg(
                total=("value", "sum"), squares=("square", "sum"), count=("value", "count")))
        if index % 20 == 0:
            print(f"[INFO] {index * chunk_rows:,} linhas lidas...")

    if not partial:
        return None
    return pd.concat(partial).groupby(level=["run", "name"]).sum(), pd.Series(configs, dtype="object")


def process_metrics():
    print("[2/4] Processando e agrupando métricas por protocolo e cenário...")
    if not os.path.exists(OUTPUT_CSV):
        return None, None

    aggregated = aggregate_export()
    if aggregated is None:
        print("[ERRO] Nenhum escalar encontrado no export.")
        return None, None
    per_run, run_config_names = aggregated

    per_run = per_run.reset_index()
    config_series = per_run["run"].map(run_config_names)
    config_series = config_series.fillna(per_run["run"].astype(str).str.split("-").str[0])
    parsed = config_series.apply(parse_config)
    per_run["Topology"] = [p[0] for p in parsed]
    per_run["Protocol"] = [p[1] for p in parsed]
    per_run["Scenario"] = [p[2] for p in parsed]

    # Preserve topology so statistics from different networks are not mixed.
    totals = per_run.groupby(["Topology", "Protocol", "Scenario", "name"])[["total", "squares", "count"]].sum()
    grouped = pd.DataFrame({"Mean": totals["total"] / totals["count"], "Count": totals["count"]})
    variance = (totals["squares"] - totals["total"] ** 2 / totals["count"]) / (totals["count"] - 1)
    grouped["Std"] = np.sqrt(variance.clip(lower=0).where(totals["count"] > 1))
    grouped = grouped[["Mean", "Std", "Count"]].reset_index()

    # Cálculo do Intervalo de Confiança de 95%
    grouped['CI_95'] = 1.96 * (grouped['Std'] / np.sqrt(grouped['Count'].replace(0, np.nan)))

    grouped.to_csv(SUMMARY_CSV, index=False)
    print(f"[OK] Resumo estatístico salvo em: '{SUMMARY_CSV}'")
    return grouped, grouped

# ==============================================================================
# 4. GERAÇÃO DE GRÁFICOS COMPARATIVOS
# ==============================================================================
def sort_protocols(values):
    protocol_order = list(load_protocols())
    return sorted(
        values,
        key=lambda name: (protocol_order.index(name) if name in protocol_order else len(protocol_order), name),
    )

def generate_plots(summary):
    print("[3/4] Gerando gráficos para a dissertação...")

    scenarios_order = ["low_flow", "medium_flow", "high_flow"]
    # procBin_* are histogram bins, plotted by plot_processing_histograms instead.
    is_bin = summary['name'].str.startswith(PROCESSING_BIN_PREFIX)
    available_metrics = summary.loc[~is_bin, 'name'].unique()

    print(f"[INFO] Métricas encontradas no dataset: {list(available_metrics)}")

    if len(available_metrics) == 0:
        print("[AVISO] Nenhuma métrica escalar encontrada para plotagem.")
        return

    # One axis per metric: x groups are topologies, each group holds its flows, bars are protocols.
    colors = plt.get_cmap("Set2")
    for metric_name in available_metrics:
        metric_data = summary[summary['name'] == metric_name]

        if metric_data.empty:
            continue

        topologies = sorted(metric_data['Topology'].unique())
        flows = scenarios_order
        safe_filename = "".join([c if c.isalnum() else "_" for c in metric_name]).strip("_")
        protocols = sort_protocols(metric_data['Protocol'].unique())
        grouped = metric_data.set_index(['Topology', 'Scenario', 'Protocol'])
        bar_width = 0.8 / max(len(protocols), 1)
        fig, ax = plt.subplots(figsize=(1.6 * len(topologies) * (len(flows) + 1), 4.5))
        handles = {}
        tick_positions, tick_labels, group_centers = [], [], []

        for t_index, topology in enumerate(topologies):
            base = t_index * (len(flows) + 1)
            for f_index, flow in enumerate(flows):
                position = base + f_index
                tick_positions.append(position)
                tick_labels.append(flow.replace("_flow", ""))
                for protocol_index, protocol in enumerate(protocols):
                    key = (topology, flow, protocol)
                    if key in grouped.index:
                        stats = grouped.loc[key]
                        mean = stats['Mean']
                        std = stats['Std'] if pd.notna(stats['Std']) else 0
                        confidence_interval = 1.96 * std / np.sqrt(stats['Count'])
                    else:
                        mean = np.nan
                        confidence_interval = 0

                    offset = (protocol_index - (len(protocols) - 1) / 2) * bar_width
                    bars = ax.bar(
                        position + offset,
                        mean,
                        width=bar_width,
                        yerr=confidence_interval,
                        capsize=2,
                        label=protocol,
                        color=colors(protocol_index % colors.N)
                    )
                    handles.setdefault(protocol, bars[0])
            group_centers.append(base + (len(flows) - 1) / 2)

        ax.set_xticks(tick_positions, tick_labels)
        for center, topology in zip(group_centers, topologies):
            ax.annotate(
                topology,
                xy=(center, 0),
                xycoords=("data", "axes fraction"),
                xytext=(0, -38),
                textcoords="offset points",
                ha="center",
                va="top",
                fontweight="bold"
            )
        ax.set_ylabel("Valor Médio")
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        ax.legend(list(handles.values()), list(handles.keys()), title="Protocol", loc="upper right")
        fig.suptitle(f"Métrica: {metric_name}", y=0.99)
        fig.tight_layout()
        fig.savefig(os.path.join(PLOTS_DIR, f"{safe_filename}.png"))
        # fig.savefig(os.path.join(PLOTS_DIR, f"{safe_filename}.pdf"))
        plt.close(fig)
        print(f"[OK] Gráfico salvo: {safe_filename}.png/.pdf")

    print(f"[OK] Processamento de gráficos concluído em: '{PLOTS_DIR}'")

def plot_processing_histograms(summary):
    """One figure with a histogram of processing latency per topology and flow, one curve per protocol."""
    print("[3/4] Gerando histogramas de latência de processamento...")

    scenarios_order = ["low_flow", "medium_flow", "high_flow"]
    bins = summary[summary['name'].str.startswith(PROCESSING_BIN_PREFIX)].copy()
    if bins.empty:
        print("[AVISO] Nenhum procBin_* encontrado para histograma.")
        return

    bins['bin'] = bins['name'].str[len(PROCESSING_BIN_PREFIX):].astype(int)
    protocols = sort_protocols(bins['Protocol'].unique())

    # Stop the x-axis just after the last bin that holds any messages.
    nonzero = bins.loc[bins['Mean'] > 0, 'bin']
    last_bin = min(int(nonzero.max()) + 2, PROCESSING_BINS) if not nonzero.empty else 0
    edges = np.arange(last_bin + 2) * PROCESSING_BIN_MS
    colors = plt.get_cmap("Set2")

    # One figure: columns are topologies (grouped on the x direction), rows are flows.
    topologies = sorted(bins['Topology'].unique())
    flows = [flow for flow in scenarios_order if flow in bins['Scenario'].unique()]
    if not flows:
        print("[AVISO] Nenhum fluxo conhecido encontrado para histograma.")
        return

    fig, axes = plt.subplots(
        len(flows),
        len(topologies),
        figsize=(4.5 * len(topologies), 3.2 * len(flows)),
        sharex=True,
        sharey=True,
        squeeze=False
    )
    handles = {}

    for row, flow in enumerate(flows):
        for col, topology in enumerate(topologies):
            ax = axes[row][col]
            cell = bins[(bins['Topology'] == topology) & (bins['Scenario'] == flow)]
            for protocol_index, protocol in enumerate(protocols):
                counts = (
                    cell[cell['Protocol'] == protocol]
                    .set_index('bin')['Mean']
                    .reindex(range(last_bin + 1), fill_value=0)
                )
                stairs = ax.stairs(
                    counts.values,
                    edges,
                    fill=True,
                    alpha=0.35,
                    linewidth=1.2,
                    color=colors(protocol_index % colors.N),
                    label=protocol,
                )
                handles.setdefault(protocol, stairs)

            # Log scale: the peak bin dwarfs the tails, which would otherwise disappear.
            ax.set_yscale("log")
            ax.grid(axis='y', linestyle='--', alpha=0.7)
            if row == 0:
                ax.set_title(topology)
            if col == 0:
                ax.set_ylabel(flow)
            if row == len(flows) - 1:
                ax.set_xlabel("Latência de processamento (ms)")

    fig.legend(
        list(handles.values()),
        list(handles.keys()),
        title="Protocol",
        loc="upper center",
        bbox_to_anchor=(0.5, 0.94),
        ncol=len(handles)
    )
    fig.suptitle("Mensagens por execução (média, escala log) por topologia e fluxo", y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    filename = "processing_histogram.png"
    fig.savefig(os.path.join(PLOTS_DIR, filename))
    plt.close(fig)
    print(f"[OK] Gráfico salvo: {filename}")

    print(f"[OK] Histogramas concluídos em: '{PLOTS_DIR}'")

# ==============================================================================
# 5. EXECUÇÃO DA ETAPA 4
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Etapa 4")
    parser.add_argument("--skip-csv", action="store_true", help="Reutiliza o CSV existente")
    args = parser.parse_args()

    print("=== Iniciando Processamento e Análise da Etapa 4 ===")
    if not args.skip_csv:
        export_omnet_results()

    df_raw, df_summary = process_metrics()
    if df_raw is not None:
        generate_plots(df_summary)
        plot_processing_histograms(df_summary)
        print("\n=== Etapa 4 Concluída com Sucesso! ===")
        print(f"- Relatório Estatístico: {SUMMARY_CSV}")
        print(f"- Gráficos PDF/PNG: {PLOTS_DIR}")