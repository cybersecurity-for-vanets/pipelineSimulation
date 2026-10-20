import os
import glob
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
def generate_plots(summary):
    print("[3/4] Gerando gráficos para a dissertação...")
    
    scenarios_order = ["low_flow", "medium_flow", "high_flow"]
    available_metrics = summary['name'].unique()

    print(f"[INFO] Métricas encontradas no dataset: {list(available_metrics)}")

    if len(available_metrics) == 0:
        print("[AVISO] Nenhuma métrica escalar encontrada para plotagem.")
        return

    # Keep each topology separate so its results remain comparable.
    for metric_name in available_metrics:
        metric_data = summary[summary['name'] == metric_name]
        
        if metric_data.empty:
            continue

        topologies = sorted(metric_data['Topology'].unique())
        safe_filename = "".join([c if c.isalnum() else "_" for c in metric_name]).strip("_")
        protocol_order = list(load_protocols())
        protocols = sorted(
            metric_data['Protocol'].unique(),
            key=lambda name: (protocol_order.index(name) if name in protocol_order else len(protocol_order), name),
        )
        grouped = metric_data.set_index(['Topology', 'Scenario', 'Protocol'])
        fig, axes = plt.subplots(
            1,
            len(topologies),
            figsize=(6 * len(topologies), 4.5),
            sharey=True,
            squeeze=False
        )
        legend_handles = []
        legend_labels = []
        colors = plt.get_cmap("Set2")

        for index, (topology, ax) in enumerate(zip(topologies, axes[0])):
            topology_data = metric_data[metric_data['Topology'] == topology]
            flows = [flow for flow in scenarios_order if flow in topology_data['Scenario'].unique()]
            if not flows:
                continue

            positions = np.arange(len(flows))
            bar_width = 0.8 / max(len(protocols), 1)
            for protocol_index, protocol in enumerate(protocols):
                means = []
                confidence_intervals = []
                for flow in flows:
                    key = (topology, flow, protocol)
                    if key in grouped.index:
                        stats = grouped.loc[key]
                        means.append(stats['Mean'])
                        std = stats['Std'] if pd.notna(stats['Std']) else 0
                        confidence_intervals.append(1.96 * std / np.sqrt(stats['Count']))
                    else:
                        means.append(np.nan)
                        confidence_intervals.append(0)

                offset = (protocol_index - (len(protocols) - 1) / 2) * bar_width
                bars = ax.bar(
                    positions + offset,
                    means,
                    width=bar_width,
                    yerr=confidence_intervals,
                    capsize=2,
                    label=protocol,
                    color=colors(protocol_index % colors.N)
                )
                if index == 0:
                    legend_handles.append(bars[0])
                    legend_labels.append(protocol)

            ax.set_xticks(positions, flows)
            ax.set_title(topology)
            ax.set_xlabel("Cenário de Densidade de Tráfego")
            ax.grid(axis='y', linestyle='--', alpha=0.7)

        axes[0][0].set_ylabel("Valor Médio")
        if legend_handles:
            fig.legend(
                legend_handles,
                legend_labels,
                title="Protocol",
                loc="upper center",
                bbox_to_anchor=(0.5, 0.94),
                ncol=len(legend_labels)
            )
        fig.suptitle(f"Métrica: {metric_name}", y=0.99)
        fig.tight_layout(rect=(0, 0, 1, 0.84))
        fig.savefig(os.path.join(PLOTS_DIR, f"{safe_filename}.png"))
        # fig.savefig(os.path.join(PLOTS_DIR, f"{safe_filename}.pdf"))
        plt.close(fig)
        print(f"[OK] Gráfico salvo: {safe_filename}.png/.pdf")

    print(f"[OK] Processamento de gráficos concluído em: '{PLOTS_DIR}'")

# ==============================================================================
# 5. EXECUÇÃO DA ETAPA 4
# ==============================================================================
if __name__ == "__main__":
    print("=== Iniciando Processamento e Análise da Etapa 4 ===")
    # if export_omnet_results():
    df_raw, df_summary = process_metrics()
    if df_raw is not None:
        generate_plots(df_summary)
        print("\n=== Etapa 4 Concluída com Sucesso! ===")
        print(f"- Relatório Estatístico: {SUMMARY_CSV}")
        print(f"- Gráficos PDF/PNG: {PLOTS_DIR}")