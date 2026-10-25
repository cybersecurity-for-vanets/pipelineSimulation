"""T6: comparabilidade SUMO × TomTom (comprimento, viagens incompletas, warm-up).

Roda o modelo calibrado atual (fluxo por rota, speedFactor, Krauss por topologia e tls_offsets), uma
simulação por topologia, e grava conf/calibration_checks.json com um registro por rota:
- razão routeLength do SUMO / distância da TomTom, contra checks.max_length_ratio_error;
- fração de viagens que não terminam dentro de simulation.seconds;
- velocidade das viagens completas (distância / tempo, como em run_sumo) com partidas a partir de cada
  corte de checks.cutoffs_s, o efeito do warm-up (corte simulation.warmup_s contra o corte 0) e a curva por janela.

Constantes vêm de conf/calibration_params.json.
"""
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from .params import PROJECT_DIR, load_calibration_params, KRAUSS_KEYS
except ImportError:
    from params import PROJECT_DIR, load_calibration_params, KRAUSS_KEYS

sys.path.insert(0, str(PROJECT_DIR / "src"))
from generate_routes import (  # noqa: E402
    DEFAULT_KRAUSS, SUMO_STEP_LENGTH, apply_route_speed_factors, build_route_file, safe_id, tls_offsets_path,
)
from sumo_route_utils import network_path  # noqa: E402


CONF_DIR = PROJECT_DIR / "conf"
CALIBRATION_PATH = CONF_DIR / "tomtom_calibrated_rates.json"
FLOWS_PATH = CONF_DIR / "route_flows_calibrated.json"
KRAUSS_PATH = CONF_DIR / "krauss_calibration.json"
MULTIDAY_PATH = CONF_DIR / "tomtom_multiday.json"
OUTPUT_PATH = CONF_DIR / "calibration_checks.json"
SUMO_BINARY = os.environ.get("SUMO_BINARY") or shutil.which("sumo")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def tomtom_distances(calibration):
    """Distância por rota (m) da TomTom: do tomtom_multiday.json, se existir, senão do bruto do job calibrado."""
    if MULTIDAY_PATH.is_file():
        multiday = load_json(MULTIDAY_PATH)
        return {name: route["distance_m"] for name, route in multiday["routes"].items() if route["distance_m"]}, MULTIDAY_PATH.name

    raw_files = sorted(CONF_DIR.glob(f"jobs_{calibration['job_id']}_results_*.json"))
    if not raw_files:
        raise FileNotFoundError(f"Nem tomtom_multiday.json nem bruto do job {calibration['job_id']} em {CONF_DIR}")
    distances = {}
    for route in load_json(raw_files[0])["routes"]:
        for summary in route.get("summaries", []):
            if float(summary.get("distance") or 0) > 0:
                distances[route["routeName"]] = float(summary["distance"])
                break
    return distances, raw_files[0].name


def run_topology(topology, routes, route_flows, krauss, seconds):
    """Uma simulação da topologia com o modelo calibrado; devolve as linhas de tripinfo, inclusive as incompletas."""
    if not SUMO_BINARY:
        raise FileNotFoundError("Binário do SUMO não encontrado; defina SUMO_BINARY ou coloque sumo no PATH")

    route_xml = build_route_file(topology, routes, 0, krauss, route_flows)
    with tempfile.TemporaryDirectory(prefix=f"checks_{topology}_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        route_file = tmp_path / "checks.rou.xml"
        tripinfo_file = tmp_path / "tripinfo.xml"
        route_file.write_text(route_xml, encoding="utf-8")

        command = [
            SUMO_BINARY,
            "--net-file", str(network_path(topology)),
            "--route-files", str(route_file),
            "--tripinfo-output", str(tripinfo_file),
            "--tripinfo-output.write-unfinished", "true",
            "--end", str(seconds),
            "--step-length", str(SUMO_STEP_LENGTH),
            "--seed", "1",
            "--no-step-log", "true",
            "--duration-log.disable", "true",
        ]
        if tls_offsets_path(topology).is_file():
            command += ["--additional-files", str(tls_offsets_path(topology))]
        result = subprocess.run(command, capture_output=True, text=True, cwd=PROJECT_DIR)
        if result.returncode != 0:
            raise RuntimeError(f"SUMO falhou na topologia {topology}: {result.stderr.strip()}")
        return ET.parse(tripinfo_file).getroot().findall("tripinfo")


def speed_between(trips, start_s, end_s):
    """Velocidade média espacial (km/h) das viagens completas que partem em [start_s, end_s)."""
    selected = [t for t in trips if start_s <= float(t.get("depart")) < end_s]
    duration = sum(float(t.get("duration")) for t in selected)
    if not selected or duration <= 0:
        return None
    return sum(float(t.get("routeLength")) for t in selected) / duration * 3.6


def route_check(name, all_trips, distance_m, flow_veh_h, params):
    rows = [t for t in all_trips if t.get("id", "").rsplit(".", 1)[0] == f"flow_{safe_id(name)}"]
    if not rows:
        return None
    seconds = params["simulation"]["seconds"]
    warmup = params["simulation"]["warmup_s"]
    checks = params["checks"]
    max_error = checks["max_length_ratio_error"]
    window = checks["window_s"]
    completed = [t for t in rows if float(t.get("arrival")) >= 0]

    length_m = statistics.median(float(t.get("routeLength")) for t in rows)
    ratio = length_m / distance_m if distance_m else None
    speeds = {cut: speed_between(completed, cut, seconds) for cut in sorted(set(checks["cutoffs_s"]) | {0, warmup})}
    warmup_speed = speeds[warmup]
    return {
        "flow_veh_h": flow_veh_h,
        "tomtom_distance_m": distance_m,
        "sumo_length_m": round(length_m, 1),
        "length_ratio": round(ratio, 3) if ratio else None,
        "length_ok": ratio is not None and abs(ratio - 1) <= max_error,
        "departed": len(rows),
        "completed": len(completed),
        "incomplete_fraction": round(1 - len(completed) / len(rows), 4),
        "speed_by_cutoff_kmh": {str(cut): round(value, 2) if value else None for cut, value in speeds.items()},
        "warmup_effect_kmh": round(warmup_speed - speeds[0], 2) if warmup_speed and speeds[0] else None,
        "profile_kmh": [
            round(value, 2) if value else None
            for value in (speed_between(completed, start, start + window) for start in range(0, seconds, window))
        ],
    }


def print_summary(report):
    print(f"Distância TomTom de: {report['distance_source']}; corte de warm-up: {report['warmup_s']} s")
    for name, route in report["routes"].items():
        status = "OK" if route["length_ok"] else "FORA"
        print(
            f"[{status}] {name:40s} comprimento {route['length_ratio']:.2f}x  "
            f"incompletas {route['incomplete_fraction'] * 100:4.1f}%  "
            f"warm-up {route['warmup_effect_kmh']:+.1f} km/h"
        )


def main():
    params = load_calibration_params()
    calibration = load_json(CALIBRATION_PATH)
    flows = load_json(FLOWS_PATH)
    profiles = load_json(KRAUSS_PATH)
    distances, distance_source = tomtom_distances(calibration)
    routes_all = apply_route_speed_factors(calibration["routes"], flows)

    report = {
        "distance_source": distance_source,
        "simulation_seconds": params["simulation"]["seconds"],
        "warmup_s": params["simulation"]["warmup_s"],
        "max_length_ratio_error": params["checks"]["max_length_ratio_error"],
        "routes": {},
    }
    for topology in sorted({route["topology"] for route in routes_all.values()}):
        routes = {name: route for name, route in routes_all.items() if route["topology"] == topology}
        krauss = {key: profiles.get(topology, DEFAULT_KRAUSS)[key] for key in KRAUSS_KEYS}
        route_flows = {name: flows["routes"][name]["flow_veh_h"] for name in routes}
        trips = run_topology(topology, routes, route_flows, krauss, params["simulation"]["seconds"])
        for name in routes:
            check = route_check(name, trips, distances.get(name), route_flows[name], params)
            if check is not None:
                report["routes"][name] = {"topology": topology, **check}

    OUTPUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Relatório de verificações salvo em {OUTPUT_PATH}")
    print_summary(report)


if __name__ == "__main__":
    main()
