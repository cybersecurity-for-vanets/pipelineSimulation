import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIBRATION_PATH = os.path.join(BASE_DIR, "conf", "tomtom_calibrated_rates.json")
TRAFFIC_DIR = os.path.join(BASE_DIR, "trafficXmlFiles")

os.makedirs(TRAFFIC_DIR, exist_ok=True)

def generate_sumo_files():
    if not os.path.exists(CALIBRATION_PATH):
        raise FileNotFoundError(f"Arquivo {CALIBRATION_PATH} não encontrado. Execute fetch_data.py primeiro.")

    with open(CALIBRATION_PATH, "r", encoding="utf-8") as f:
        calibrated_data = json.load(f)

    print("=== [Etapa 1.2] Gerando arquivos de rotas (.rou.xml) e SUMO (.sumocfg) ===")

    for top_key, flows in calibrated_data.items():
        for flow_level, details in flows.items():
            file_prefix = f"{top_key}_{flow_level}"
            rou_filename = f"{file_prefix}.rou.xml"
            sumocfg_filename = f"{file_prefix}.sumocfg"
            
            rou_path = os.path.join(TRAFFIC_DIR, rou_filename)
            sumocfg_path = os.path.join(TRAFFIC_DIR, sumocfg_filename)

            veh_per_hour = details["veh_per_hour_per_direction"]
            edges_centro = " ".join(details["edges_centro"])
            edges_bairro = " ".join(details["edges_bairro"])

            # 1. Gerar Arquivo .rou.xml com fluxos bidirecionais
            rou_content = f"""<routes>
    <vType id="car" vClass="passenger" length="5.0" minGap="2.5" maxSpeed="{details['speed_limit_kmh']/3.6:.2f}" accel="2.6" decel="4.5" />

    <!-- Sentido Bairro -> Centro -->
    <flow id="flow_centro" type="car" begin="0" end="600" vehsPerHour="{veh_per_hour}" from="{details['edges_centro'][0]}" to="{details['edges_centro'][-1]}" departLane="free" departSpeed="max" />

    <!-- Sentido Centro -> Bairro -->
    <flow id="flow_bairro" type="car" begin="0" end="600" vehsPerHour="{veh_per_hour}" from="{details['edges_bairro'][0]}" to="{details['edges_bairro'][-1]}" departLane="free" departSpeed="max" />
</routes>
"""
            with open(rou_path, "w", encoding="utf-8") as f:
                f.write(rou_content)

            # 2. Gerar Arquivo .sumocfg correspondente
            net_file = f"../osmFiles/{top_key}.net.xml"
            sumocfg_content = f"""<configuration>
    <input>
        <net-file value="{net_file}" />
        <route-files value="{rou_filename}" />
    </input>
    <time>
        <begin value="0" />
        <end value="600" />
    </time>
</configuration>
"""
            with open(sumocfg_path, "w", encoding="utf-8") as f:
                f.write(sumocfg_content)

            print(f"[OK] Criado: {rou_filename} e {sumocfg_filename}")

if __name__ == "__main__":
    generate_sumo_files()