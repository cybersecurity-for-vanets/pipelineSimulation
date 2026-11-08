import os
import sys
import re
import glob
import shutil
import argparse
import subprocess
import concurrent.futures
import json
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SSD_DIR = "/Volumes/PSSD T7/simulationData"
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

def get_free_space_gb(path):
    """Retorna o espaço livre em GB para o caminho fornecido."""
    target = path
    while not os.path.exists(target) and os.path.dirname(target) != target:
        target = os.path.dirname(target)
    try:
        return shutil.disk_usage(target).free / (1024 ** 3)
    except Exception:
        return 0.0

def setup_storage(storage_preference="auto", min_gb_required=10.0):
    """
    Configura LOGS_DIR e RESULTS_DIR com base na disponibilidade de espaço em disco.
    storage_preference: 'auto', 'ssd', ou 'local'.
    """
    global LOGS_DIR, RESULTS_DIR

    ssd_mount = "/Volumes/PSSD T7"
    ssd_mounted = os.path.isdir(ssd_mount)
    ssd_free_gb = get_free_space_gb(ssd_mount) if ssd_mounted else 0.0
    local_free_gb = get_free_space_gb(BASE_DIR)

    chosen = None
    if storage_preference == "ssd":
        if not ssd_mounted:
            raise FileNotFoundError(f"[ERRO] SSD externo não está montado em '{ssd_mount}'.")
        if ssd_free_gb < min_gb_required:
            raise OSError(
                f"[SEM ESPAÇO] SSD '{ssd_mount}' possui apenas {ssd_free_gb*1024:.1f} MB livres "
                f"(mínimo necessário: {min_gb_required:.1f} GB).\n"
                f"Dica: '/Volumes/PSSD T7/simulationData.old' está ocupando espaço no SSD. "
                f"Libere espaço ou utilize '--storage local'."
            )
        chosen = SSD_DIR
    elif storage_preference == "local":
        if local_free_gb < min_gb_required:
            raise OSError(
                f"[SEM ESPAÇO] Armazenamento local possui apenas {local_free_gb:.1f} GB livres "
                f"(mínimo necessário: {min_gb_required:.1f} GB)."
            )
        chosen = BASE_DIR
    else:  # auto
        if ssd_mounted and ssd_free_gb >= min_gb_required:
            chosen = SSD_DIR
        elif local_free_gb >= min_gb_required:
            if ssd_mounted and ssd_free_gb < min_gb_required:
                print(f"[AVISO] SSD em '{ssd_mount}' está quase cheio ({ssd_free_gb*1024:.1f} MB livres).")
                print(f"[AVISO] Alternando automaticamente para o armazenamento local ({local_free_gb:.1f} GB livres).")
                print(f"        (Para liberar o SSD, verifique '/Volumes/PSSD T7/simulationData.old')")
            chosen = BASE_DIR
        else:
            raise OSError(
                f"[SEM ESPAÇO] Espaço em disco insuficiente em todos os destinos!\n"
                f" - SSD ({ssd_mount}): {ssd_free_gb*1024:.1f} MB livres\n"
                f" - Local ({BASE_DIR}): {local_free_gb:.1f} GB livres\n"
                f"Mínimo necessário: {min_gb_required:.1f} GB. Libere espaço antes de executar as simulações."
            )

    if chosen == SSD_DIR:
        LOGS_DIR = os.path.join(SSD_DIR, "logs")
        RESULTS_DIR = os.path.join(SSD_DIR, "results", "rawData")
    else:
        LOGS_DIR = os.path.join(BASE_DIR, "logs")
        RESULTS_DIR = os.path.join(BASE_DIR, "results", "rawData")

    os.makedirs(LOGS_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    print(f"[STORAGE] Destino: {chosen}")
    print(f"          LOGS_DIR:    {LOGS_DIR}")
    print(f"          RESULTS_DIR: {RESULTS_DIR}")
    print(f"          Espaço livre: {get_free_space_gb(RESULTS_DIR):.2f} GB")
    return chosen

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
    try:
        os.makedirs(out_log_dir, exist_ok=True)
    except OSError as e:
        return f"[FAIL] {config_name} | Seed {seed:02d} (Erro ao criar diretório: {e})"

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

    try:
        with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
            proc = subprocess.run(cmd, stdout=out, stderr=err, cwd=CONF_DIR)
    except OSError as e:
        return f"[FAIL] {config_name} | Seed {seed:02d} (Erro de I/O: {e})"

    if proc.returncode == 0:
        return f"[OK] {config_name} | Seed {seed:02d}"
    else:
        return f"[FAIL] {config_name} | Seed {seed:02d} (Code {proc.returncode})"

def main(workers=6):
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(run_simulation, task) for task in tasks]
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
    parser = argparse.ArgumentParser(description="Orquestração Dinâmica - Etapa 3")
    parser.add_argument(
        "--storage",
        choices=["auto", "ssd", "local"],
        default="auto",
        help="Destino dos dados: 'auto' (prioriza SSD com espaço suficiente, caso contrário usa disco local), 'ssd' ou 'local'"
    )
    parser.add_argument("--workers", type=int, default=6, help="Número de threads simultâneas (padrão: 6)")
    parser.add_argument("--min-gb", type=float, default=10.0, help="Espaço mínimo livre exigido em GB (padrão: 10.0)")
    args = parser.parse_args()

    setup_storage(storage_preference=args.storage, min_gb_required=args.min_gb)
    main(workers=args.workers)