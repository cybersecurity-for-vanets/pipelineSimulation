"""T2: bisseção por rota avaliada pela média de várias sementes, com aceite que considera a inserção.

Para cada rota e regime de conf/calibration_targets.json (T1), procura o fluxo (urban_road, intersection)
ou o speedFactor ao fluxo HCM fixo (highway) cuja velocidade média entre as sementes de replicas.seeds
reproduz o alvo. Cada avaliação roda todas as sementes; a decisão da bisseção e a escolha do valor final
usam a média, não uma semente só. A velocidade conta só as partidas a partir de simulation.warmup_s.

Fila pontual: o tempo de viagem inclui o departDelay. Nas rotas cuja primeira aresta é curta e termina em
semáforo, o veículo que espera inserção está, na prática, parado no primeiro vermelho; sem esse tempo a
velocidade fica alta demais e a bisseção empurra o fluxo para cima. O warm-up usa a partida pretendida
(depart - departDelay), que é quando o veículo chega à fila.

Uma rota só é marcada como convergida se |média - alvo| <= flow.tolerance_kmh e, em todas as sementes,
o maior atraso de partida <= acceptance.max_depart_delay_s (cerca de um ciclo de semáforo: a fila do
vermelho é esperada, uma fila que cresce não) e a fração de veículos ainda esperando inserção no fim
<= acceptance.max_waiting_fraction. Sem isso o fluxo efetivo é menor que o nominal.

Krauss fica em krauss.default e os offsets de semáforo são os já gravados em osmFiles/ pela Etapa 1.
Saída: conf/route_flows_replicated.json. Não altera route_flows_calibrated.json.
"""
import argparse
import json
import math
import statistics
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

try:
    from .params import PROJECT_DIR, load_calibration_params
except ImportError:
    from params import PROJECT_DIR, load_calibration_params

sys.path.insert(0, str(PROJECT_DIR / "src"))
import calibrate_stage1  # noqa: E402
from calibrate_stage1 import (  # noqa: E402
    MAX_WORKERS, bottleneck_lanes, calibration_flow_rate, load_calibration_data, run_sumo,
)
from generate_routes import safe_id  # noqa: E402


CONF_DIR = PROJECT_DIR / "conf"
TARGETS_PATH = CONF_DIR / "calibration_targets.json"
OUTPUT_PATH = CONF_DIR / "route_flows_replicated.json"

# Sementes de todas as avaliações em andamento dividem os mesmos processos SUMO.
_sumo_pool = ThreadPoolExecutor(MAX_WORKERS)


def read_trip_speeds_with_queue(tripinfo_path, route_names, min_depart=0.0):
    """Como calibrate_stage1.read_trip_speeds, com o departDelay no tempo de viagem (fila pontual)."""
    distance_by_route = {name: 0.0 for name in route_names}
    duration_by_route = {name: 0.0 for name in route_names}
    flow_to_route = {f"flow_{safe_id(name)}": name for name in route_names}

    for trip in ET.parse(tripinfo_path).getroot().findall("tripinfo"):
        route_name = flow_to_route.get(trip.get("id", "").rsplit(".", 1)[0])
        if route_name is None:
            continue
        delay = float(trip.get("departDelay", 0))
        if float(trip.get("depart", 0)) - delay < min_depart:
            continue
        duration = float(trip.get("duration", 0)) + delay
        distance = float(trip.get("routeLength", 0))
        if duration > 0 and distance > 0:
            distance_by_route[route_name] += distance
            duration_by_route[route_name] += duration

    return {
        route_name: distance / duration_by_route[route_name]
        for route_name, distance in distance_by_route.items()
        if distance > 0 and duration_by_route[route_name] > 0
    }


def waiting_fraction(stats):
    """Fração dos veículos carregados que ainda esperam inserção no fim da simulação."""
    return stats["waiting_at_end"] / stats["loaded"] if stats["loaded"] else 0.0


def insertion_ok(stats, acceptance):
    return (stats["max_depart_delay_s"] <= acceptance["max_depart_delay_s"]
            and waiting_fraction(stats) <= acceptance["max_waiting_fraction"])


def replicated_speed(route_name, route, value, field, fixed_flow, krauss, params):
    """Velocidade (km/h) por semente e estatísticas de inserção com a rota em value."""
    by_flow = field == "flow_veh_h"
    trial_route = route if by_flow else {**route, "speed_factor": value}
    flows = {route_name: value if by_flow else fixed_flow}

    def simulate(seed):
        return run_sumo(
            route["topology"], {route_name: trial_route}, 0, krauss, [route_name], route_flows=flows,
            min_depart=params["simulation"]["warmup_s"], seed=seed, with_stats=True,
        )

    seeds = params["replicas"]["seeds"]
    results = list(_sumo_pool.map(simulate, seeds))
    speeds = [speed.get(route_name, 0.0) * 3.6 for speed, _ in results]
    stats = [stat for _, stat in results]
    return {
        "value": value,
        "mean_kmh": statistics.fmean(speeds),
        "stdev_kmh": statistics.stdev(speeds),
        "seed_speeds_kmh": dict(zip(map(str, seeds), speeds)),
        "insertion_by_seed": dict(zip(map(str, seeds), stats)),
        "insertion_ok": all(insertion_ok(stat, params["acceptance"]) for stat in stats),
    }


def bisect_replicated(route_name, route, target_kmh, field, search_interval, fixed_flow, krauss, params):
    """Mesma bisseção de calibrate_stage1.bisect_route_speed, decidida pela média entre sementes."""
    by_flow = field == "flow_veh_h"
    tolerance = params["flow"]["tolerance_kmh"]
    evaluations = {}

    def evaluate(value):
        evaluations[value] = replicated_speed(route_name, route, value, field, fixed_flow, krauss, params)
        return evaluations[value]

    low, high = search_interval
    low_speed, high_speed = evaluate(low)["mean_kmh"], evaluate(high)["mean_kmh"]
    slowest, fastest = (high_speed, low_speed) if by_flow else (low_speed, high_speed)

    if target_kmh >= fastest:
        reason = "target_faster_than_interval"
    elif target_kmh <= slowest:
        reason = "target_slower_than_interval"
    else:
        reason = "iteration_budget"
        while len(evaluations) < params["flow"]["max_iterations"] and (high - low > 10 if by_flow else high - low > 0.005):
            middle = round(math.sqrt(low * high)) if by_flow else round((low + high) / 2, 4)
            evaluation = evaluate(middle)
            if abs(evaluation["mean_kmh"] - target_kmh) <= tolerance:
                # Na tolerância, mas com fila de inserção: seguir a bisseção pela velocidade não muda isso.
                reason = "converged" if evaluation["insertion_ok"] else "insertion_limit"
                break
            # Move o limite do lado em que a velocidade ainda está alta demais.
            if (evaluation["mean_kmh"] > target_kmh) == by_flow:
                low = middle
            else:
                high = middle

    # Prefere valores sem fila de inserção; só cai para os outros se nenhum respeita os limites.
    candidates = [e for e in evaluations.values() if e["insertion_ok"]] or list(evaluations.values())
    best = min(candidates, key=lambda e: abs(e["mean_kmh"] - target_kmh))
    error = best["mean_kmh"] - target_kmh
    seeds = len(params["replicas"]["seeds"])
    converged = abs(error) <= tolerance and best["insertion_ok"]

    return {
        field: best["value"],
        "target_kmh": target_kmh,
        "simulated_mean_kmh": best["mean_kmh"],
        "error_kmh": error,
        "seed_stdev_kmh": best["stdev_kmh"],
        "seed_stderr_kmh": best["stdev_kmh"] / math.sqrt(seeds),
        "seed_speeds_kmh": best["seed_speeds_kmh"],
        "within_tolerance": abs(error) <= tolerance,
        "insertion_ok": best["insertion_ok"],
        "converged": converged,
        "stop_reason": reason,
        "evaluations": len(evaluations),
        "search_interval": list(search_interval),
        "speed_at_interval_ends_kmh": [low_speed, high_speed],
        "insertion_by_seed": best["insertion_by_seed"],
        "trace": [
            {field: e["value"], "mean_kmh": round(e["mean_kmh"], 2), "stdev_kmh": round(e["stdev_kmh"], 2),
             "insertion_ok": e["insertion_ok"],
             "max_depart_delay_s": max(s["max_depart_delay_s"] for s in e["insertion_by_seed"].values()),
             "max_waiting_fraction": round(max(waiting_fraction(s) for s in e["insertion_by_seed"].values()), 4)}
            for e in evaluations.values()
        ],
    }


def calibrate_route(route_name, route, regime_name, regime_target, calibration_data, params):
    krauss = params["krauss"]["default"]
    lanes = bottleneck_lanes(route["topology"], route["sumo_edges"])
    target_kmh = regime_target["target_kmh"]
    if route["topology"] in params["topologies"]["speed_factor"]:
        flow = calibration_flow_rate(calibration_data) * lanes
        result = bisect_replicated(route_name, route, target_kmh, "speed_factor",
                                   tuple(params["speed_factor"]["interval"]), flow, krauss, params)
        result = {"method": "hcm_flow_speed_factor", "flow_veh_h": flow,
                  "calibration_flow": calibration_data["calibration_flow"], **result}
        value = f"speedFactor {result['speed_factor']:.3f}"
    else:
        interval = (params["flow"]["min_veh_h"], params["flow"]["max_veh_h_per_lane"] * lanes)
        result = bisect_replicated(route_name, route, target_kmh, "flow_veh_h", interval, None, krauss, params)
        result = {"method": "tomtom_speed", **result}
        value = f"{result['flow_veh_h']} veh/h"

    result.update(
        topology=route["topology"],
        bottleneck_lanes=lanes,
        target_day_std_kmh=regime_target["day_std_kmh"],
        target_days=regime_target["days_with_value"],
    )
    status = "OK" if result["converged"] else "AVISO"
    print(f"[{status}] {route_name} ({regime_name}): {value} -> {result['simulated_mean_kmh']:.1f} "
          f"± {result['seed_stdev_kmh']:.2f} km/h (alvo {target_kmh:.1f}, {result['stop_reason']}, "
          f"inserção {'ok' if result['insertion_ok'] else 'com fila'}, {result['evaluations']} avaliações)")
    return result


def main():
    params = load_calibration_params()
    regime_names = list(params["target"]["regimes"])
    parser = argparse.ArgumentParser(description="T2: bisseção por rota com réplicas e aceite com inserção")
    parser.add_argument("--regime", choices=regime_names, action="append",
                        help="Regime a calibrar (repetível); padrão: todos de target.regimes")
    parser.add_argument("--route", action="append", help="Rota a calibrar (repetível); padrão: todas com alvo")
    args = parser.parse_args()

    if not TARGETS_PATH.is_file():
        raise FileNotFoundError(f"{TARGETS_PATH} não existe; rode python3 calibration/targets.py antes")
    # run_sumo lê a duração e o leitor de velocidade do módulo; a fonte da duração é calibration_params.json.
    calibrate_stage1.SIMULATION_SECONDS = params["simulation"]["seconds"]
    calibrate_stage1.read_trip_speeds = read_trip_speeds_with_queue
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    calibration_data = load_calibration_data()
    routes = calibration_data["routes"]

    tasks, skipped = [], {}
    for regime_name in args.regime or regime_names:
        for route_name, target in sorted(targets["routes"].items()):
            if args.route and route_name not in args.route:
                continue
            regime_target = target["regimes"].get(regime_name)
            if not routes.get(route_name, {}).get("sumo_edges"):
                skipped.setdefault(regime_name, {})[route_name] = "sem caminho SUMO"
            elif regime_target is None or regime_target["status"] != "ok":
                skipped.setdefault(regime_name, {})[route_name] = "sem_alvo na T1"
            else:
                tasks.append((route_name, regime_name, regime_target))
    for regime_name, names in skipped.items():
        for route_name, reason in names.items():
            print(f"[PULADA] {route_name} ({regime_name}): {reason}")
    if not tasks:
        raise ValueError("Nenhuma rota com alvo para calibrar")

    def run(task):
        route_name, regime_name, regime_target = task
        return task, calibrate_route(route_name, routes[route_name], regime_name, regime_target,
                                     calibration_data, params)

    # As threads de rota só esperam; quem limita os processos SUMO é _sumo_pool.
    with ThreadPoolExecutor(len(tasks)) as pool:
        results = list(pool.map(run, tasks))
    _sumo_pool.shutdown()

    output = {
        "metadata": {
            "targets": TARGETS_PATH.name,
            "target_days": [targets["days_used"][0], targets["days_used"][-1]],
            "aggregation": targets["aggregation"],
            "seeds": params["replicas"]["seeds"],
            "warmup_s": params["simulation"]["warmup_s"],
            "simulation_seconds": params["simulation"]["seconds"],
            "tolerance_kmh": params["flow"]["tolerance_kmh"],
            "speed_includes_depart_delay": True,
            "acceptance": params["acceptance"],
            "krauss": params["krauss"]["default"],
            "signal_coordinated_topologies": params["topologies"]["signal_coordinated"],
            "speed_factor_topologies": params["topologies"]["speed_factor"],
        },
        "regimes": {},
    }
    for (route_name, regime_name, _), result in results:
        regime = output["regimes"].setdefault(regime_name, {"routes": {}, "skipped_routes": skipped.get(regime_name, {})})
        regime["routes"][route_name] = result
    for regime in output["regimes"].values():
        regime["converged_routes"] = sum(r["converged"] for r in regime["routes"].values())

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Calibração com réplicas salva em {OUTPUT_PATH}")
    for regime_name, regime in output["regimes"].items():
        print(f"{regime_name}: {regime['converged_routes']}/{len(regime['routes'])} rotas convergidas")


if __name__ == "__main__":
    main()
