import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from .flow_levels import load_flow_levels, split_level, is_current_config
    from .generate_stage2_configs import MEASUREMENT_S
    from .level_style import add_level_columns, level_origin_handles, scatter_levels, sort_levels
    from .protocols import load_protocols
except ImportError:
    from flow_levels import load_flow_levels, split_level, is_current_config
    from generate_stage2_configs import MEASUREMENT_S
    from level_style import add_level_columns, level_origin_handles, scatter_levels, sort_levels
    from protocols import load_protocols

# ==============================================================================
# 1. DIRETÓRIOS E CONFIGURAÇÕES
# ==============================================================================
SSD_DIR = os.path.dirname("/Volumes/PSSD T7/simulationData/")
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(SSD_DIR):
    RESULTS_DIR = os.path.join(SSD_DIR, "results")
else:
    RESULTS_DIR = os.path.join(PROJECT_DIR, "results")
SCAVETOOL_CSV = os.path.join(RESULTS_DIR, "rawData", "scavetool_export.csv")
OUTPUT_CSV = os.path.join(RESULTS_DIR, "rawData", "flow_kpis_by_topology.csv")
OUTPUT_SEED_CSV = os.path.join(RESULTS_DIR, "rawData", "flow_kpis_per_seed.csv")
OUTPUT_DISTANCE_CSV = os.path.join(RESULTS_DIR, "rawData", "flow_kpis_by_distance.csv")
PLOTS_DIR = os.path.join(RESULTS_DIR, "plots", "flow_kpis")

TOPOLOGIES = ["highway", "urbanRoad", "intersection"]
FLOW_X = "Flow per lane (veh/h/lane)"
PROTOCOLS = list(load_protocols())
METRICS = ["PDR (%)", "CBR (%)", "Latency E2E (ms)"]

# Faixas de distância do PDR (ver LatencyTraCIDemo11p.h): 50 m cada; a última acumula tudo acima de 250 m.
PDR_DISTANCE_BIN_M = 50
PDR_DISTANCE_BINS = 6

# Escalares usados (ver LatencyTraCIDemo11p.cc). Latência E2E = packetLatencyTotalMs / packetLatencySamples,
# cada amostra é (instante de recepção - instante de criação) do CAM.
SCALARS = {
    "pdrExpectedReceptions", "pdrReceivedReceptions",
    "packetLatencyTotalMs", "packetLatencySamples",
    "channelBusy:timeavg",
    # Veículos ativos = soma de measurementTimeS / MEASUREMENT_S (média de veículos simultâneos na medição).
    "measurementTimeS",
} | {f"pdr{kind}_d{index}" for kind in ("Expected", "Received") for index in range(PDR_DISTANCE_BINS)}

# ==============================================================================
# 2. LEITURA EM STREAMING DO SCAVETOOL EXPORT
# ==============================================================================
def parse_config(config_name):
    # Configs fora de conf/flow_levels.json (rodadas antigas) ficam de fora, como na etapa 4.
    if not isinstance(config_name, str) or not is_current_config(config_name):
        return None

    # Ex.: Config_highway_FF1_AES_y2019h07 -> ("highway", "FF1_AES", "y2019h07"); sintético: ..._s050
    clean_name, flow = split_level(config_name.removeprefix("Config_"))
    if flow is None:
        return None
    topology = next((value for value in TOPOLOGIES if clean_name.startswith(value + "_")), None)
    if topology is None:
        return None

    protocol = clean_name[len(topology) + 1:]
    return topology, protocol, flow


def aggregate_export(chunk_rows=500_000):
    """Soma (sum, count) por (run, escalar). Cada linha do export é um nó, então agregamos por execução."""
    columns = ["run", "type", "module", "name", "attrname", "attrvalue", "value"]
    partial, configs, seeds = [], {}, {}
    reader = pd.read_csv(SCAVETOOL_CSV, usecols=columns, dtype=str, chunksize=chunk_rows)
    for index, chunk in enumerate(reader, start=1):
        runattr = chunk[chunk["type"] == "runattr"]
        configs.update(zip(runattr.loc[runattr["attrname"] == "configname", "run"],
                           runattr.loc[runattr["attrname"] == "configname", "attrvalue"]))
        seed_attr = runattr[runattr["attrname"] == "seedset"]
        seeds.update(zip(seed_attr["run"], seed_attr["attrvalue"]))

        scalars = chunk[(chunk["type"] == "scalar") & chunk["name"].isin(SCALARS)]
        scalars = scalars.assign(value=pd.to_numeric(scalars["value"], errors="coerce")).dropna(subset=["value"])
        if not scalars.empty:
            partial.append(scalars.groupby(["run", "name"])["value"].agg(["sum", "count"]))
        if index % 20 == 0:
            print(f"[INFO] {index * chunk_rows:,} linhas lidas...")

    if not partial:
        raise ValueError("Nenhum escalar relevante encontrado no scavetool export.")
    combined = pd.concat(partial).groupby(level=["run", "name"]).sum()
    return combined["sum"].unstack("name"), combined["count"].unstack("name"), configs, seeds

# ==============================================================================
# 3. KPIs POR EXECUÇÃO E AGREGAÇÃO (MÉDIA E IC 95%)
# ==============================================================================
def value_of(row, name):
    return row[name] if name in row.index and pd.notna(row[name]) else np.nan


def kpis_per_run(sums, counts, configs, seeds):
    rows = []
    for run, config in configs.items():
        parsed = parse_config(config)
        if parsed is None or run not in sums.index:
            continue
        topology, protocol, flow = parsed
        s, c = sums.loc[run], counts.loc[run]

        expected = value_of(s, "pdrExpectedReceptions")
        received = value_of(s, "pdrReceivedReceptions")
        latency_total = value_of(s, "packetLatencyTotalMs")
        latency_samples = value_of(s, "packetLatencySamples")
        cbr_sum = value_of(s, "channelBusy:timeavg")
        cbr_count = value_of(c, "channelBusy:timeavg")
        vehicle_seconds = value_of(s, "measurementTimeS")

        rows.append({
            "run": run,
            "Seed": int(seeds[run]) if run in seeds else np.nan,
            "Topology": topology,
            "Scenario": flow,
            "Protocol": protocol,
            # PDR agregado da execução: recepções esperadas e recebidas somadas sobre todos os nós.
            "PDR (%)": 100 * received / expected if expected > 0 else np.nan,
            # CBR da execução: média dos nós (cada nó reporta channelBusy:timeavg).
            "CBR (%)": 100 * cbr_sum / cbr_count if cbr_count > 0 else np.nan,
            "Latency E2E (ms)": latency_total / latency_samples if latency_samples > 0 else np.nan,
            "Active vehicles": vehicle_seconds / MEASUREMENT_S,
        })
    return pd.DataFrame(rows)


def distance_pdr_per_run(sums, configs):
    """PDR por faixa de distância em cada execução (recepções somadas sobre todos os nós)."""
    rows = []
    for run, config in configs.items():
        parsed = parse_config(config)
        if parsed is None or run not in sums.index:
            continue
        topology, protocol, flow = parsed
        for index in range(PDR_DISTANCE_BINS):
            expected = value_of(sums.loc[run], f"pdrExpected_d{index}")
            received = value_of(sums.loc[run], f"pdrReceived_d{index}")
            rows.append({
                "run": run, "Topology": topology, "Scenario": flow, "Protocol": protocol,
                "Distance bin": index,
                "Distance min (m)": index * PDR_DISTANCE_BIN_M,
                "PDR (%)": 100 * received / expected if expected > 0 else np.nan,
            })
    return pd.DataFrame(rows)


def summarize_distance(distance_runs):
    """Média e IC 95% entre sementes do PDR de cada faixa de distância."""
    keys = ["Topology", "Scenario", "Protocol", "Distance bin", "Distance min (m)"]
    summary = distance_runs.groupby(keys)["PDR (%)"].agg(["mean", "std", "count"]).reset_index()
    summary = summary.rename(columns={"mean": "PDR (%)", "count": "N"})
    summary["PDR (%) CI95"] = 1.96 * summary["std"].fillna(0) / np.sqrt(summary["N"])
    summary = ordered(summary.drop(columns="std"))
    return summary.sort_values(keys).reset_index(drop=True)


def flow_per_lane_by_scenario():
    """veh/h/faixa de cada (topologia, nível), lido de conf/flow_levels.json (gravado por generate_routes.py).

    Nível observado: volume CET do ano e hora do nível; nível sintético: fator × fluxo médio observado. Em ambos,
    somado sobre as rotas da topologia e dividido pelas faixas.
    """
    topologies = load_flow_levels()["topologies"]
    return {(topology, flow): values["veh_h_per_lane"]
            for topology, levels in topologies.items() for flow, values in levels.items()}


def with_flow_per_lane(frame, flow_per_lane):
    """Acrescenta veh/h/faixa, 'Level kind' (observed/synthetic) e 'Survey year' (NaN para sintético)."""
    frame = frame.copy()
    keys = zip(frame["Topology"].astype(str), frame["Scenario"].astype(str))
    frame["Flow per lane (veh/h/lane)"] = [flow_per_lane.get(key, np.nan) for key in keys]
    return add_level_columns(frame)


def ordered(frame):
    """Aplica a ordem de topologias, fluxos e protocolos (a mesma das listas do script)."""
    frame = frame.copy()
    frame["Topology"] = pd.Categorical(frame["Topology"], TOPOLOGIES, ordered=True)
    # Níveis em ordem de level_sort_key (observados por ano/hora, depois sintéticos); cada topologia tem a sua lista.
    frame["Scenario"] = pd.Categorical(frame["Scenario"], sort_levels(frame["Scenario"].astype(str)), ordered=True)
    frame["Protocol"] = pd.Categorical(frame["Protocol"], PROTOCOLS, ordered=True)
    return frame.sort_values(["Topology", "Scenario", "Protocol"]).reset_index(drop=True)


def summarize(run_kpis):
    grouped = run_kpis.groupby(["Topology", "Scenario", "Protocol"])[METRICS]
    summary = grouped.agg(["mean", "std", "count"])
    summary.columns = [f"{metric}|{stat}" for metric, stat in summary.columns]
    summary = summary.reset_index()

    out = summary[["Topology", "Scenario", "Protocol"]].copy()
    out["N"] = summary["PDR (%)|count"]
    for metric in METRICS:
        out[metric] = summary[f"{metric}|mean"]
        out[f"{metric} CI95"] = 1.96 * summary[f"{metric}|std"].fillna(0) / np.sqrt(summary[f"{metric}|count"])
    return ordered(out)

# ==============================================================================
# 4. GRÁFICO DE DISPERSÃO POR SEMENTE
# ==============================================================================
def plot_seed_scatter(run_kpis):
    """Um gráfico por topologia: x = volume do nível (veh/h/faixa), cada ponto é uma semente; preto = média.

    Nível sintético = marcador vazado.
    """
    os.makedirs(PLOTS_DIR, exist_ok=True)
    colors = plt.get_cmap("tab10")

    for topology in TOPOLOGIES:
        data = run_kpis[(run_kpis["Topology"] == topology) & run_kpis[FLOW_X].notna()]
        if data.empty:
            continue
        # Deslocamento horizontal entre protocolos: fração da faixa de volumes da topologia.
        flows = data[FLOW_X].astype(float)
        spread = 0.02 * max(flows.max() - flows.min(), flows.max() * 0.1) / max(len(PROTOCOLS), 1)

        figure, axes = plt.subplots(1, len(METRICS), figsize=(5 * len(METRICS), 4.5))
        protocol_handles = []
        for axis, metric in zip(axes, METRICS):
            for offset, protocol in enumerate(PROTOCOLS):
                group = data[data["Protocol"] == protocol]
                if group.empty:
                    continue
                shift = (offset - (len(PROTOCOLS) - 1) / 2) * spread
                color = colors(offset % colors.N)
                scatter_levels(axis, group[FLOW_X] + shift, group[metric], group["Scenario"].astype(str),
                               color=color)
                if axis is axes[0]:
                    protocol_handles.append(plt.Line2D([], [], marker="o", linestyle="", color=color, label=protocol))

                means = group.groupby(FLOW_X)[metric].mean()
                axis.scatter(means.index + shift, means.values, marker="_", s=400, linewidths=2, color="black",
                             zorder=3)

            axis.set_xlabel("Volume por faixa (veh/h/faixa)")
            axis.set_title(metric)
            axis.grid(True, linestyle=":", alpha=0.6)

        handles = protocol_handles + level_origin_handles(with_line=False)
        figure.legend(handles=handles, fontsize=8, loc="lower center", ncol=len(handles), bbox_to_anchor=(0.5, 0.0))
        figure.suptitle(f"{topology}: valores por semente (marca preta = média)")
        figure.tight_layout(rect=(0, 0.07, 1, 1))
        figure.savefig(os.path.join(PLOTS_DIR, f"scatter_{topology}.png"), dpi=200)
        plt.close(figure)
        print(f"[OK] Gráfico salvo em: '{os.path.join(PLOTS_DIR, f'scatter_{topology}.png')}'")

# ==============================================================================
# 5. EXECUÇÃO
# ==============================================================================
def main():
    if not os.path.isfile(SCAVETOOL_CSV):
        raise FileNotFoundError(f"Não encontrei {SCAVETOOL_CSV}. Execute analyze_stage4_results.py primeiro.")

    print("[1/4] Lendo o scavetool export...")
    sums, counts, configs, seeds = aggregate_export()

    print("[2/4] Calculando PDR, CBR e latência E2E por execução...")
    run_kpis = kpis_per_run(sums, counts, configs, seeds)
    if run_kpis.empty:
        raise ValueError("Nenhuma execução reconhecida: verifique os nomes de configuração no export.")
    without_pdr = run_kpis["PDR (%)"].isna().sum()
    if without_pdr:
        print(f"[AVISO] {without_pdr} execuções sem recepções PDR; elas ficam de fora da média de PDR.")

    print("[3/4] Agregando por topologia, fluxo e protocolo (média e IC 95%)...")
    flow_per_lane = flow_per_lane_by_scenario()
    run_kpis = with_flow_per_lane(run_kpis, flow_per_lane)
    summary = with_flow_per_lane(summarize(run_kpis), flow_per_lane)
    summary.to_csv(OUTPUT_CSV, index=False, float_format="%.4f")

    seed_rows = ordered(run_kpis).sort_values(["Topology", "Scenario", "Protocol", "Seed"]).reset_index(drop=True)
    seed_rows.to_csv(OUTPUT_SEED_CSV, index=False, float_format="%.4f")
    print(f"[OK] Valores por semente salvos em: '{OUTPUT_SEED_CSV}'")

    distance_summary = with_flow_per_lane(summarize_distance(distance_pdr_per_run(sums, configs)), flow_per_lane)
    distance_summary.to_csv(OUTPUT_DISTANCE_CSV, index=False, float_format="%.4f")
    print(f"[OK] PDR por distância salvo em: '{OUTPUT_DISTANCE_CSV}'")

    print("[4/4] Gerando gráficos de dispersão por semente...")
    plot_seed_scatter(run_kpis)

    for topology in TOPOLOGIES:
        block = summary[summary["Topology"] == topology].drop(columns="Topology")
        print(f"\n=== {topology} ===")
        print(block.to_string(index=False, float_format=lambda value: f"{value:.3f}"))

    print(f"\n[OK] Resumo salvo em: '{OUTPUT_CSV}'")


if __name__ == "__main__":
    main()
