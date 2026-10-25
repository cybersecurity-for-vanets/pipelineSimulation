import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import optuna

try:
    from .generate_routes import DEFAULT_KRAUSS, SUMO_STEP_LENGTH, build_route_file, safe_id, tls_offsets_path
    from .sumo_route_utils import network_path
except ImportError:
    from generate_routes import DEFAULT_KRAUSS, SUMO_STEP_LENGTH, build_route_file, safe_id, tls_offsets_path
    from sumo_route_utils import network_path


BASE_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = BASE_DIR / "conf"
RAW_DATA_DIR = BASE_DIR / "results" / "rawData"
CALIBRATION_DATA_PATH = CONF_DIR / "tomtom_calibrated_rates.json"
KRAUSS_OUTPUT_PATH = CONF_DIR / "krauss_calibration.json"
ROUTE_FLOWS_OUTPUT_PATH = CONF_DIR / "route_flows_calibrated.json"
# Signalized topologies: traffic light offsets coordinated along the TomTom routes (green wave),
# then the route flow is calibrated to the TomTom speed (option A).
SIGNAL_COORDINATED_TOPOLOGIES = ("urban_road", "intersection")
# Topologies whose free-flow speed is above TomTom: flow fixed at the HCM calibration flow and
# speedFactor/speedDev added to the Krauss search (option B). See docs/calibracao_etapa1.md.
SPEED_FACTOR_TOPOLOGIES = ("highway",)
# Concurrent SUMO processes.
MAX_WORKERS = 6
# Krauss studies run per topology in parallel, each with this many concurrent trials.
KRAUSS_JOBS_PER_TOPOLOGY = 2
# Route flow search interval, total veh/h; the upper bound is per lane of the route's narrowest edge.
FLOW_MIN_VEH_H = 50
FLOW_MAX_VEH_H_PER_LANE = 2000
# Bisection stops when |v_sim - v_tomtom| <= FLOW_TOLERANCE_KMH or after FLOW_MAX_ITERATIONS runs.
FLOW_TOLERANCE_KMH = 1.0
FLOW_MAX_ITERATIONS = 12
# Seeds used to measure the dispersion of the speed at the calibrated flow.
STABILITY_SEEDS = (1, 2, 3)
N_TRIALS = 25
# Stop a topology's search once RMSE / mean target speed reaches this relative error.
TARGET_RELATIVE_RMSE = 0.10
# TPE samples the first N_STARTUP_TRIALS at random; only trials after them are guided by the model.
N_STARTUP_TRIALS = 10
# Early stop: the best trial unchanged for STAGNATION_PATIENCE consecutive guided trials.
STAGNATION_PATIENCE = 5
SIMULATION_SECONDS = 3600
SUMO_BINARY = os.environ.get("SUMO_BINARY") or shutil.which("sumo")

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


def read_insertion_stats(tripinfo_path, statistic_path):
    """Insertion backlog and teleports of one run (audit item 1.2)."""
    trips = ET.parse(tripinfo_path).getroot().findall("tripinfo")
    delays = [float(trip.get("departDelay", 0)) for trip in trips]
    statistics_root = ET.parse(statistic_path).getroot()
    vehicles = statistics_root.find("vehicles")
    teleports = statistics_root.find("teleports")
    return {
        "loaded": int(vehicles.get("loaded", 0)),
        "inserted": int(vehicles.get("inserted", 0)),
        "waiting_at_end": int(vehicles.get("waiting", 0)),
        "teleports": int(teleports.get("total", 0)),
        "completed_trips": len(trips),
        # Delays up to one step are insertion-time quantization, not a queue.
        "delayed_departures": sum(1 for delay in delays if delay > SUMO_STEP_LENGTH),
        "max_depart_delay_s": max(delays, default=0.0),
    }


def run_sumo(topology, routes, flow_rate, krauss_params, route_names, route_flows=None, min_depart=0.0,
             seed=1, with_stats=False):
    """One SUMO hour; returns {route_name: mean space speed in m/s} for route_names.

    With with_stats, returns (speeds, insertion stats) instead.
    """
    if not SUMO_BINARY:
        raise FileNotFoundError("SUMO binary not found; set SUMO_BINARY or add sumo to PATH")

    route_xml = build_route_file(topology, routes, flow_rate, krauss_params, route_flows)
    net_file = network_path(topology)
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"krauss_{topology}_", dir=RAW_DATA_DIR) as tmp_dir:
        tmp_path = Path(tmp_dir)
        route_file = tmp_path / "calibration.rou.xml"
        tripinfo_file = tmp_path / "tripinfo.xml"
        statistic_file = tmp_path / "statistics.xml"
        route_file.write_text(route_xml, encoding="utf-8")

        command = [
            SUMO_BINARY,
            "--net-file", str(net_file),
            "--route-files", str(route_file),
            "--tripinfo-output", str(tripinfo_file),
            "--statistic-output", str(statistic_file),
            "--end", str(SIMULATION_SECONDS),
            "--step-length", str(SUMO_STEP_LENGTH),
            "--seed", str(seed),
            "--no-step-log", "true",
            "--duration-log.disable", "true",
        ]
        if tls_offsets_path(topology).is_file():
            command += ["--additional-files", str(tls_offsets_path(topology))]
        result = subprocess.run(command, capture_output=True, text=True, cwd=BASE_DIR)
        if result.returncode != 0:
            raise RuntimeError(f"SUMO calibration trial failed: {result.stderr.strip()}")

        speeds = read_trip_speeds(tripinfo_file, route_names, min_depart)
        if with_stats:
            return speeds, read_insertion_stats(tripinfo_file, statistic_file)
        return speeds


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


def run_sumo_trial(topology, routes, target_speeds, calibration_data, krauss_params, route_flows=None):
    """One run at the calibrated route flows (HCM flow rate if absent) against the TomTom 24 h mean speed."""
    simulated_speeds = run_sumo(
        topology, routes, calibration_flow_rate(calibration_data), krauss_params, target_speeds, route_flows,
    )
    return rmse(target_speeds, simulated_speeds)


_bottleneck_cache = {}
_bottleneck_lock = threading.Lock()


def bottleneck_lanes(topology, edges):
    """Lane count of the narrowest edge of a route in the topology's SUMO network."""
    with _bottleneck_lock:
        if topology not in _bottleneck_cache:
            root = ET.parse(network_path(topology)).getroot()
            _bottleneck_cache[topology] = {
                edge.get("id"): len(edge.findall("lane"))
                for edge in root.findall("edge")
                if edge.get("function") != "internal"
            }
    return min(_bottleneck_cache[topology][edge] for edge in edges)


def sumo_tools_dir():
    if os.environ.get("SUMO_HOME"):
        return Path(os.environ["SUMO_HOME"]) / "tools"
    return Path(SUMO_BINARY).resolve().parent.parent / "tools"


def coordinate_traffic_lights(calibration_data):
    """Green-wave offsets along the TomTom routes with SUMO's tlsCoordinator.py.

    Without them every netconvert program starts its 90 s cycle at t=0, and the
    free-flow speed on signalized routes stays below the TomTom speed at any flow.
    """
    topologies = sorted({route["topology"] for route in calibration_data["routes"].values()})
    for topology in topologies:
        offsets_path = tls_offsets_path(topology)
        offsets_path.unlink(missing_ok=True)
        if topology not in SIGNAL_COORDINATED_TOPOLOGIES:
            continue

        routes = {
            name: route for name, route in calibration_data["routes"].items()
            if route["topology"] == topology and route.get("sumo_edges")
        }
        # Equal flows: every TomTom route weighs the same when the coordinator orders signal pairs.
        route_xml = build_route_file(topology, routes, 0, DEFAULT_KRAUSS, {name: 1 for name in routes})
        with tempfile.TemporaryDirectory(prefix=f"tls_{topology}_") as tmp_dir:
            route_file = Path(tmp_dir) / "coordination.rou.xml"
            route_file.write_text(route_xml, encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(sumo_tools_dir() / "tlsCoordinator.py"),
                "--net-file", str(network_path(topology)),
                "--route-file", str(route_file),
                "--output-file", str(offsets_path),
            ], capture_output=True, text=True, cwd=BASE_DIR)
        if result.returncode != 0:
            raise RuntimeError(f"tlsCoordinator failed for '{topology}': {result.stderr.strip()}")
        print(f"[SEMÁFOROS] {topology}: offsets coordenados salvos em {offsets_path}")


def calibrate_route_flow(route_name, route, target_speed, krauss_params):
    """Total route flow (veh/h) whose simulated speed matches the TomTom 24 h mean speed.

    Speed decreases with flow, so the target is first bracketed by the interval ends and
    then bisected in log scale. A target outside the bracket is reported as unreachable.
    """
    topology = route["topology"]
    lanes = bottleneck_lanes(topology, route["sumo_edges"])
    evaluations = {}

    def evaluate(flow):
        speeds, stats = run_sumo(
            topology, {route_name: route}, 0, krauss_params, [route_name],
            route_flows={route_name: flow}, with_stats=True,
        )
        evaluations[flow] = (speeds.get(route_name, 0.0), stats)
        return evaluations[flow][0]

    search_interval = [FLOW_MIN_VEH_H, FLOW_MAX_VEH_H_PER_LANE * lanes]
    low, high = search_interval
    low_speed, high_speed = evaluate(low), evaluate(high)
    tolerance = FLOW_TOLERANCE_KMH / 3.6

    if target_speed >= low_speed:
        reason = "free_flow_slower_than_target"
    elif target_speed <= high_speed:
        reason = "target_below_speed_at_max_flow"
    else:
        reason = "iteration_budget"
        while len(evaluations) < FLOW_MAX_ITERATIONS and high - low > 10:
            middle = round(math.sqrt(low * high))
            speed = evaluate(middle)
            if abs(speed - target_speed) <= tolerance:
                reason = "converged"
                break
            if speed > target_speed:
                low = middle
            else:
                high = middle

    best_flow = min(evaluations, key=lambda flow: abs(evaluations[flow][0] - target_speed))
    best_speed, best_stats = evaluations[best_flow]
    seed_speeds = [best_speed] + [
        run_sumo(topology, {route_name: route}, 0, krauss_params, [route_name],
                 route_flows={route_name: best_flow}, seed=seed).get(route_name, 0.0)
        for seed in STABILITY_SEEDS[1:]
    ]

    return {
        "method": "tomtom_speed",
        "flow_veh_h": best_flow,
        "target_kmh": target_speed * 3.6,
        "simulated_kmh": best_speed * 3.6,
        "error_kmh": (best_speed - target_speed) * 3.6,
        "reachable": reason in ("converged", "iteration_budget"),
        "stop_reason": reason,
        "trials": len(evaluations),
        "search_interval_veh_h": search_interval,
        "bottleneck_lanes": lanes,
        "speed_at_min_flow_kmh": low_speed * 3.6,
        "speed_at_max_flow_kmh": high_speed * 3.6,
        "insertion": best_stats,
        "seed_speeds_kmh": [speed * 3.6 for speed in seed_speeds],
        "seed_stdev_kmh": statistics.stdev(seed_speeds) * 3.6,
    }


def calibrate_route_flows(calibration_data):
    routes = {
        name: route for name, route in calibration_data["routes"].items()
        if route.get("sumo_edges") and route.get("mean_harmonic_speed_24h_kmh", 0) > 0
    }

    def calibrate(name):
        route = routes[name]
        if route["topology"] in SPEED_FACTOR_TOPOLOGIES:
            lanes = bottleneck_lanes(route["topology"], route["sumo_edges"])
            result = {
                "method": "hcm_fixed",
                "flow_veh_h": calibration_flow_rate(calibration_data) * lanes,
                "target_kmh": route["mean_harmonic_speed_24h_kmh"],
                "calibration_flow": calibration_data["calibration_flow"],
                "bottleneck_lanes": lanes,
            }
            print(f"[FLUXO] {name}: fluxo HCM fixo {result['flow_veh_h']} veh/h "
                  f"({calibration_data['calibration_flow']} × {lanes} faixas no gargalo)")
            return name, result

        print(f"[FLUXO] Calibrando {name}...")
        result = calibrate_route_flow(name, route, route["mean_harmonic_speed_24h_kmh"] / 3.6, DEFAULT_KRAUSS)
        status = "OK" if result["reachable"] else "AVISO"
        print(f"[{status}] {name}: {result['flow_veh_h']} veh/h -> {result['simulated_kmh']:.1f} km/h "
              f"(alvo {result['target_kmh']:.1f}, {result['stop_reason']}, {result['trials']} execuções)")
        return name, result

    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        results = dict(pool.map(calibrate, sorted(routes)))

    output = {
        "metadata": {
            "date": calibration_data["date"],
            "time_zone": calibration_data["time_zone"],
            "target": "TomTom 24 h mean harmonic speed per route, Krauss fixed at DEFAULT_KRAUSS",
            "krauss": DEFAULT_KRAUSS,
            "flow_tolerance_kmh": FLOW_TOLERANCE_KMH,
            "signal_coordinated_topologies": list(SIGNAL_COORDINATED_TOPOLOGIES),
            "speed_factor_topologies": list(SPEED_FACTOR_TOPOLOGIES),
            "skipped_routes": calibration_data.get("skipped_routes", []),
        },
        "routes": results,
    }
    ROUTE_FLOWS_OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"[OK] Fluxos por rota salvos em {ROUTE_FLOWS_OUTPUT_PATH}")
    return output


def load_route_flows():
    if not ROUTE_FLOWS_OUTPUT_PATH.is_file():
        raise FileNotFoundError(f"Calibrated route flows not found: {ROUTE_FLOWS_OUTPUT_PATH}; "
                                "run calibrate_stage1.py --etapa fluxo first")
    with ROUTE_FLOWS_OUTPUT_PATH.open("r", encoding="utf-8") as source:
        return json.load(source)


def suggest_krauss_parameters(trial, with_speed_factor=False):
    parameters = {
        "accel": trial.suggest_float("accel", 1.0, 3.5),
        "decel": trial.suggest_float("decel", 1.5, 4.5),
        "tau": trial.suggest_float("tau", 0.5, 2.0),
        "sigma": trial.suggest_float("sigma", 0.0, 1.0),
        "minGap": trial.suggest_float("minGap", 1.0, 3.5),
    }
    if with_speed_factor:
        # Below 1 only: the vType maxSpeed is the speed limit, so SUMO caps any factor above it.
        parameters["speedFactor"] = trial.suggest_float("speedFactor", 0.5, 1.0)
        parameters["speedDev"] = trial.suggest_float("speedDev", 0.05, 0.3)
    return parameters


def calibrate_topology(topology, calibration_data, route_flows=None):
    topology_routes = {
        name: route
        for name, route in calibration_data["routes"].items()
        if route["topology"] == topology and route.get("sumo_edges")
    }
    target_speeds = {
        name: route["mean_harmonic_speed_24h_kmh"] / 3.6
        for name, route in topology_routes.items()
        if route.get("mean_harmonic_speed_24h_kmh", 0) > 0
    }
    if not target_speeds:
        raise ValueError(f"No valid TomTom target speeds or SUMO edge paths for '{topology}'")

    reference_speed = statistics.mean(target_speeds.values())

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=1, n_startup_trials=N_STARTUP_TRIALS),
    )
    # Route flows were calibrated with DEFAULT_KRAUSS: start there so the search can only improve on it.
    with_speed_factor = topology in SPEED_FACTOR_TOPOLOGIES
    study.enqueue_trial({**DEFAULT_KRAUSS, **({"speedFactor": 1.0, "speedDev": 0.1} if with_speed_factor else {})})

    stop = {"reason": None, "best_number": None, "streak": 0}
    # Callbacks run in the trial threads when n_jobs > 1.
    stop_lock = threading.Lock()

    def stop_at_target(study, trial):
        with stop_lock:
            if study.best_value / reference_speed <= TARGET_RELATIVE_RMSE:
                stop["reason"] = "target_relative_rmse"
                study.stop()
                return
            if trial.number < N_STARTUP_TRIALS:
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
        parameters = suggest_krauss_parameters(trial, with_speed_factor)
        error, matched = run_sumo_trial(
            topology, topology_routes, target_speeds, calibration_data, parameters, route_flows,
        )
        trial.set_user_attr("matched_routes", matched)
        return error

    study.optimize(objective, n_trials=N_TRIALS, n_jobs=KRAUSS_JOBS_PER_TOPOLOGY, callbacks=[stop_at_target])
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
    if topology in SPEED_FACTOR_TOPOLOGIES:
        result["calibration_mode"] = "hcm_flow_with_speed_factor_vs_tomtom_24h_mean_speed"
        result["route_flows_veh_h"] = {name: route_flows[name] for name in topology_routes if name in route_flows}
    elif route_flows:
        result["calibration_mode"] = "coordinated_tls_route_flow_vs_tomtom_24h_mean_speed"
        result["route_flows_veh_h"] = {name: route_flows[name] for name in topology_routes if name in route_flows}
    else:
        result["calibration_mode"] = "hcm_flow_vs_tomtom_24h_mean_speed"
        result["calibration_flow"] = calibration_data["calibration_flow"]
    return result


def calibrate_krauss(calibration_data, route_flow_data):
    route_flows = {name: route["flow_veh_h"] for name, route in route_flow_data["routes"].items()}
    output = {
        "metadata": {
            "date": calibration_data["date"],
            "time_zone": calibration_data["time_zone"],
            "route_flows": str(ROUTE_FLOWS_OUTPUT_PATH.name),
            "target": "TomTom 24 h mean harmonic speed at the calibrated route flows",
        }
    }
    output_lock = threading.Lock()

    def calibrate(topology):
        print(f"[KRAUSS] Calibrando {topology}...")
        result = calibrate_topology(topology, calibration_data, route_flows)
        with output_lock:
            output[topology] = result
            KRAUSS_OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
        status = "OK" if result["relative_rmse"] <= TARGET_RELATIVE_RMSE else "AVISO"
        print(f"[{status}] {topology} ({result['calibration_mode']}): RMSE={result['rmse_mps']:.3f} m/s "
              f"({result['relative_rmse']:.1%} da velocidade-alvo média, {result['trials']} trials)")

    topologies = sorted({route["topology"] for route in calibration_data["routes"].values()})
    with ThreadPoolExecutor(max(1, MAX_WORKERS // KRAUSS_JOBS_PER_TOPOLOGY)) as pool:
        list(pool.map(calibrate, topologies))

    print(f"[OK] Perfis Krauss salvos em {KRAUSS_OUTPUT_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Etapa 1: fluxo por rota e Krauss por topologia")
    parser.add_argument("--etapa", choices=("fluxo", "krauss", "tudo"), default="tudo",
                        help="fluxo: só fluxo por rota; krauss: Krauss com os fluxos já salvos; tudo: ambos")
    args = parser.parse_args()

    calibration_data = load_calibration_data()
    if args.etapa in ("fluxo", "tudo"):
        coordinate_traffic_lights(calibration_data)
        route_flow_data = calibrate_route_flows(calibration_data)
    else:
        route_flow_data = load_route_flows()
    if args.etapa in ("krauss", "tudo"):
        calibrate_krauss(calibration_data, route_flow_data)


if __name__ == "__main__":
    main()
