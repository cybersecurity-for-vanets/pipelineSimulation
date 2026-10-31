"""Etapa 1.3: rotas SUMO de cada nível de fluxo (uma hora pesquisada pela CET) e conf/flow_levels.json.

Entradas: conf/cet_scenarios.json (fetch_data.py), conf/route_flows_calibrated.json e
conf/krauss_calibration.json (calibrate_stage1.py), cet.flow_level_hours de conf/calibration_params.json.
Saídas: trafficXmlFiles/<topologia>_<nível>.rou.xml e .sumocfg, e conf/flow_levels.json (contrato em flow_levels.py).
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = BASE_DIR / "conf"
OSM_DIR = BASE_DIR / "osmFiles"
TRAFFIC_DIR = BASE_DIR / "trafficXmlFiles"
SCENARIOS_PATH = CONF_DIR / "cet_scenarios.json"
KRAUSS_CALIBRATION_PATH = CONF_DIR / "krauss_calibration.json"
ROUTE_FLOWS_PATH = CONF_DIR / "route_flows_calibrated.json"
TOPOLOGY_FILE_NAMES = {"urban_road": "urbanRoad"}
ROUTE_END_SECONDS = 3600
# SUMO step and Veins TraCI update interval: ETSI CAM triggers are checked every 100 ms.
SUMO_STEP_LENGTH = 0.1


def safe_id(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")


def file_topology(topology):
    return TOPOLOGY_FILE_NAMES.get(topology, topology)


def tls_offsets_path(topology):
    """Coordinated traffic light programs written by calibrate_stage1.py, if the topology uses them."""
    return OSM_DIR / f"{file_topology(topology)}.tls_offsets.add.xml"


def load_json(path, required=True):
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"Arquivo necessário não encontrado: {path}")
        return {}
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def build_route_file(topology, routes, flow_rate, krauss, route_flows=None):
    """flow_rate is veh/h/lane; route_flows optionally overrides it per route with total veh/h.

    krauss holds the car-following parameters plus speedFactor and speedDev; a route's own speed_factor wins.
    """
    route_flows = route_flows or {}
    root = ET.Element("routes")

    for route_name, route_data in sorted(routes.items()):
        edges = route_data.get("sumo_edges", [])
        if len(edges) < 2:
            raise ValueError(f"Route '{route_name}' has no connected SUMO edge path")

        suffix = safe_id(route_name)
        route_id = f"route_{suffix}"
        flow_id = f"flow_{suffix}"
        vehicle_type_id = f"car_{suffix}"
        ET.SubElement(root, "vType", {
            "id": vehicle_type_id,
            "vClass": "passenger",
            "carFollowModel": "Krauss",
            "length": "5.0",
            "minGap": str(krauss["minGap"]),
            "maxSpeed": f"{route_data['speed_limit_kmh'] / 3.6:.2f}",
            "accel": str(krauss["accel"]),
            "decel": str(krauss["decel"]),
            "tau": str(krauss["tau"]),
            "sigma": str(krauss["sigma"]),
            "speedFactor": str(route_data.get("speed_factor", krauss.get("speedFactor", 1))),
            "speedDev": str(krauss.get("speedDev", 0.1)),
        })
        ET.SubElement(root, "route", {"id": route_id, "edges": " ".join(edges)})
        ET.SubElement(root, "flow", {
            "id": flow_id,
            "type": vehicle_type_id,
            "route": route_id,
            "begin": "0",
            "end": str(ROUTE_END_SECONDS),
            "vehsPerHour": str(route_flows.get(route_name, flow_rate * route_data["lanes_per_direction"])),
            "departLane": "allowed",
            "departSpeed": "max",
        })

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def build_sumocfg(topology, route_path, net_path):
    root = ET.Element("configuration")
    inputs = ET.SubElement(root, "input")
    ET.SubElement(inputs, "net-file", {"value": str(net_path.resolve())})
    ET.SubElement(inputs, "route-files", {"value": str(route_path.resolve())})
    # veins_launchd copies only the .sumocfg, so the absolute path keeps the offsets reachable.
    if tls_offsets_path(topology).is_file():
        ET.SubElement(inputs, "additional-files", {"value": str(tls_offsets_path(topology).resolve())})
    time = ET.SubElement(root, "time")
    ET.SubElement(time, "begin", {"value": "0"})
    ET.SubElement(time, "end", {"value": str(ROUTE_END_SECONDS)})
    processing = ET.SubElement(root, "processing")
    ET.SubElement(processing, "step-length", {"value": str(SUMO_STEP_LENGTH)})
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def generate_sumo_files():
    sys.path.insert(0, str(BASE_DIR))
    from calibration.params import load_calibration_params
    from flow_levels import FLOW_LEVELS_PATH, level_id, level_label

    params = load_calibration_params()
    routes = load_json(SCENARIOS_PATH)["routes"]
    if not routes:
        raise ValueError(f"{SCENARIOS_PATH} não tem rotas; rode fetch_data.py")
    calibrated = load_json(ROUTE_FLOWS_PATH)["routes"]
    krauss_profiles = load_json(KRAUSS_CALIBRATION_PATH)
    hours = sorted(params["cet"]["flow_level_hours"])

    missing = set(routes) - set(calibrated)
    if missing:
        raise ValueError(f"Sem fluxo calibrado para {sorted(missing)}; rode calibrate_stage1.py")

    TRAFFIC_DIR.mkdir(parents=True, exist_ok=True)
    flow_levels = {
        "source": "Volumes CET MSVP 2019 por faixa horária (equivalente-carro, cet.pce)",
        "levels": [level_id(hour) for hour in hours],
        "labels": {level_id(hour): level_label(level_id(hour)) for hour in hours},
        "topologies": {},
    }
    for topology in sorted({route["topology"] for route in routes.values()}):
        topology_routes = {name: route for name, route in routes.items() if route["topology"] == topology}
        if topology not in krauss_profiles:
            raise ValueError(f"Sem perfil Krauss para '{topology}' em {KRAUSS_CALIBRATION_PATH}; rode calibrate_stage1.py")
        krauss = krauss_profiles[topology]["params"]
        net_path = OSM_DIR / f"{file_topology(topology)}.net.xml"
        if not net_path.is_file():
            raise FileNotFoundError(f"Rede SUMO não encontrada: {net_path}")
        # Faixas típicas (mediana) de cada rota: o gargalo das pontas não representa a seção da contagem CET.
        lanes = sum(route["lanes_per_direction"] for route in topology_routes.values())
        levels = flow_levels["topologies"].setdefault(file_topology(topology), {})

        for hour in hours:
            level = level_id(hour)
            route_flows = {}
            for name in topology_routes:
                hourly = calibrated[name]["hourly_flow_veh_h"]
                if str(hour) not in hourly:
                    raise ValueError(f"Rota {name} não tem fluxo para {hour}h em {ROUTE_FLOWS_PATH}")
                route_flows[name] = round(hourly[str(hour)])
            prefix = f"{file_topology(topology)}_{level}"
            route_path = TRAFFIC_DIR / f"{prefix}.rou.xml"
            sumocfg_path = TRAFFIC_DIR / f"{prefix}.sumocfg"
            route_path.write_text(build_route_file(topology, topology_routes, 0, krauss, route_flows), encoding="utf-8")
            sumocfg_path.write_text(build_sumocfg(topology, route_path, net_path), encoding="utf-8")
            total = sum(route_flows.values())
            levels[level] = {"total_veh_h": total, "veh_h_per_lane": round(total / lanes, 1), "routes": route_flows}
            print(f"[OK] {prefix}: {len(topology_routes)} rotas, {total} veic/h ({total / lanes:.0f} veic/h/faixa)")

    FLOW_LEVELS_PATH.write_text(json.dumps(flow_levels, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Níveis de fluxo salvos em {FLOW_LEVELS_PATH}")


if __name__ == "__main__":
    generate_sumo_files()
