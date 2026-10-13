import json
import argparse
import math
import os
import shutil
import subprocess
from pathlib import Path

import requests

try:
    from .tomtom_traffic_stats import (
        create_and_wait_for_route_analysis,
        normalize_route_analysis,
    )
    from .sumo_route_utils import build_topology_edge_routes
except ImportError:
    from tomtom_traffic_stats import (
        create_and_wait_for_route_analysis,
        normalize_route_analysis,
    )
    from sumo_route_utils import build_topology_edge_routes


BASE_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = BASE_DIR / "conf"
OSM_DIR = BASE_DIR / "osmFiles"
TOMTOM_CONF_PATH = CONF_DIR / "tomtom.json"
ROUTES_CONF_PATH = CONF_DIR / "routes.json"
SCENARIOS_CONF_PATH = CONF_DIR / "coordinates_scenaries.json"
OUTPUT_CALIBRATION_PATH = CONF_DIR / "tomtom_calibrated_rates.json"
MAP_MARGIN_KM = 2.0
TOPOLOGY_FILE_NAMES = {"urban_road": "urbanRoad"}


def load_json(path):
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def calculate_route_bboxes(calibration_data, margin_km=MAP_MARGIN_KM):
    coordinates = {}
    for route in calibration_data.get("routes", {}).values():
        topology = route["topology"]
        points = coordinates.setdefault(topology, [])
        for segment in route.get("segments", []):
            for longitude, latitude in segment.get("shape_lon_lat", []):
                points.append((latitude, longitude))
    if not coordinates or any(not points for points in coordinates.values()):
        raise ValueError("Traffic Stats result has no route geometries for deriving OSM bounds")

    bboxes = {}
    for topology, points in coordinates.items():
        latitudes = [point[0] for point in points]
        longitudes = [point[1] for point in points]
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
    session = session or requests.Session()
    min_lat, min_lon, max_lat, max_lon = bbox
    osm_path = OSM_DIR / f"{TOPOLOGY_FILE_NAMES.get(topology, topology)}.osm"
    bbox_string = f"{min_lon},{min_lat},{max_lon},{max_lat}"
    map_url = f"https://api.openstreetmap.org/api/0.6/map?bbox={bbox_string}"

    response = session.get(map_url, timeout=60)
    if response.status_code != 200 or not response.content:
        query = f"(way({min_lat},{min_lon},{max_lat},{max_lon});>;);out body;"
        response = session.get(
            "https://overpass-api.de/api/interpreter",
            params={"data": query},
            timeout=120,
        )
    response.raise_for_status()
    if not response.content:
        raise RuntimeError(f"OSM returned an empty map for topology '{topology}'")

    OSM_DIR.mkdir(parents=True, exist_ok=True)
    osm_path.write_bytes(response.content)
    print(f"[OSM] {topology}: saved map using route-derived bounds to {osm_path}")
    return osm_path


def convert_osm_to_net(topology, osm_path):
    netconvert = os.environ.get("NETCONVERT_BINARY") or shutil.which("netconvert")
    if not netconvert:
        raise FileNotFoundError("netconvert is not on PATH; set NETCONVERT_BINARY")

    net_path = OSM_DIR / f"{TOPOLOGY_FILE_NAMES.get(topology, topology)}.net.xml"
    command = [
        netconvert,
        "--osm-files", str(osm_path),
        "-o", str(net_path),
        "--geometry.remove", "true",
        "--roundabouts.guess", "true",
        "--ramps.guess", "true",
        "--junctions.join", "true",
        "--tls.guess", "true",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=BASE_DIR)
    if result.returncode != 0:
        raise RuntimeError(f"netconvert failed for '{topology}': {result.stderr.strip()}")
    print(f"[SUMO] {topology}: network saved to {net_path}")
    return net_path


def main():
    parser = argparse.ArgumentParser(description="Fetch one historical TomTom Traffic Stats day for the target routes")
    parser.add_argument("--dry-run", action="store_true", help="Print the request summary without network calls")
    args = parser.parse_args()

    tomtom_config = load_json(TOMTOM_CONF_PATH)
    routes_config = load_json(ROUTES_CONF_PATH)

    if args.dry_run:
        try:
            from .tomtom_traffic_stats import build_route_analysis_payload
        except ImportError:
            from tomtom_traffic_stats import build_route_analysis_payload
        payload = build_route_analysis_payload(routes_config, tomtom_config)
        print(f"[DRY RUN] Date: {payload['dateRanges'][0]['from']}")
        print(f"[DRY RUN] Time zone: {payload['routes'][0]['zoneId']}")
        print(f"[DRY RUN] Routes: {len(payload['routes'])}")
        print(f"[DRY RUN] Hourly time sets: {len(payload['timeSets'])}")
        for route in payload["routes"]:
            print(f"[DRY RUN] {route['name']}")
        return

    print("=== Stage 1.1: TomTom Traffic Stats for target routes ===")
    job_id, route_analysis = create_and_wait_for_route_analysis(
        routes_config,
        tomtom_config,
    )
    selected_date = tomtom_config["date_range"]["24h_data"]
    calibration_data = normalize_route_analysis(
        route_analysis,
        routes_config,
        selected_date,
        tomtom_config.get("time_zone", "America/Sao_Paulo"),
    )
    bboxes = calculate_route_bboxes(calibration_data)
    print("=== Stage 1.2: Route-centered OSM maps ===")
    for topology, bbox in bboxes.items():
        osm_path = download_osm_map(topology, bbox)
        convert_osm_to_net(topology, osm_path)

    edge_routes = build_topology_edge_routes(calibration_data)
    for route_name, route_data in calibration_data["routes"].items():
        route_data.update(edge_routes[route_data["topology"]][route_name])
        route_data["sumo_edges"] = route_data.pop("edges")
    calibration_data["job_id"] = job_id
    calibration_data["map_bboxes"] = {key: list(value) for key, value in bboxes.items()}
    calibration_data["hcm_flow_rates_veh_per_hour_per_lane"] = tomtom_config[
        "hcm_flow_rates_veh_per_hour_per_lane"
    ]
    calibration_data["calibration_flow"] = tomtom_config.get("calibration_flow", "medium_flow")
    with OUTPUT_CALIBRATION_PATH.open("w", encoding="utf-8") as output:
        json.dump(calibration_data, output, indent=2)
    print(f"[OK] Traffic Stats results saved to {OUTPUT_CALIBRATION_PATH}")
    print(f"[INFO] TomTom job ID: {job_id}; date: {selected_date}; routes: {len(calibration_data['routes'])}")


if __name__ == "__main__":
    main()