import json
import math
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import optuna

try:
    from .generate_routes import DEFAULT_KRAUSS, build_route_file, safe_id
    from .sumo_route_utils import network_path
except ImportError:
    from generate_routes import DEFAULT_KRAUSS, build_route_file, safe_id
    from sumo_route_utils import network_path


BASE_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = BASE_DIR / "conf"
RAW_DATA_DIR = BASE_DIR / "results" / "rawData"
CALIBRATION_DATA_PATH = CONF_DIR / "tomtom_calibrated_rates.json"
KRAUSS_OUTPUT_PATH = CONF_DIR / "krauss_calibration.json"
N_TRIALS = 50
SIMULATION_SECONDS = 3600
SUMO_BINARY = os.environ.get("SUMO_BINARY") or shutil.which("sumo")


def load_calibration_data():
    if not CALIBRATION_DATA_PATH.is_file():
        raise FileNotFoundError(f"Traffic Stats data not found: {CALIBRATION_DATA_PATH}; run fetch_data.py first")
    with CALIBRATION_DATA_PATH.open("r", encoding="utf-8") as source:
        return json.load(source)


def read_trip_speeds(tripinfo_path, route_names):
    distance_by_route = {name: 0.0 for name in route_names}
    duration_by_route = {name: 0.0 for name in route_names}
    root = ET.parse(tripinfo_path).getroot()
    flow_to_route = {f"flow_{safe_id(name)}": name for name in route_names}

    for trip in root.findall("tripinfo"):
        flow_id = trip.get("id", "").rsplit(".", 1)[0]
        route_name = flow_to_route.get(flow_id)
        if route_name is None:
            continue
        duration = float(trip.get("duration", 0))
        distance = float(trip.get("routeLength", 0))
        if duration > 0 and distance > 0:
            distance_by_route[route_name] += distance
            duration_by_route[route_name] += duration

    return {
        route_name: distance / duration_by_route[route_name]
        for route_name, distance in distance_by_route.items()
        if distance > 0 and duration_by_route[route_name] > 0
    }


def run_sumo_trial(topology, routes, target_speeds, calibration_data, krauss_params):
    if not SUMO_BINARY:
        raise FileNotFoundError("SUMO binary not found; set SUMO_BINARY or add sumo to PATH")

    flow = calibration_data["calibration_flow"]
    if flow not in calibration_data["hcm_flow_rates_veh_per_hour_per_lane"]:
        raise ValueError(f"Unknown calibration_flow '{flow}' in Traffic Stats configuration")

    flow_rate = calibration_data["hcm_flow_rates_veh_per_hour_per_lane"][flow]
    route_xml = build_route_file(
        topology,
        routes,
        flow_rate,
        krauss_params,
    )
    net_file = network_path(topology)
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"krauss_{topology}_", dir=RAW_DATA_DIR) as tmp_dir:
        tmp_path = Path(tmp_dir)
        route_file = tmp_path / "calibration.rou.xml"
        tripinfo_file = tmp_path / "tripinfo.xml"
        route_file.write_text(route_xml, encoding="utf-8")

        command = [
            SUMO_BINARY,
            "--net-file", str(net_file),
            "--route-files", str(route_file),
            "--tripinfo-output", str(tripinfo_file),
            "--end", str(SIMULATION_SECONDS),
            "--seed", "1",
            "--no-step-log", "true",
            "--duration-log.disable", "true",
        ]
        result = subprocess.run(command, capture_output=True, text=True, cwd=BASE_DIR)
        if result.returncode != 0:
            raise RuntimeError(f"SUMO calibration trial failed: {result.stderr.strip()}")

        simulated_speeds = read_trip_speeds(tripinfo_file, target_speeds)

    common_routes = sorted(set(target_speeds) & set(simulated_speeds))
    if not common_routes:
        return math.inf, 0

    squared_errors = [
        (target_speeds[name] - simulated_speeds[name]) ** 2
        for name in common_routes
    ]
    return math.sqrt(sum(squared_errors) / len(squared_errors)), len(common_routes)


def calibrate_topology(topology, calibration_data):
    topology_routes = {
        name: route
        for name, route in calibration_data["routes"].items()
        if route["topology"] == topology
    }
    target_speeds = {
        name: route["mean_harmonic_speed_24h_kmh"] / 3.6
        for name, route in topology_routes.items()
        if route.get("mean_harmonic_speed_24h_kmh", 0) > 0 and route.get("sumo_edges")
    }
    if not target_speeds:
        raise ValueError(f"No valid hourly TomTom target speeds or SUMO edge paths for '{topology}'")

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=1),
    )

    def objective(trial):
        parameters = {
            "accel": trial.suggest_float("accel", 1.0, 3.5),
            "decel": trial.suggest_float("decel", 1.5, 4.5),
            "tau": trial.suggest_float("tau", 0.5, 2.0),
            "sigma": trial.suggest_float("sigma", 0.0, 1.0),
            "minGap": trial.suggest_float("minGap", 1.0, 3.5),
        }
        rmse, matched_routes = run_sumo_trial(
            topology,
            topology_routes,
            target_speeds,
            calibration_data,
            parameters,
        )
        trial.set_user_attr("matched_routes", matched_routes)
        return rmse

    study.optimize(objective, n_trials=N_TRIALS, n_jobs=1)
    best = study.best_trial
    if not math.isfinite(best.value):
        raise RuntimeError(f"No SUMO trips completed for topology '{topology}' during calibration")

    return {
        **best.params,
        "rmse_mps": best.value,
        "matched_routes": best.user_attrs.get("matched_routes", 0),
        "calibration_flow": calibration_data["calibration_flow"],
        "trials": N_TRIALS,
    }


def main():
    calibration_data = load_calibration_data()
    output = {
        "metadata": {
            "date": calibration_data["date"],
            "time_zone": calibration_data["time_zone"],
            "calibration_flow": calibration_data["calibration_flow"],
            "target": "mean harmonic speed over available hourly Traffic Stats sets",
        }
    }

    topologies = sorted({route["topology"] for route in calibration_data["routes"].values()})
    for topology in topologies:
        print(f"[KRAUSS] Calibrating {topology} on {calibration_data['calibration_flow']}...")
        output[topology] = calibrate_topology(topology, calibration_data)
        KRAUSS_OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(f"[OK] {topology}: RMSE={output[topology]['rmse_mps']:.3f} m/s")

    print(f"[OK] Krauss profiles saved to {KRAUSS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()