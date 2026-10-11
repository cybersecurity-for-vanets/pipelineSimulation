import os
import glob
import subprocess
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# ==============================================================================
# 1. DIRETÓRIOS E CONFIGURAÇÕES
# ==============================================================================
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    
    # Exemplo: Config_highway_ASCON_128_high_flow
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
            
    protocol_found = clean  # Restante é o protocolo (ex: ASCON_128, Proposed_V2X)
    return top_found, protocol_found, flow_found

def process_metrics():
    print("[2/4] Processando e agrupando métricas por protocolo e cenário...")
    if not os.path.exists(OUTPUT_CSV):
        return None, None

    # Leitura com low_memory=False para evitar avisos de tipos mistos
    df = pd.read_csv(OUTPUT_CSV, low_memory=False)

    # Filtrar apenas escalares
    if 'type' in df.columns:
        df_scalars = df[df['type'] == 'scalar'].copy()
    else:
        df_scalars = df.copy()

    # Converter a coluna 'value' para numérico e descartar strings/hashes não numéricos
    df_scalars['value'] = pd.to_numeric(df_scalars['value'], errors='coerce')
    df_scalars = df_scalars.dropna(subset=['value'])

    # Identificar a coluna que contém o nome da configuração
    if 'config' in df_scalars.columns:
        config_series = df_scalars['config']
    elif 'configname' in df_scalars.columns:
        config_series = df_scalars['configname']
    elif 'attr:configname' in df_scalars.columns:
        config_series = df_scalars['attr:configname']
    elif 'run' in df_scalars.columns:
        config_series = df_scalars['run'].astype(str).apply(lambda x: x.split('-')[0])
    else:
        print(f"[ERRO] Nenhuma coluna de configuração encontrada. Colunas: {list(df_scalars.columns)}")
        return None, None

    parsed = config_series.apply(parse_config)
    df_scalars['Protocol'] = [p[0] for p in parsed]
    df_scalars['Scenario'] = [p[1] for p in parsed]

    # Agrupar métricas por protocolo, cenário e nome do escalar
    grouped = df_scalars.groupby(['Protocol', 'Scenario', 'name'])['value'].agg(
        Mean='mean',
        Std='std',
        Count='count'
    ).reset_index()

    # Cálculo do Intervalo de Confiança de 95%
    grouped['CI_95'] = 1.96 * (grouped['Std'] / np.sqrt(grouped['Count'].replace(0, np.nan)))

    grouped.to_csv(SUMMARY_CSV, index=False)
    print(f"[OK] Resumo estatístico salvo em: '{SUMMARY_CSV}'")
    return df_scalars, grouped

# ==============================================================================
# 4. GERAÇÃO DE GRÁFICOS COMPARATIVOS
# ==============================================================================
def generate_plots(df_scalars):
    print("[3/4] Gerando gráficos para a dissertação...")
    
    scenarios_order = ["low_flow", "medium_flow", "high_flow"]
    available_metrics = df_scalars['name'].unique()

    print(f"[INFO] Métricas encontradas no dataset: {list(available_metrics)}")

    if len(available_metrics) == 0:
        print("[AVISO] Nenhuma métrica escalar encontrada para plotagem.")
        return

    # Plota um gráfico separado para cada métrica disponível
    for metric_name in available_metrics:
        metric_data = df_scalars[df_scalars['name'] == metric_name]
        
        if metric_data.empty:
            continue

        # Sanitizar nome da métrica para nome do arquivo
        safe_filename = "".join([c if c.isalnum() else "_" for c in metric_name]).strip("_")

        fig, ax = plt.subplots(figsize=(7, 4.5))
        sns.barplot(
            data=metric_data,
            x="Scenario",
            y="value",
            hue="Protocol",
            order=[s for s in scenarios_order if s in metric_data['Scenario'].unique()],
            capsize=0.1,
            ax=ax,
            palette="Set2"
        )
        ax.set_title(f"Métrica: {metric_name}")
        ax.set_xlabel("Cenário de Densidade de Tráfego")
        ax.set_ylabel("Valor Médio")
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        plt.tight_layout()
        
        plt.savefig(os.path.join(PLOTS_DIR, f"{safe_filename}.png"))
        plt.savefig(os.path.join(PLOTS_DIR, f"{safe_filename}.pdf"))
        plt.close()
        print(f"[OK] Gráfico salvo: {safe_filename}.png/.pdf")

    print(f"[OK] Processamento de gráficos concluído em: '{PLOTS_DIR}'")

# ==============================================================================
# 5. EXECUÇÃO DA ETAPA 4
# ==============================================================================
if __name__ == "__main__":
    print("=== Iniciando Processamento e Análise da Etapa 4 ===")
    if export_omnet_results():
        df_raw, df_summary = process_metrics()
        if df_raw is not None:
            generate_plots(df_raw)
            print("\n=== Etapa 4 Concluída com Sucesso! ===")
            print(f"- Relatório Estatístico: {SUMMARY_CSV}")
            print(f"- Gráficos PDF/PNG: {PLOTS_DIR}")