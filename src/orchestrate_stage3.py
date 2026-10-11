import os
import sys
import subprocess
import concurrent.futures
import json
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_DIR = os.path.join(BASE_DIR, "conf")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
RESULTS_DIR = os.path.join(BASE_DIR, "results", "rawData")
SCENARIOS_CONF_PATH = os.path.join(CONF_DIR, "coordinates_scenaries.json")
TOMTOM_CONF_PATH = os.path.join(CONF_DIR, "tomtom.json")

OPP_RUN = "/Users/fabiotorres/omnetpp-6.2.0/bin/opp_run"
VEINS_LIB = "/Users/fabiotorres/veins-veins-5.3.1/src/veins"
VEINS_LAUNCHD = "/Users/fabiotorres/veins-veins-5.3.1/bin/veins_launchd"
NED_PATH = f"/Users/fabiotorres/veins-veins-5.3.1/ned:{CONF_DIR}"
INI_FILE = os.path.join(CONF_DIR, "omnetpp.ini")

os.makedirs(RESULTS_DIR, exist_ok=True)

def start_launchd():
    """Inicia o daemon do veins_launchd na porta 9999 se não estiver ativo."""
    print("[LAUNCHD] Iniciando o daemon veins_launchd na porta 9999...")
    cmd = [sys.executable, VEINS_LAUNCHD, "-v", "9999", "."]
    process = subprocess.Popen(cmd, cwd=CONF_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)  # Aguarda a inicialização do socket
    return process

def run_simulation(args):
    config_name, seed, log_subfolder = args
    
    out_log_dir = os.path.join(LOGS_DIR, log_subfolder, config_name)
    os.makedirs(out_log_dir, exist_ok=True)

    cmd = [
        OPP_RUN, "-l", VEINS_LIB, "-n", NED_PATH,
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
        return f"[FAIL] {config_name} | Seed {seed:02d} - Reason: Exit code {proc.returncode}"

def main():
    launchd_proc = start_launchd()
    try:
        with open(SCENARIOS_CONF_PATH, "r", encoding="utf-8") as f:
            topologies = list(json.load(f).keys())
        with open(TOMTOM_CONF_PATH, "r", encoding="utf-8") as f:
            flows = list(json.load(f)["hcm_flow_rates_veh_per_hour_per_lane"].keys())

        protocols = ["ASCON_128", "Chaskey", "Proposed_V2X", "Ed25519"]
        seeds = range(30)

        tasks = []
        for top in topologies:
            for proto in protocols:
                for flow in flows:
                    cfg_name = f"Config_{top}_{proto}_{flow}"
                    for s in seeds:
                        tasks.append((cfg_name, s, top))

        print(f"=== Iniciando Orquestração Etapa 3 no Apple M1 ===")
        print(f"Total de simulações agendadas: {len(tasks)} (36 configs x 30 seeds)")

        # Execução em lotes paralelos no Apple M1
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(run_simulation, task) for task in tasks]
            for future in concurrent.futures.as_completed(futures):
                print(future.result())

    finally:
        print("[LAUNCHD] Encerrando o daemon veins_launchd...")
        launchd_proc.terminate()

if __name__ == "__main__":
    main()