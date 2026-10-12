import os
import sys
import re
import glob
import subprocess
import concurrent.futures
import json
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_DIR = os.path.join(BASE_DIR, "conf")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
RESULTS_DIR = os.path.join(BASE_DIR, "results", "rawData")
INI_FILE = os.path.join(CONF_DIR, "omnetpp.ini")
SCENARIOS_CONF_PATH = os.path.join(CONF_DIR, "coordinates_scenaries.json")

OPP_RUN = "/Users/fabiotorres/omnetpp-6.2.0/bin/opp_run"
OPP_MAKEMAKE = "/Users/fabiotorres/omnetpp-6.2.0/bin/opp_makemake"
VEINS_LIB = "/Users/fabiotorres/veins-veins-5.3.1/src/veins"
VEINS_SRC = "/Users/fabiotorres/veins-veins-5.3.1/src"
VEINS_LAUNCHD = "/Users/fabiotorres/veins-veins-5.3.1/bin/veins_launchd"
NED_PATH = f"/Users/fabiotorres/veins-veins-5.3.1/ned:{CONF_DIR}"
LATENCY_APP_DIR = os.path.join(BASE_DIR, "src", "latency")
LATENCY_PLUGIN_LIB = None

os.makedirs(RESULTS_DIR, exist_ok=True)

def load_known_topologies():
    """Carrega dinamicamente as chaves de topologia cadastradas no JSON."""
    if os.path.exists(SCENARIOS_CONF_PATH):
        with open(SCENARIOS_CONF_PATH, "r", encoding="utf-8") as f:
            return list(json.load(f).keys())
    return ["highway", "urbanRoad", "intersection"]

def parse_omnetpp_ini():
    """
    Extrai adaptativamente todas as seções [Config Config_...] e a 
    quantidade de repetições (repeat) declaradas no omnetpp.ini.
    """
    if not os.path.exists(INI_FILE):
        print(f"[ERRO] {INI_FILE} não encontrado. Execute generate_stage2_configs.py primeiro.")
        sys.exit(1)

    with open(INI_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Busca a quantidade de sementes/repetições (ex: repeat = 30)
    repeat_match = re.search(r"^\s*repeat\s*=\s*(\d+)", content, re.MULTILINE)
    repeat_count = int(repeat_match.group(1)) if repeat_match else 30

    # Extrai os IDs das configurações ativas no .ini
    config_matches = re.findall(r"^\s*\[Config\s+(Config_[^\]]+)\]", content, re.MULTILINE)
    configs = sorted(list(set(config_matches)))

    return configs, repeat_count

def resolve_topology_id(config_name, known_topologies):
    """Mapeia adaptativamente o ID da topologia para direcionamento correto dos logs."""
    clean_name = config_name.replace("Config_", "")
    for top_id in known_topologies:
        if clean_name.startswith(top_id):
            return top_id
    return "general"

def build_latency_plugin():
    subprocess.run(
        [
            OPP_MAKEMAKE,
            "--make-so",
            "-f",
            "-o",
            "LatencyPlugin",
            "-I",
            VEINS_SRC,
            "-L",
            VEINS_SRC,
            "-l",
            "veins",
        ],
        cwd=LATENCY_APP_DIR,
        check=True,
    )
    subprocess.run(["make", "MODE=release"], cwd=LATENCY_APP_DIR, check=True)

    plugin_candidates = glob.glob(
        os.path.join(LATENCY_APP_DIR, "out", "**", "libLatencyPlugin.dylib"),
        recursive=True,
    )
    if not plugin_candidates:
        raise FileNotFoundError("LatencyPlugin.dylib was not produced by the build")
    return plugin_candidates[0]

def start_launchd():
    """Inicia o daemon veins_launchd na porta 9999 registrando os logs."""
    print("[LAUNCHD] Subindo o daemon veins_launchd na porta 9999...")
    launchd_log_path = os.path.join(LOGS_DIR, "veins_launchd.log")
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_file = open(launchd_log_path, "a", encoding="utf-8")
    cmd = [sys.executable, VEINS_LAUNCHD, "-v", "-p", "9999", "-c", "/usr/local/bin/sumo"]
    process = subprocess.Popen(cmd, cwd=CONF_DIR, stdout=log_file, stderr=log_file)
    time.sleep(2)
    return process, log_file

def run_simulation(args):
    config_name, seed, top_id = args
    
    # Estrutura adaptativa de logs por topologia e configuração
    out_log_dir = os.path.join(LOGS_DIR, top_id, config_name)
    os.makedirs(out_log_dir, exist_ok=True)

    cmd = [
        OPP_RUN, "-l", VEINS_LIB, "-l", LATENCY_PLUGIN_LIB, "-n", NED_PATH,
        "-u", "Cmdenv",
        "-c", config_name,
        "-r", str(seed),
        f"--result-dir={RESULTS_DIR}",
        INI_FILE
    ]

    stdout_path = os.path.join(out_log_dir, f"run_{seed}.out")
    stderr_path = os.path.join(out_log_dir, f"run_{seed}.err")

    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        proc = subprocess.run(cmd, stdout=out, stderr=err, cwd=CONF_DIR)

    if proc.returncode == 0:
        return f"[OK] {config_name} | Seed {seed:02d}"
    else:
        return f"[FAIL] {config_name} | Seed {seed:02d} (Code {proc.returncode})"

def main():
    global LATENCY_PLUGIN_LIB
    print("[BUILD] Compilando módulo de latência...")
    LATENCY_PLUGIN_LIB = build_latency_plugin()
    print(f"[BUILD] Plugin carregável: {LATENCY_PLUGIN_LIB}")

    launchd_proc, log_file = start_launchd()
    try:
        topologies = load_known_topologies()
        configs, repeat_count = parse_omnetpp_ini()

        if not configs:
            print("[ERRO] Nenhuma configuração encontrada no omnetpp.ini.")
            return

        # Montagem adaptativa da matriz de tarefas
        tasks = []
        for config_name in configs:
            top_id = resolve_topology_id(config_name, topologies)
            for seed in range(repeat_count):
                tasks.append((config_name, seed, top_id))

        print(f"=== [Etapa 3] Orquestração Dinâmica (Apple M1) ===")
        print(f"Configurações detectadas no ini: {len(configs)}")
        print(f"Sementes por configuração: {repeat_count}")
        print(f"Total de simulações agendadas: {len(tasks)}")

        # Execução paralela utilizando hilos para o chip M1
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(run_simulation, task) for task in tasks]
            for future in concurrent.futures.as_completed(futures):
                print(future.result())

    finally:
        print("[LAUNCHD] Encerrando o daemon veins_launchd...")
        launchd_proc.terminate()
        log_file.close()

if __name__ == "__main__":
    main()