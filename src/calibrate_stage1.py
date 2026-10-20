import json
import math
import os
import shutil
import statistics
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import optuna

try:
    from .generate_routes import DEFAULT_KRAUSS, SUMO_STEP_LENGTH, build_route_file, safe_id
    from .reference_data import load_cet_hourly_flows, load_cet_hourly_speeds
    from .sumo_route_utils import network_path
except ImportError:
    from generate_routes import DEFAULT_KRAUSS, SUMO_STEP_LENGTH, build_route_file, safe_id
    from reference_data import load_cet_hourly_flows, load_cet_hourly_speeds
    from sumo_route_utils import network_path


BASE_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = BASE_DIR / "conf"
RAW_DATA_DIR = BASE_DIR / "results" / "rawData"
CALIBRATION_DATA_PATH = CONF_DIR / "tomtom_calibrated_rates.json"
KRAUSS_OUTPUT_PATH = CONF_DIR / "krauss_calibration.json"
N_TRIALS = 25
# Stop a topology's search once RMSE / mean target speed reaches this relative error.
TARGET_RELATIVE_RMSE = 0.10
# Early stop: the best trial unchanged for STAGNATION_PATIENCE consecutive trials, checked only
# while the trial number is below STAGNATION_TRIAL_LIMIT.
STAGNATION_PATIENCE = 5
STAGNATION_TRIAL_LIMIT = 15
SIMULATION_SECONDS = 3600
WARMUP_SECONDS = 600
MAX_LANE_CAPACITY_PCE = 2400
SUMO_WORKERS = int(os.environ.get("SUMO_WORKERS", min(4, os.cpu_count() or 1)))
SUMO_BINARY = os.environ.get("SUMO_BINARY") or shutil.which("sumo")

if SUMO_WORKERS < 1:
    raise ValueError("SUMO_WORKERS must be at least 1")

def load_calibration_data():
    if not CALIBRATION_DATA_PATH.is_file():
        raise FileNotFoundError(f"Traffic Stats data not found: {CALIBRATION_DATA_PATH}; run fetch_data.py first")
    with CALIBRATION_DATA_PATH.open("r", encoding="utf-8") as source:
        return json.load(source)


def read_trip_speeds(tripinfo_path, route_names, min_depart=0.0):
    distance_by_route = {name: 0.0 for name in route_names}
    duration_by_route = {name: 0.0 for name in route_names}
    root = ET.parse(tripinfo_path).getroot()
    flow_to_route = {f"flow_{safe_id(name)}": name for name in route_names}

    for trip in root.findall("tripinfo"):
        flow_id = trip.get("id", "").rsplit(".", 1)[0]
        route_name = flow_to_route.get(flow_id)
        if route_name is None:
            continue
        if float(trip.get("depart", 0)) < min_depart:
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


def run_sumo(topology, routes, flow_rate, krauss_params, route_names, route_flows=None, min_depart=0.0):
    """One SUMO hour; returns {route_name: mean space speed in m/s} for route_names."""
    if not SUMO_BINARY:
        raise FileNotFoundError("SUMO binary not found; set SUMO_BINARY or add sumo to PATH")

    route_xml = build_route_file(topology, routes, flow_rate, krauss_params, route_flows)
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
            "--step-length", str(SUMO_STEP_LENGTH),
            "--seed", "1",
            "--no-step-log", "true",
            "--duration-log.disable", "true",
        ]
        result = subprocess.run(command, capture_output=True, text=True, cwd=BASE_DIR)
        if result.returncode != 0:
            raise RuntimeError(f"SUMO calibration trial failed: {result.stderr.strip()}")

        return read_trip_speeds(tripinfo_file, route_names, min_depart)


def calibration_flow_rate(calibration_data):
    flow = calibration_data["calibration_flow"]
    if flow not in calibration_data["hcm_flow_rates_veh_per_hour_per_lane"]:
        raise ValueError(f"Unknown calibration_flow '{flow}' in Traffic Stats configuration")
    return calibration_data["hcm_flow_rates_veh_per_hour_per_lane"][flow]


def rmse(target_speeds, simulated_speeds):
    common_routes = sorted(set(target_speeds) & set(simulated_speeds))
    if not common_routes:
        return math.inf, 0
    squared_errors = [(target_speeds[name] - simulated_speeds[name]) ** 2 for name in common_routes]
    return math.sqrt(sum(squared_errors) / len(squared_errors)), len(common_routes)


def run_sumo_trial(topology, routes, target_speeds, calibration_data, krauss_params):
    """Fallback calibration: one run at the HCM flow rate against the 24 h mean speed."""
    simulated_speeds = run_sumo(
        topology, routes, calibration_flow_rate(calibration_data), krauss_params, target_speeds,
    )
    return rmse(target_speeds, simulated_speeds)


def hour_key(hour):
    return f"hour_{hour:02d}_{hour + 1:02d}"


def build_hourly_cases(topology_routes, cet_flows):
    """[(hour, {route: veh/h}, {route: TomTom km/h target})] using routes that have CET counts."""
    anchored = {name: cet_flows[name] for name in topology_routes if name in cet_flows}
    cases = []
    for hour in sorted({hour for flows in anchored.values() for hour in flows}):
        flows, targets = {}, {}
        for name, route_flows in anchored.items():
            speed = topology_routes[name].get("hourly_harmonic_speed_kmh", {}).get(hour_key(hour))
            if hour in route_flows and speed:
                flows[name] = route_flows[hour]
                targets[name] = speed / 3.6
        if targets:
            cases.append((hour, flows, targets))
    for hour, flows, _ in cases:
        for name, flow in flows.items():
            capacity = topology_routes[name].get("lanes_per_direction", 1) * MAX_LANE_CAPACITY_PCE
            if flow > capacity:
                print(
                    f"WARNING: CET flow {flow:.0f} veh/h on '{name}' at {hour:02d}h exceeds the "
                    f"~{capacity} veh/h capacity of its {topology_routes[name].get('lanes_per_direction', 1)} "
                    "SUMO lane(s); vehicles will queue at insertion. Check the route/lane mapping."
                )
    return cases


def run_hourly_cases(topology, topology_routes, cases, calibration_data, krauss_params, executor=None):
    """Simulate each case hour with the CET flows; returns [(hour, route, flow, target_mps, simulated_mps)]."""
    fallback_rate = calibration_flow_rate(calibration_data)
    tasks = [
        (topology, topology_routes, hour, flows, targets, fallback_rate, krauss_params)
        for hour, flows, targets in cases
    ]
    run_cases = executor.map if executor else map
    return [row for case_rows in run_cases(run_hourly_case, tasks) for row in case_rows]


def run_hourly_case(task):
    topology, topology_routes, hour, flows, targets, fallback_rate, krauss_params = task
    simulated = run_sumo(
        topology, topology_routes, fallback_rate, krauss_params, targets,
        route_flows=flows, min_depart=WARMUP_SECONDS,
    )
    return [
        (hour, name, flows[name], targets[name], simulated[name])
        for name in targets if name in simulated
    ]


def hourly_rmse(rows):
    if not rows:
        return math.inf, 0
    squared_errors = [(target - simulated) ** 2 for _, _, _, target, simulated in rows]
    return math.sqrt(sum(squared_errors) / len(squared_errors)), len(rows)


def suggest_krauss_parameters(trial):
    return {
        "accel": trial.suggest_float("accel", 1.0, 3.5),
        "decel": trial.suggest_float("decel", 1.5, 4.5),
        "tau": trial.suggest_float("tau", 0.5, 2.0),
        "sigma": trial.suggest_float("sigma", 0.0, 1.0),
        "minGap": trial.suggest_float("minGap", 1.0, 3.5),
    }


def validation_report(rows, cet_speeds):
    """Per route/hour: CET flow, TomTom target, simulated speed and (when mapped) CET GPS speed, in km/h."""
    return [
        {
            "route": route,
            "hour": hour,
            "cet_flow_pce_veh_h": round(flow, 1),
            "tomtom_kmh": round(target * 3.6, 2),
            "simulated_kmh": round(simulated * 3.6, 2),
            "cet_gps_kmh": cet_speeds.get(route, {}).get(hour),
        }
        for hour, route, flow, target, simulated in rows
    ]


def calibrate_topology(topology, calibration_data, executor=None):
    topology_routes = {
        name: route
        for name, route in calibration_data["routes"].items()
        if route["topology"] == topology and route.get("sumo_edges")
    }
    cases = build_hourly_cases(topology_routes, load_cet_hourly_flows())
    target_speeds = {
        name: route["mean_harmonic_speed_24h_kmh"] / 3.6
        for name, route in topology_routes.items()
        if route.get("mean_harmonic_speed_24h_kmh", 0) > 0
    }
    if not cases and not target_speeds:
        raise ValueError(f"No valid hourly TomTom target speeds or SUMO edge paths for '{topology}'")

    reference_speed = (
        statistics.mean(speed for _, _, targets in cases for speed in targets.values())
        if cases else statistics.mean(target_speeds.values())
    )

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=1),
    )

    stop = {"reason": None, "best_number": None, "streak": 0}

    def stop_at_target(study, trial):
        if study.best_value / reference_speed <= TARGET_RELATIVE_RMSE:
            stop["reason"] = "target_relative_rmse"
            study.stop()
            return
        if trial.number >= STAGNATION_TRIAL_LIMIT:
            return
        if study.best_trial.number == stop["best_number"]:
            stop["streak"] += 1
        else:
            stop["best_number"] = study.best_trial.number
            stop["streak"] = 0
        if stop["streak"] >= STAGNATION_PATIENCE:
            stop["reason"] = "best_trial_unchanged"
            study.stop()

    def objective(trial):
        parameters = suggest_krauss_parameters(trial)
        if cases:
            error, matched = hourly_rmse(
                run_hourly_cases(topology, topology_routes, cases, calibration_data, parameters, executor)
            )
        else:
            error, matched = run_sumo_trial(
                topology, topology_routes, target_speeds, calibration_data, parameters,
            )
        trial.set_user_attr("matched_routes", matched)
        return error

    study.optimize(objective, n_trials=N_TRIALS, n_jobs=1, callbacks=[stop_at_target])
    best = study.best_trial
    if not math.isfinite(best.value):
        raise RuntimeError(f"No SUMO trips completed for topology '{topology}' during calibration")

    result = {
        **best.params,
        "rmse_mps": best.value,
        "relative_rmse": best.value / reference_speed,
        "reference_speed_mps": reference_speed,
        "stopped_at_target": best.value / reference_speed <= TARGET_RELATIVE_RMSE,
        "stop_reason": stop["reason"] or "trial_budget",
        "matched_routes": best.user_attrs.get("matched_routes", 0),
        "trials": len(study.trials),
    }
    if cases:
        rows = run_hourly_cases(topology, topology_routes, cases, calibration_data, best.params, executor)
        result["calibration_mode"] = "hourly_cet_flows_vs_tomtom_hourly_speeds"
        result["calibration_hours"] = [hour for hour, _, _ in cases]
        result["validation"] = validation_report(rows, load_cet_hourly_speeds())
    else:
        result["calibration_mode"] = "hcm_flow_vs_tomtom_24h_mean_speed"
        result["calibration_flow"] = calibration_data["calibration_flow"]
    return result


def main():
    calibration_data = load_calibration_data()
    output = {
        "metadata": {
            "date": calibration_data["date"],
            "time_zone": calibration_data["time_zone"],
            "calibration_flow": calibration_data["calibration_flow"],
            "target": "TomTom hourly harmonic speed at the hours with CET counts; "
                      "topologies without CET counts use the 24 h mean at the HCM flow",
        }
    }

    topologies = sorted({route["topology"] for route in calibration_data["routes"].values()})
    for topology in topologies:
        print(f"[KRAUSS] Calibrating {topology}...")
        with ProcessPoolExecutor(max_workers=SUMO_WORKERS) as executor:
            output[topology] = calibrate_topology(topology, calibration_data, executor)
        KRAUSS_OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(f"[OK] {topology} ({output[topology]['calibration_mode']}): RMSE={output[topology]['rmse_mps']:.3f} m/s "
              f"({output[topology]['relative_rmse']:.1%} of mean target speed, {output[topology]['trials']} trials)")

    print(f"[OK] Krauss profiles saved to {KRAUSS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
