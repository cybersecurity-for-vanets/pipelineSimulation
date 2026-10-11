import os
import json
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOMTOM_CONF_PATH = os.path.join(BASE_DIR, "conf", "tomtom.json")
SCENARIOS_CONF_PATH = os.path.join(BASE_DIR, "conf", "coordinates_scenaries.json")
OSM_DIR = os.path.join(BASE_DIR, "osmFiles")
OUTPUT_CALIBRATION_PATH = os.path.join(BASE_DIR, "conf", "tomtom_calibrated_rates.json")

os.makedirs(OSM_DIR, exist_ok=True)

def load_configs():
    with open(TOMTOM_CONF_PATH, "r", encoding="utf-8") as f:
        tomtom_cfg = json.load(f)
    with open(SCENARIOS_CONF_PATH, "r", encoding="utf-8") as f:
        scenarios_cfg = json.load(f)
    return tomtom_cfg, scenarios_cfg

def download_osm_map(top_key, bbox):
    """
    Faz o download do mapa .osm para o quadrante definido na bbox [min_lat, min_lon, max_lat, max_lon]
    usando a API oficial do OpenStreetMap / Overpass.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    osm_path = os.path.join(OSM_DIR, f"{top_key}.osm")
    
    print(f"  [OSM] Baixando mapa para '{top_key}' (bbox: {bbox})...")
    
    # URL da API de mapa do OpenStreetMap (bbox: min_lon, min_lat, max_lon, max_lat)
    osm_api_url = f"https://api.openstreetmap.org/api/0.6/map?bbox={min_lon},{min_lat},{max_lon},{max_lat}"
    
    try:
        response = requests.get(osm_api_url, timeout=30)
        if response.status_code == 200 and len(response.content) > 0:
            with open(osm_path, "wb") as f:
                f.write(response.content)
            print(f"  [OK] Arquivo salvo em: '{osm_path}'")
        else:
            print(f"  [AVISO] Falha ao baixar via OSM API (Status {response.status_code}). Usando Overpass API como fallback...")
            # Fallback para Overpass API (bbox: min_lat, min_lon, max_lat, max_lon)
            overpass_url = f"https://overpass-api.de/api/interpreter?data=(node({min_lat},{min_lon},{max_lat},{max_lon});<;);out meta;"
            res_overpass = requests.get(overpass_url, timeout=30)
            if res_overpass.status_code == 200:
                with open(osm_path, "wb") as f:
                    f.write(res_overpass.content)
                print(f"  [OK] Arquivo salvo via Overpass em: '{osm_path}'")
            else:
                print(f"  [ERRO] Não foi possível baixar o mapa para {top_key}.")
    except Exception as e:
        print(f"  [ERRO] Exceção ao baixar o mapa OSM: {e}")

def fetch_tomtom_data(api_key, bbox, default_speed):
    """
    Consulta a velocidade atual na API TomTom Traffic Flow.
    """
    min_lat, min_lon, _, _ = bbox
    url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?key={api_key}&point={min_lat},{min_lon}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get("flowSegmentData", {}).get("currentSpeed", default_speed)
    except Exception:
        pass
    return default_speed

def main():
    tomtom_cfg, scenarios_cfg = load_configs()
    api_key = tomtom_cfg["api_key"]
    hcm_rates = tomtom_cfg["hcm_flow_rates_veh_per_hour_per_lane"]
    
    calibrated_data = {}

    print("=== [Etapa 1.1] Coleta Unificada: OpenStreetMap (.osm) + API TomTom ===")
    
    for top_key, top_info in scenarios_cfg.items():
        print(f"\n-> Processando Topologia: {top_info['name']} ({top_key})")
        bbox = top_info["bbox"]
        lanes = top_info["lanes_per_direction"]
        
        # 1. Download do mapa OSM
        download_osm_map(top_key, bbox)
        
        # 2. Consulta à API TomTom
        speed = fetch_tomtom_data(api_key, bbox, top_info["speed_limit_kmh"])
        
        # 3. Mapeamento das taxas do HCM por sentido
        calibrated_data[top_key] = {}
        for flow_level, base_rate in hcm_rates.items():
            total_flow_per_direction = base_rate * lanes
            calibrated_data[top_key][flow_level] = {
                "veh_per_hour_per_direction": total_flow_per_direction,
                "speed_limit_kmh": speed,
                "edges_centro": top_info["edges_centro"],
                "edges_bairro": top_info["edges_bairro"]
            }
            print(f"   [{flow_level}] Taxa: {total_flow_per_direction} veh/h | Vel: {speed} km/h")

    with open(OUTPUT_CALIBRATION_PATH, "w", encoding="utf-8") as f:
        json.dump(calibrated_data, f, indent=2)
        
    print(f"\n[OK] Processo concluído. Calibração salva em: '{OUTPUT_CALIBRATION_PATH}'")

if __name__ == "__main__":
    main()