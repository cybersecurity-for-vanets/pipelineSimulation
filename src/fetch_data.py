import json
import argparse
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests

try:
    from .tomtom_traffic_stats import (
        build_multiday_payloads,
        build_route_analysis_payload,
        create_and_wait_for_route_analysis,
        normalize_multiday_analysis,
        normalize_route_analysis,
        open_download_route_analysis,
        wait_for_route_analysis,
    )
    from .sumo_route_utils import _shape_points, build_topology_edge_routes
except ImportError:
    from tomtom_traffic_stats import (
        build_multiday_payloads,
        build_route_analysis_payload,
        create_and_wait_for_route_analysis,
        normalize_multiday_analysis,
        normalize_route_analysis,
        open_download_route_analysis,
        wait_for_route_analysis,
    )
    from sumo_route_utils import _shape_points, build_topology_edge_routes


BASE_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = BASE_DIR / "conf"
OSM_DIR = BASE_DIR / "osmFiles"
TOMTOM_CONF_PATH = CONF_DIR / "tomtom.json"
ROUTES_CONF_PATH = CONF_DIR / "routes.json"
SCENARIOS_CONF_PATH = CONF_DIR / "coordinates_scenaries.json"
OUTPUT_CALIBRATION_PATH = CONF_DIR / "tomtom_calibrated_rates.json"
MULTIDAY_OUTPUT_PATH = CONF_DIR / "tomtom_multiday.json"
MAP_MARGIN_KM = 2.0
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
OSM_USER_AGENT = "pipelineSimulation/1.0 (SUMO Krauss calibration)"
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
            for longitude, latitude in _shape_points(segment.get("shape_lon_lat")):
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
    session.headers.update({"User-Agent": OSM_USER_AGENT})
    min_lat, min_lon, max_lat, max_lon = bbox
    osm_path = OSM_DIR / f"{TOPOLOGY_FILE_NAMES.get(topology, topology)}.osm"
    bbox_string = f"{min_lon},{min_lat},{max_lon},{max_lat}"
    map_url = f"https://api.openstreetmap.org/api/0.6/map?bbox={bbox_string}"

    try:
        response = session.get(map_url, timeout=60)
    except requests.RequestException:
        response = None
    if response is None or response.status_code != 200 or not response.content:
        query = (
            f'[out:xml][timeout:180];(way["highway"]({min_lat},{min_lon},{max_lat},{max_lon});>;);out body;'
        )
        response = None
        for attempt in range(3):
            for endpoint in OVERPASS_ENDPOINTS:
                try:
                    response = session.get(endpoint, params={"data": query}, timeout=200)
                except requests.RequestException:
                    response = None
                    continue
                if response.status_code == 200 and response.content:
                    break
                print(f"[OSM] {topology}: {endpoint} returned HTTP {response.status_code}; retrying")
            else:
                time.sleep(10 * (attempt + 1))
                continue
            break
        if response is None:
            raise RuntimeError(f"All OSM endpoints failed for topology '{topology}'")
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
        # Only OSM-mapped traffic signals: --tls.guess added uncoordinated 90 s signals
        # that made free-flow speed slower than TomTom on several routes.
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=BASE_DIR)
    if result.returncode != 0:
        raise RuntimeError(f"netconvert failed for '{topology}': {result.stderr.strip()}")
    print(f"[SUMO] {topology}: network saved to {net_path}")
    return net_path


def save_raw_job(job_id, result):
    """Grava o bruto como conf/jobs_<jobId>_results_<jobName>.json, o mesmo padrão dos arquivos baixados à mão."""
    job_name = str(result.get("jobName", "krauss")).replace(".", "_")
    file_name = f"jobs_{job_id}_results_{job_name}.json"
    path = CONF_DIR / file_name
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Bruto do job {job_id} salvo em {path}")
    return file_name


def print_multiday_dry_run(payloads):
    dates_by_job = [[item["from"] for item in payload["dateRanges"]] for payload in payloads]
    total_dates = sum(len(job_dates) for job_dates in dates_by_job)
    print(f"[DRY RUN] Janela: {dates_by_job[0][0]} a {dates_by_job[-1][-1]} ({total_dates} datas)")
    print(f"[DRY RUN] Jobs: {len(payloads)}")
    for index, job_dates in enumerate(dates_by_job, start=1):
        print(f"[DRY RUN] Job {index}: {len(job_dates)} datas ({job_dates[0]} a {job_dates[-1]})")
    print(f"[DRY RUN] Rotas: {len(payloads[0]['routes'])}")
    print(f"[DRY RUN] Faixas horárias: {len(payloads[0]['timeSets'])}")
    for route in payloads[0]["routes"]:
        print(f"[DRY RUN] {route['name']}")


def recover_route_analysis(job_id):
    """Baixa o resultado de um job já criado na TomTom e grava o bruto, sem criar outro job."""
    print(f"Recuperando job {job_id} já criado na TomTom")
    result = wait_for_route_analysis(job_id)
    raw_file = save_raw_job(job_id, result)
    return {"job_id": job_id, "raw_file": raw_file, "result": result}


def run_multiday(args, routes_config, tomtom_config):
    """Coleta a janela inteira e grava tomtom_multiday.json.

    Não baixa o OSM, não roda netconvert e não altera tomtom_calibrated_rates.json: rodar a rede de novo
    pode mudar o OSM e invalidar os tls_offsets e as calibrações de fluxo e Krauss.

    Com --job-id, os primeiros payloads são recuperados dos jobs informados (na ordem da janela) e só os
    restantes são criados. Assim, um job que caiu no meio não é refeito.
    """
    print("=== Etapa 1.1 (multi-dia): TomTom Traffic Stats para a janela de tomtom.json ===")
    if args.dry_run:
        print_multiday_dry_run(build_multiday_payloads(routes_config, tomtom_config))
        return

    jobs = []
    if args.file:
        for file_name in args.file:
            print(f"Abrindo bruto: {file_name}")
            job_id, result = open_download_route_analysis(f"{CONF_DIR}/{file_name}")
            jobs.append({"job_id": job_id, "raw_file": Path(file_name).name, "result": result})
    else:
        # Cada job é salvo antes do próximo; se um falhar, os anteriores podem ser reabertos com -f.
        recovered_ids = args.job_id or []
        for index, payload in enumerate(build_multiday_payloads(routes_config, tomtom_config)):
            if index < len(recovered_ids):
                jobs.append(recover_route_analysis(recovered_ids[index]))
                continue
            job_id, result = create_and_wait_for_route_analysis(routes_config, tomtom_config, payload=payload)
            raw_file = save_raw_job(job_id, result)
            jobs.append({"job_id": job_id, "raw_file": raw_file, "result": result})

    data = normalize_multiday_analysis(
        jobs,
        routes_config,
        tomtom_config.get("time_zone", "America/Sao_Paulo"),
    )
    with MULTIDAY_OUTPUT_PATH.open("w", encoding="utf-8") as output:
        json.dump(data, output, indent=2, ensure_ascii=False)
    print(f"[OK] Dados multi-dia salvos em {MULTIDAY_OUTPUT_PATH}")
    print(
        f"[INFO] Jobs: {', '.join(job['job_id'] for job in jobs)}; "
        f"datas: {data['date_from']} a {data['date_to']}; rotas: {len(data['routes'])}"
    )


def main():
    parser = argparse.ArgumentParser(description="Fetch one historical TomTom Traffic Stats day for the target routes")
    parser.add_argument("--dry-run", action="store_true", help="Print the request summary without network calls")
    parser.add_argument("--multi-day", action="store_true",
                        help="Collect every date in tomtom.json's window and write conf/tomtom_multiday.json")
    parser.add_argument("-f", "--file", nargs="+", help="Raw result file(s) in conf/ to open instead of calling the API")
    parser.add_argument("--job-id", nargs="+",
                        help="Jobs já criados na TomTom para recuperar, na ordem da janela (só com --multi-day). "
                             "Os payloads sem jobId são criados normalmente")
    args = parser.parse_args()

    if args.job_id and not args.multi_day:
        parser.error("--job-id só funciona com --multi-day")
    if args.job_id and args.file:
        parser.error("--job-id e -f não podem ser usados juntos")

    tomtom_config = load_json(TOMTOM_CONF_PATH)
    routes_config = load_json(ROUTES_CONF_PATH)

    if args.multi_day:
        if args.job_id and len(args.job_id) > len(build_multiday_payloads(routes_config, tomtom_config)):
            parser.error("Há mais --job-id do que jobs na janela")
        run_multiday(args, routes_config, tomtom_config)
        return
    if args.file and len(args.file) > 1:
        parser.error("Vários arquivos em -f só funcionam com --multi-day")

    if args.dry_run:
        payload = build_route_analysis_payload(routes_config, tomtom_config)
        print(f"[DRY RUN] Date: {payload['dateRanges'][0]['from']}")
        print(f"[DRY RUN] Time zone: {payload['routes'][0]['zoneId']}")
        print(f"[DRY RUN] Routes: {len(payload['routes'])}")
        print(f"[DRY RUN] Hourly time sets: {len(payload['timeSets'])}")
        for route in payload["routes"]:
            print(f"[DRY RUN] {route['name']}")
        return

    print("=== Stage 1.1: TomTom Traffic Stats for target routes ===")
    if args.file is None:
        job_id, route_analysis = create_and_wait_for_route_analysis(
            routes_config,
            tomtom_config,
        )
    else:
        print(f"Open JSON file: {args.file[0]}")
        job_id, route_analysis = open_download_route_analysis(f"{CONF_DIR}/{args.file[0]}")

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