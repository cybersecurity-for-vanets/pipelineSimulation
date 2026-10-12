import os
import json
import xml.etree.ElementTree as ET
from collections import deque

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIBRATION_PATH = os.path.join(BASE_DIR, "conf", "tomtom_calibrated_rates.json")
OSM_DIR = os.path.join(BASE_DIR, "osmFiles")
TRAFFIC_DIR = os.path.join(BASE_DIR, "trafficXmlFiles")

os.makedirs(TRAFFIC_DIR, exist_ok=True)

def is_passenger_allowed(edge_elem):
    for lane in edge_elem.findall('lane'):
        allow = lane.get('allow', '')
        disallow = lane.get('disallow', '')

        if disallow:
            dis_list = disallow.split()
            if 'passenger' in dis_list or 'all' in dis_list:
                continue

        if allow:
            allow_list = allow.split()
            if 'passenger' not in allow_list and 'all' not in allow_list:
                continue

        return True
    return False

def get_valid_edges(net_file_path):
    if not os.path.exists(net_file_path):
        return []
    tree = ET.parse(net_file_path)
    root = tree.getroot()
    valid_edges = []

    for edge in root.findall('edge'):
        edge_id = edge.get('id')
        if not edge_id or edge_id.startswith(':') or edge.get('function', '') == 'internal':
            continue

        if is_passenger_allowed(edge):
            valid_edges.append(edge_id)

    return valid_edges

def find_connected_pairs(net_file_path, valid_edges):
    tree = ET.parse(net_file_path)
    root = tree.getroot()
    passenger_set = set(valid_edges)

    adj = {edge_id: set() for edge_id in passenger_set}

    for conn in root.findall('connection'):
        f = conn.get('from')
        t = conn.get('to')
        if f in passenger_set and t in passenger_set and f != t:
            adj[f].add(t)

    def bfs_farthest(start_node):
        visited = {start_node: 0}
        queue = deque([start_node])
        farthest = start_node
        max_dist = 0

        while queue:
            curr = queue.popleft()
            dist = visited[curr]
            if dist > max_dist:
                max_dist = dist
                farthest = curr

            for nxt in adj.get(curr, []):
                if nxt not in visited:
                    visited[nxt] = dist + 1
                    queue.append(nxt)

        return farthest, max_dist

    best_pair_1 = None
    max_d1 = -1
    for start in valid_edges:
        farthest, d = bfs_farthest(start)
        if d > max_d1:
            max_d1 = d
            best_pair_1 = (start, farthest)

    best_pair_2 = None
    max_d2 = -1
    for start in valid_edges:
        if best_pair_1 and start == best_pair_1[0]:
            continue
        farthest, d = bfs_farthest(start)
        if d > max_d2:
            max_d2 = d
            best_pair_2 = (start, farthest)

    if not best_pair_2:
        best_pair_2 = best_pair_1

    return best_pair_1, best_pair_2

def generate_sumo_files():
    if not os.path.exists(CALIBRATION_PATH):
        raise FileNotFoundError(f"Arquivo {CALIBRATION_PATH} não encontrado. Execute fetch_data.py primeiro.")

    with open(CALIBRATION_PATH, "r", encoding="utf-8") as f:
        calibrated_data = json.load(f)

    print("=== [Etapa 1.2] Gerando arquivos de rotas (.rou.xml) e SUMO (.sumocfg) ===")

    for top_key, flows in calibrated_data.items():
        net_path = os.path.join(OSM_DIR, f"{top_key}.net.xml")
        abs_net_path = os.path.abspath(net_path)
        real_edges = get_valid_edges(net_path)

        if not real_edges:
            print(f"[ERRO CRÍTICO] Nenhuma aresta válida para passageiros em: {net_path}")
            continue

        pair_centro, pair_bairro = find_connected_pairs(net_path, real_edges)

        for flow_level, details in flows.items():
            file_prefix = f"{top_key}_{flow_level}"
            rou_filename = f"{file_prefix}.rou.xml"
            sumocfg_filename = f"{file_prefix}.sumocfg"

            rou_path = os.path.join(TRAFFIC_DIR, rou_filename)
            sumocfg_path = os.path.join(TRAFFIC_DIR, sumocfg_filename)

            abs_rou_path = os.path.abspath(rou_path)

            from_c, to_c = pair_centro
            from_b, to_b = pair_bairro

            veh_per_hour = details["veh_per_hour_per_direction"]

            rou_content = f"""<routes>
<vType id="car" vClass="passenger" length="5.0" minGap="2.5" maxSpeed="{details['speed_limit_kmh']/3.6:.2f}" accel="2.6" decel="4.5" />

<!-- Sentido Bairro -> Centro -->
<flow id="flow_centro" type="car" begin="0" end="600" vehsPerHour="{veh_per_hour}" from="{from_c}" to="{to_c}" departLane="allowed" departSpeed="max" />

<!-- Sentido Centro -> Bairro -->
<flow id="flow_bairro" type="car" begin="0" end="600" vehsPerHour="{veh_per_hour}" from="{from_b}" to="{to_b}" departLane="allowed" departSpeed="max" />
</routes>
"""
            with open(rou_path, "w", encoding="utf-8") as f:
                f.write(rou_content)

            # Usar caminhos absolutos garante que o launchd encontre os arquivos
            sumocfg_content = f"""<configuration>
<input>
    <net-file value="{abs_net_path}" />
    <route-files value="{abs_rou_path}" />
</input>
<time>
    <begin value="0" />
    <end value="600" />
</time>
</configuration>
"""
            with open(sumocfg_path, "w", encoding="utf-8") as f:
                f.write(sumocfg_content)

            print(f"[OK] Gerado com caminhos absolutos: {rou_filename} e {sumocfg_filename}")

if __name__ == "__main__":
    generate_sumo_files()