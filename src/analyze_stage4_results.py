import os
import glob
import argparse
import subprocess
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

try:
    from .flow_levels import current_result_files, flow_per_lane, is_current_config, level_label, level_los, split_level
    from .level_style import (add_level_columns, level_origin_handles, plot_level_series, safe_level_sort_key)
    from .protocols import load_protocols
except ImportError:
    from flow_levels import current_result_files, flow_per_lane, is_current_config, level_label, level_los, split_level
    from level_style import (add_level_columns, level_origin_handles, plot_level_series, safe_level_sort_key)
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

TOPOLOGIES = ["highway", "urbanRoad", "intersection"]
FLOW_X = "Flow per lane (veh/h/lane)"

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
    sca_files, ignored = current_result_files(glob.glob(os.path.join(RESULTS_DIR, "*.sca")))
    if ignored:
        print(f"[AVISO] Ignorando {len(ignored)} arquivo(s) .sca de configs que não estão em conf/flow_levels.json "
              f"(rodadas antigas), ex.: {os.path.basename(ignored[0])}")

    if not sca_files:
        print(f"[ERRO] Nenhum arquivo .sca dos níveis atuais em '{RESULTS_DIR}'. Execute a Etapa 3 primeiro.")
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
    
    # Exemplo: Config_highway_FF1_AES_y2019h07 ou Config_urbanRoad_baseline_s050
    # (o protocolo pode ter "_", então o nível sai pelo sufixo)
    clean, level = split_level(config_str.removeprefix("Config_"))

    top_found = "Unknown"
    for t in TOPOLOGIES:
        if clean.startswith(t + "_"):
            top_found = t
            clean = clean[len(t)+1:]
            break

    protocol_found = clean  # Restante é o protocolo (ex: baseline, FF1_AES)
    return top_found, protocol_found, level or "Unknown"


def scenario_flow_per_lane(topology, level):
    """veh/h/faixa do nível na topologia (conf/flow_levels.json); NaN se o par não estiver lá."""
    try:
        return flow_per_lane(topology, level)
    except KeyError:
        return np.nan

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
    # Proteção extra: um scavetool_export.csv antigo pode ter runs de níveis que não existem mais.
    current = config_series.map(lambda name: isinstance(name, str) and is_current_config(name))
    if not current.all():
        stale = sorted(config_series[~current].dropna().unique())
        print(f"[AVISO] Ignorando {len(stale)} config(s) fora de conf/flow_levels.json, ex.: {stale[:3]}")
        per_run, config_series = per_run[current.values].reset_index(drop=True), config_series[current].reset_index(drop=True)
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
    flows = {key: scenario_flow_per_lane(*key)
             for key in grouped[['Topology', 'Scenario']].drop_duplicates().itertuples(index=False)}
    grouped[FLOW_X] = [flows[key] for key in zip(grouped['Topology'], grouped['Scenario'])]
    grouped = add_level_columns(grouped)
    grouped = grouped.sort_values(
        ['Topology', 'Protocol', 'Scenario', 'name'],
        key=lambda column: column.map(safe_level_sort_key) if column.name == 'Scenario' else column,
    ).reset_index(drop=True)

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

def present_topologies(values):
    known = [topology for topology in TOPOLOGIES if topology in set(values)]
    return known + sorted(set(values) - set(known))


def generate_plots(summary):
    """Uma figura por métrica: um painel por topologia, x = veh/h/faixa do nível, uma linha por protocolo.

    Substitui as barras com uma categoria por nível (ilegíveis com ~28 níveis). Nível sintético = marcador vazado
    e segmento esmaecido.
    """
    print("[3/4] Gerando gráficos para a dissertação...")

    # procBin_* are histogram bins, plotted by plot_processing_histograms instead.
    is_bin = summary['name'].str.startswith(PROCESSING_BIN_PREFIX)
    available_metrics = summary.loc[~is_bin, 'name'].unique()

    print(f"[INFO] Métricas encontradas no dataset: {list(available_metrics)}")

    if len(available_metrics) == 0:
        print("[AVISO] Nenhuma métrica escalar encontrada para plotagem.")
        return

    colors = plt.get_cmap("Set2")
    protocols_all = sort_protocols(summary['Protocol'].unique())
    for metric_name in available_metrics:
        metric_data = summary[(summary['name'] == metric_name) & summary[FLOW_X].notna()]
        if metric_data.empty:
            continue

        topologies = present_topologies(metric_data['Topology'].unique())
        safe_filename = "".join([c if c.isalnum() else "_" for c in metric_name]).strip("_")
        fig, axes = plt.subplots(1, len(topologies), figsize=(4.2 * len(topologies), 3.8), squeeze=False)
        handles = {}
        for ax, topology in zip(axes[0], topologies):
            cell = metric_data[metric_data['Topology'] == topology]
            for protocol in protocols_all:
                group = cell[cell['Protocol'] == protocol]
                if group.empty:
                    continue
                color = colors(protocols_all.index(protocol) % colors.N)
                plot_level_series(ax, group[FLOW_X], group['Mean'], group['Scenario'], color=color,
                                  yerr=group['CI_95'], markersize=4)
                handles.setdefault(protocol, plt.Line2D([], [], color=color, marker="o", label=protocol))
            ax.set_title(topology)
            ax.set_xlabel("Volume por faixa (veh/h/faixa)")
            ax.grid(linestyle='--', alpha=0.5)
        axes[0][0].set_ylabel("Valor médio (barra = IC 95%)")
        legend_handles = list(handles.values()) + level_origin_handles()
        fig.legend(handles=legend_handles, loc="lower center", ncol=len(legend_handles), fontsize=8,
                   bbox_to_anchor=(0.5, 0.0))
        fig.suptitle(f"Métrica: {metric_name}", y=0.99)
        fig.tight_layout(rect=(0, 0.08, 1, 1))
        fig.savefig(os.path.join(PLOTS_DIR, f"{safe_filename}.png"))
        plt.close(fig)
        print(f"[OK] Gráfico salvo: {safe_filename}.png")

    print(f"[OK] Processamento de gráficos concluído em: '{PLOTS_DIR}'")


def representative_levels(bins, topology):
    """Menor, mediano e maior volume por faixa entre os níveis da topologia presentes no histograma."""
    flows = (bins[bins['Topology'] == topology][['Scenario', FLOW_X]]
             .drop_duplicates('Scenario').dropna(subset=[FLOW_X]).sort_values(FLOW_X))
    if flows.empty:
        return []
    picks = [0, (len(flows) - 1) // 2, len(flows) - 1]
    return [(flows['Scenario'].iloc[index], flows[FLOW_X].iloc[index]) for index in picks]


def plot_processing_histograms(summary):
    """Histograma da latência de processamento: colunas = topologias, linhas = menor, mediano e maior volume.

    Com ~28 níveis por topologia, uma linha por nível ficaria ilegível; os três níveis representativos são
    escolhidos por veh/h/faixa dentro de cada topologia (o título do painel diz qual nível é).
    """
    print("[3/4] Gerando histogramas de latência de processamento...")

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

    topologies = [t for t in present_topologies(bins['Topology'].unique()) if representative_levels(bins, t)]
    if not topologies:
        print("[AVISO] Nenhum fluxo conhecido encontrado para histograma.")
        return
    row_names = ["Menor volume", "Volume mediano", "Maior volume"]

    fig, axes = plt.subplots(
        len(row_names),
        len(topologies),
        figsize=(4.5 * len(topologies), 3.2 * len(row_names)),
        sharex=True,
        sharey=True,
        squeeze=False
    )
    handles = {}

    for col, topology in enumerate(topologies):
        for row, (flow, per_lane) in enumerate(representative_levels(bins, topology)):
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
            try:
                los = level_los(topology, flow)
            except (KeyError, FileNotFoundError):
                los = None
            los_text = f"LOS {los}, " if los else ""
            ax.set_title(f"{topology}: {los_text}{level_label(flow)}\n{per_lane:.0f} veh/h/faixa", fontsize=9)
            if col == 0:
                ax.set_ylabel(row_names[row])
            if row == len(row_names) - 1:
                ax.set_xlabel("Latência de processamento (ms)")

    fig.legend(
        list(handles.values()),
        list(handles.keys()),
        title="Protocol",
        loc="upper center",
        bbox_to_anchor=(0.5, 0.965),
        ncol=len(handles)
    )
    fig.suptitle("Mensagens por execução (média, escala log): menor, mediano e maior volume por topologia", y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
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