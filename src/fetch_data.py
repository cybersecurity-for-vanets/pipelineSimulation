"""Etapa 1.1: redes OSM/SUMO em volta dos trechos CET e mapeamento das rotas para arestas SUMO.

Entrada: conf/routes.json (trechos CET MSVP 2019 por topologia, com pontos [lat, lon] em ordem de viagem).
Saídas: osmFiles/<topologia>.osm e .net.xml, e conf/cet_scenarios.json com as rotas mapeadas.

Os dados de volume e velocidade da CET ficam em conf/cet_volumes.csv e conf/cet_speeds.csv e são lidos na calibração.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

try:
    from .sumo_route_utils import build_topology_edge_routes
except ImportError:
    from sumo_route_utils import build_topology_edge_routes

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from calibration.params import load_calibration_params  # noqa: E402

CONF_DIR = BASE_DIR / "conf"
OSM_DIR = BASE_DIR / "osmFiles"
ROUTES_CONF_PATH = CONF_DIR / "routes.json"
SCENARIOS_OUTPUT_PATH = CONF_DIR / "cet_scenarios.json"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
OSM_USER_AGENT = "pipelineSimulation/1.0 (SUMO Krauss calibration)"
TOPOLOGY_FILE_NAMES = {"urban_road": "urbanRoad"}


def load_json(path):
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def load_routes(routes_config):
    """{rota: dados da rota + topology} a partir de conf/routes.json."""
    routes = {}
    for topology, topology_config in routes_config["topologies"].items():
        for name, route in topology_config["routes"].items():
            if name in routes:
                raise ValueError(f"Rota repetida em routes.json: {name}")
            if len(route.get("points", [])) < 2:
                raise ValueError(f"Rota {name} precisa de pelo menos 2 pontos [lat, lon]")
            routes[name] = {**route, "topology": topology}
    return routes


def calculate_route_bboxes(routes, margin_km):
    points = {}
    for route in routes.values():
        points.setdefault(route["topology"], []).extend(route["points"])
    bboxes = {}
    for topology, coordinates in points.items():
        latitudes = [point[0] for point in coordinates]
        longitudes = [point[1] for point in coordinates]
        center_latitude = (min(latitudes) + max(latitudes)) / 2
        latitude_margin = margin_km / 110.574
        longitude_margin = margin_km / (111.320 * max(math.cos(math.radians(center_latitude)), 0.1))
        bboxes[topology] = (
            min(latitudes) - latitude_margin,
            min(longitudes) - longitude_margin,
            max(latitudes) + latitude_margin,
            max(longitudes) + longitude_margin,
        )
    return bboxes


def download_osm_map(topology, bbox, session=None):
    """Só as vias (way[highway]) do bbox, pelo Overpass: o centro de São Paulo passa do limite da API do OSM."""
    session = session or requests.Session()
    session.headers.update({"User-Agent": OSM_USER_AGENT})
    min_lat, min_lon, max_lat, max_lon = bbox
    osm_path = OSM_DIR / f"{TOPOLOGY_FILE_NAMES.get(topology, topology)}.osm"
    query = f'[out:xml][timeout:180];(way["highway"]({min_lat},{min_lon},{max_lat},{max_lon});>;);out body;'

    response = None
    for attempt in range(2):
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                response = session.post(endpoint, data={"data": query}, timeout=120)
            except requests.RequestException as error:
                print(f"[OSM] {topology}: {endpoint} falhou ({error.__class__.__name__}); tentando o próximo")
                response = None
                continue
            if response.status_code == 200 and response.content:
                break
            print(f"[OSM] {topology}: {endpoint} respondeu HTTP {response.status_code}; tentando o próximo")
            response = None
        if response is not None:
            break
        time.sleep(10)
    OSM_DIR.mkdir(parents=True, exist_ok=True)
    if response is None:
        print(f"[OSM] {topology}: Overpass indisponível; baixando em blocos pela API do OSM")
        osm_path.write_bytes(download_osm_api_tiles(bbox, session))
    else:
        osm_path.write_bytes(response.content)
    print(f"[OSM] {topology}: mapa salvo em {osm_path}")
    return osm_path


def download_osm_api_tiles(bbox, session, depth=0):
    """Mapa do bbox pela API do OSM (limite de 50 mil nós por pedido), dividindo em quatro quando passa do limite.

    Os blocos são unidos por id de elemento: nós e vias na borda de dois blocos aparecem uma vez só.
    """
    tiles = _api_tiles(bbox, session, depth)
    elements = {"node": {}, "way": {}, "relation": {}}
    for tile in tiles:
        for element in ET.fromstring(tile):
            if element.tag in elements:
                elements[element.tag][element.get("id")] = element
    root = ET.Element("osm", {"version": "0.6", "generator": "pipelineSimulation fetch_data.py (API tiles)"})
    min_lat, min_lon, max_lat, max_lon = bbox
    ET.SubElement(root, "bounds", {"minlat": str(min_lat), "minlon": str(min_lon),
                                   "maxlat": str(max_lat), "maxlon": str(max_lon)})
    for tag in ("node", "way", "relation"):
        for element_id in sorted(elements[tag], key=int):
            root.append(elements[tag][element_id])
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _api_tiles(bbox, session, depth):
    min_lat, min_lon, max_lat, max_lon = bbox
    url = f"https://api.openstreetmap.org/api/0.6/map?bbox={min_lon},{min_lat},{max_lon},{max_lat}"
    for attempt in range(3):
        try:
            response = session.get(url, timeout=120)
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
            continue
        if response.status_code == 200:
            return [response.content]
        if response.status_code == 400 and depth < 6:
            middle_lat, middle_lon = (min_lat + max_lat) / 2, (min_lon + max_lon) / 2
            quarters = [
                (min_lat, min_lon, middle_lat, middle_lon), (min_lat, middle_lon, middle_lat, max_lon),
                (middle_lat, min_lon, max_lat, middle_lon), (middle_lat, middle_lon, max_lat, max_lon),
            ]
            return [tile for quarter in quarters for tile in _api_tiles(quarter, session, depth + 1)]
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"A API do OSM falhou para o bloco {bbox}")


def convert_osm_to_net(topology, osm_path):
    netconvert = os.environ.get("NETCONVERT_BINARY") or shutil.which("netconvert")
    if not netconvert:
        raise FileNotFoundError("netconvert não está no PATH; defina NETCONVERT_BINARY")

    net_path = OSM_DIR / f"{TOPOLOGY_FILE_NAMES.get(topology, topology)}.net.xml"
    command = [
        netconvert,
        "--osm-files", str(osm_path),
        "-o", str(net_path),
        "--geometry.remove", "true",
        "--roundabouts.guess", "true",
        "--ramps.guess", "true",
        "--junctions.join", "true",
        # Só os semáforos mapeados no OSM: --tls.guess cria semáforos de 90 s sem coordenação.
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=BASE_DIR)
    if result.returncode != 0:
        raise RuntimeError(f"netconvert falhou para '{topology}': {result.stderr.strip()}")
    print(f"[SUMO] {topology}: rede salva em {net_path}")
    return net_path


def main():
    parser = argparse.ArgumentParser(description="Etapa 1.1: redes OSM/SUMO e rotas CET")
    parser.add_argument("--skip-download", action="store_true",
                        help="Usa os .osm já baixados em osmFiles/ (refaz netconvert e o mapeamento)")
    parser.add_argument("--skip-netconvert", action="store_true",
                        help="Usa os .net.xml existentes e só refaz o mapeamento das rotas")
    args = parser.parse_args()

    params = load_calibration_params()
    routes_config = load_json(ROUTES_CONF_PATH)
    routes = load_routes(routes_config)
    bboxes = calculate_route_bboxes(routes, params["network"]["map_margin_km"])

    print("=== Etapa 1.1: redes OSM em volta dos trechos CET ===")
    for topology, bbox in sorted(bboxes.items()):
        file_topology = TOPOLOGY_FILE_NAMES.get(topology, topology)
        if args.skip_netconvert:
            continue
        osm_path = OSM_DIR / f"{file_topology}.osm"
        if not args.skip_download:
            osm_path = download_osm_map(topology, bbox)
        elif not osm_path.is_file():
            raise FileNotFoundError(f"{osm_path} não existe; rode sem --skip-download")
        convert_osm_to_net(topology, osm_path)

    print("=== Etapa 1.1: mapeamento das rotas CET na rede SUMO ===")
    edge_routes = build_topology_edge_routes(routes, params["network"]["max_length_ratio_error"])
    output_routes = {}
    for name, route in sorted(routes.items()):
        mapped = edge_routes[route["topology"]][name]
        output_routes[name] = {
            **{key: value for key, value in route.items() if key != "points"},
            "points": route["points"],
            "sumo_edges": mapped["edges"],
            "lanes_per_direction": mapped["lanes_per_direction"],
            "speed_limit_kmh": mapped["speed_limit_kmh"],
            "sumo_length_m": mapped["sumo_length_m"],
        }
        print(f"[OK] {name}: {len(mapped['edges'])} arestas, {mapped['sumo_length_m']:.0f} m "
              f"(CET {route.get('cet_distance_m', 0):.0f} m), {mapped['lanes_per_direction']} faixa(s), "
              f"{mapped['speed_limit_kmh']:.0f} km/h")

    output = {
        "source": routes_config.get("source", "CET MSVP 2019"),
        "map_bboxes": {topology: list(bbox) for topology, bbox in bboxes.items()},
        "routes": output_routes,
    }
    SCENARIOS_OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Rotas CET mapeadas salvas em {SCENARIOS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
