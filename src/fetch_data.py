import os
import json
import requests
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOMTOM_CONF_PATH = os.path.join(BASE_DIR, "conf", "tomtom.json")
SCENARIOS_CONF_PATH = os.path.join(BASE_DIR, "conf", "coordinates_scenaries.json")
OSM_DIR = os.path.join(BASE_DIR, "osmFiles")
OUTPUT_CALIBRATION_PATH = os.path.join(BASE_DIR, "conf", "tomtom_calibrated_rates.json")

os.makedirs(OSM_DIR, exist_ok=True)

def download_osm_map(top_key, bbox):
    min_lat, min_lon, max_lat, max_lon = bbox
    osm_path = os.path.join(OSM_DIR, f"{top_key}.osm")
    
    print(f"  [OSM] Baixando mapa para '{top_key}' (bbox: {bbox})...")
    osm_api_url = f"https://api.openstreetmap.org/api/0.6/map?bbox={min_lon},{min_lat},{max_lon},{max_lat}"
    
    try:
        response = requests.get(osm_api_url, timeout=30)
        if response.status_code == 200 and len(response.content) > 0:
            with open(osm_path, "wb") as f:
                f.write(response.content)
            print(f"  [OK] Arquivo salvo em: '{osm_path}'")
        else:
            overpass_url = f"https://overpass-api.de/api/interpreter?data=(node({min_lat},{min_lon},{max_lat},{max_lon});<;);out meta;"
            res_overpass = requests.get(overpass_url, timeout=30)
            if res_overpass.status_code == 200:
                with open(osm_path, "wb") as f:
                    f.write(res_overpass.content)
                print(f"  [OK] Arquivo salvo via Overpass em: '{osm_path}'")
    except Exception as e:
        print(f"  [ERRO] Exceção ao baixar o mapa OSM: {e}")

def convert_osm_to_net(top_key):
    """Converte arquivo .osm em .net.xml do SUMO."""
    osm_path = os.path.join(OSM_DIR, f"{top_key}.osm")
    net_path = os.path.join(OSM_DIR, f"{top_key}.net.xml")
    
    if not os.path.exists(osm_path):
        print(f"  [ERRO] Arquivo {osm_path} não existe para conversão.")
        return

    print(f"  [NETCONVERT] Gerando '{net_path}'...")
    cmd = [
        "netconvert",
        "--osm-files", osm_path,
        "-o", net_path,
        "--geometry.remove", "true",
        "--roundabouts.guess", "true",
        "--ramps.guess", "true",
        "--junctions.join", "true",
        "--tls.guess", "true"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"  [OK] Rede SUMO compilada em: '{net_path}'")
    else:
        print(f"  [ERRO] netconvert falhou:\n{res.stderr}")

def main():
    with open(TOMTOM_CONF_PATH, "r", encoding="utf-8") as f:
        tomtom_cfg = json.load(f)
    with open(SCENARIOS_CONF_PATH, "r", encoding="utf-8") as f:
        scenarios_cfg = json.load(f)

    api_key = tomtom_cfg["api_key"]
    hcm_rates = tomtom_cfg["hcm_flow_rates_veh_per_hour_per_lane"]
    calibrated_data = {}

    print("=== [Etapa 1.1] Download de Mapas e Compilação da Rede ===")
    for top_key, top_info in scenarios_cfg.items():
        print(f"\n-> Processando Topologia: {top_info['name']} ({top_key})")
        download_osm_map(top_key, top_info["bbox"])
        convert_osm_to_net(top_key)
        
        speed = top_info["speed_limit_kmh"]
        lanes = top_info["lanes_per_direction"]
        
        calibrated_data[top_key] = {}
        for flow_level, base_rate in hcm_rates.items():
            total_flow = base_rate * lanes
            calibrated_data[top_key][flow_level] = {
                "veh_per_hour_per_direction": total_flow,
                "speed_limit_kmh": speed,
                "edges_centro": top_info["edges_centro"],
                "edges_bairro": top_info["edges_bairro"]
            }

    with open(OUTPUT_CALIBRATION_PATH, "w", encoding="utf-8") as f:
        json.dump(calibrated_data, f, indent=2)

if __name__ == "__main__":
    main()