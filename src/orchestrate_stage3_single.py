import os
import glob
import argparse
import concurrent.futures

import orchestrate_stage3 as stage3
from protocols import load_protocols

def select_protocol_configs(configs, topologies, protocol):
    """Mantém apenas as configurações do protocolo pedido (Config_<topologia>_<protocolo>_<fluxo>)."""
    selected = []
    for config_name in configs:
        top_id = stage3.resolve_topology_id(config_name, topologies)
        remainder = config_name.replace("Config_", "", 1)
        if remainder.startswith(f"{top_id}_{protocol}_"):
            selected.append(config_name)
    return selected

def find_latency_plugin():
    candidates = glob.glob(
        os.path.join(stage3.LATENCY_APP_DIR, "out", "**", "libLatencyPlugin.dylib"),
        recursive=True,
    )
    if not candidates:
        raise FileNotFoundError("LatencyPlugin.dylib not found; run without --skip-build")
    return candidates[0]

def main():
    parser = argparse.ArgumentParser(description="Etapa 3 restrita a um único protocolo.")
    parser.add_argument("protocol", choices=list(load_protocols()), help="Protocolo a simular (definido em conf/protocols.json)")
    parser.add_argument("--workers", type=int, default=6, help="Simulações em paralelo")
    parser.add_argument("--skip-build", action="store_true", help="Reutiliza o plugin já compilado")
    args = parser.parse_args()

    if args.skip_build:
        stage3.LATENCY_PLUGIN_LIB = find_latency_plugin()
    else:
        print("[BUILD] Compilando módulo de latência...")
        stage3.LATENCY_PLUGIN_LIB = stage3.build_latency_plugin()
    print(f"[BUILD] Plugin carregável: {stage3.LATENCY_PLUGIN_LIB}")

    topologies = stage3.load_known_topologies()
    all_configs, repeat_count = stage3.parse_omnetpp_ini()
    configs = select_protocol_configs(all_configs, topologies, args.protocol)

    if not configs:
        print(f"[ERRO] Nenhuma configuração {args.protocol} encontrada no omnetpp.ini.")
        return

    tasks = [
        (config_name, seed, stage3.resolve_topology_id(config_name, topologies))
        for config_name in configs
        for seed in range(repeat_count)
    ]

    print(f"=== [Etapa 3] Execução apenas do {args.protocol} ===")
    print(f"Configurações {args.protocol}: {len(configs)} de {len(all_configs)}")
    print(f"Sementes por configuração: {repeat_count}")
    print(f"Total de simulações agendadas: {len(tasks)}")

    launchd_proc, log_file = stage3.start_launchd()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(stage3.run_simulation, task) for task in tasks]
            for future in concurrent.futures.as_completed(futures):
                try:
                    print(future.result())
                except Exception as e:
                    print(f"[FAIL] Exceção na simulação: {e}")
    finally:
        print("[LAUNCHD] Encerrando o daemon veins_launchd...")
        launchd_proc.terminate()
        log_file.close()

if __name__ == "__main__":
    stage3.setup_storage()
    main()
