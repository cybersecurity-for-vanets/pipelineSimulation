import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = BASE_DIR / "conf"
OSM_DIR = BASE_DIR / "osmFiles"
TRAFFIC_DIR = BASE_DIR / "trafficXmlFiles"
CALIBRATION_DATA_PATH = CONF_DIR / "tomtom_calibrated_rates.json"
KRAUSS_CALIBRATION_PATH = CONF_DIR / "krauss_calibration.json"
TOPOLOGY_FILE_NAMES = {"urban_road": "urbanRoad"}
DEFAULT_KRAUSS = {"accel": 2.6, "decel": 4.5, "tau": 1.0, "sigma": 0.5, "minGap": 2.5}
ROUTE_END_SECONDS = 3600
# SUMO step and Veins TraCI update interval: ETSI CAM triggers are checked every 100 ms.
SUMO_STEP_LENGTH = 0.1


def safe_id(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")


def load_json(path, required=True):
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"Required file not found: {path}")
        return {}
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def build_route_file(topology, routes, flow_rate, krauss, route_flows=None):
    """flow_rate is veh/h/lane; route_flows optionally overrides it per route with total veh/h."""
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
            "speedFactor": "1",
            "speedDev": "0.1",
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
    time = ET.SubElement(root, "time")
    ET.SubElement(time, "begin", {"value": "0"})
    ET.SubElement(time, "end", {"value": str(ROUTE_END_SECONDS)})
    processing = ET.SubElement(root, "processing")
    ET.SubElement(processing, "step-length", {"value": str(SUMO_STEP_LENGTH)})
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def generate_sumo_files():
    calibration_data = load_json(CALIBRATION_DATA_PATH)
    calibration_profiles = load_json(KRAUSS_CALIBRATION_PATH, required=False)
    routes = calibration_data.get("routes", {})
    if not routes:
        raise ValueError("Traffic Stats data contains no routes; run fetch_data.py first")

    hcm_rates = calibration_data.get("hcm_flow_rates_veh_per_hour_per_lane")
    if not hcm_rates:
        raise ValueError("Traffic Stats data is missing HCM flow rates")

    TRAFFIC_DIR.mkdir(parents=True, exist_ok=True)
    topologies = sorted({route.get("topology") for route in routes.values()})
    for topology in topologies:
        topology_routes = {
            name: route for name, route in routes.items()
            if route.get("topology") == topology
        }
        if not topology_routes:
            raise ValueError(f"Traffic Stats data has no routes for topology '{topology}'")

        file_topology = TOPOLOGY_FILE_NAMES.get(topology, topology)
        net_path = OSM_DIR / f"{file_topology}.net.xml"
        if not net_path.is_file():
            raise FileNotFoundError(f"SUMO network not found: {net_path}")

        krauss = calibration_profiles.get(topology, DEFAULT_KRAUSS)

        for flow_name, flow_rate in hcm_rates.items():
            prefix = f"{file_topology}_{flow_name}"
            route_path = TRAFFIC_DIR / f"{prefix}.rou.xml"
            sumocfg_path = TRAFFIC_DIR / f"{prefix}.sumocfg"
            route_xml = build_route_file(topology, topology_routes, flow_rate, krauss)
            route_path.write_text(route_xml, encoding="utf-8")
            sumocfg_xml = build_sumocfg(topology, route_path, net_path)
            sumocfg_path.write_text(sumocfg_xml, encoding="utf-8")
            print(f"[OK] {prefix}: {len(topology_routes)} TomTom routes")


if __name__ == "__main__":
    generate_sumo_files()