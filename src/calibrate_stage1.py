"""Etapa 1.2: calibração com os dados CET MSVP 2019 (volume e velocidade por hora, sentido e trecho).

Método (adaptado de Khaleghian et al., SMARTCOMP 2023, docs/resumo_calibrating_real_world_city_traffic_simulation.md):
- Demanda observada: o fluxo de cada rota com `demand = "cet_volume"` é o volume CET da hora, em equivalente-carro
  (cet.pce). Não é inferido da velocidade.
- Demanda inferida: rotas com `demand = "speed_bisection"` (a Av. São Gabriel da intersection, sem contagem CET) têm o
  fluxo de cada hora achado por bisseção contra a velocidade CET da mesma hora, com as demais rotas no fluxo observado.
- Semáforos: nas topologias de topologies.signal_coordinated, offsets de onda verde do tlsCoordinator.py do SUMO,
  ponderados pelo fluxo médio das horas de calibração.
- Krauss: um perfil por topologia (parâmetros de krauss.bounds; os demais ficam em krauss.default), escolhido por TPE
  para minimizar o RMSE da velocidade nas horas cet.calibration_hours (tarde), só nas rotas de demanda observada.
  Depois, a bisseção das rotas de demanda inferida é refeita com o perfil escolhido.
- Validação: as horas cet.validation_hours (manhã) não entram no ajuste; o relatório final simula todas as horas de
  cet.flow_level_hours com simulation.report_seeds e separa os erros de calibração e de validação.

Entradas: conf/cet_scenarios.json (fetch_data.py), conf/cet_*_2019.csv, conf/calibration_params.json.
Saídas: conf/route_flows_calibrated.json (fluxo por rota e hora), conf/krauss_calibration.json (perfil e relatório),
osmFiles/<topologia>.tls_offsets.add.xml.
"""
import argparse
import json
import math
import statistics
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import optuna

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))
from calibration.params import load_calibration_params  # noqa: E402
from calibration.simulation import SUMO_BINARY, simulate  # noqa: E402
from cet_data import load_hourly_speeds, load_hourly_volumes  # noqa: E402
from generate_routes import build_route_file, tls_offsets_path  # noqa: E402
from sumo_route_utils import network_path  # noqa: E402


CONF_DIR = BASE_DIR / "conf"
SCENARIOS_PATH = CONF_DIR / "cet_scenarios.json"
KRAUSS_OUTPUT_PATH = CONF_DIR / "krauss_calibration.json"
ROUTE_FLOWS_OUTPUT_PATH = CONF_DIR / "route_flows_calibrated.json"
OBSERVED = "cet_volume"
INFERRED = "speed_bisection"


class Calibration:
    """Estado compartilhado: parâmetros, rotas CET e um limite global de processos SUMO simultâneos."""

    def __init__(self, params, scenarios, topologies=None):
        self.params = params
        self.volumes = load_hourly_volumes(params["cet"]["pce"])
        self.speeds = load_hourly_speeds()
        self.routes = {
            name: route for name, route in scenarios["routes"].items()
            if topologies is None or route["topology"] in topologies
        }
        if not self.routes:
            raise ValueError("Nenhuma rota para calibrar; confira conf/cet_scenarios.json e --topology")
        self.hours = sorted(params["cet"]["flow_level_hours"])
        self.sumo_slots = threading.BoundedSemaphore(params["execution"]["max_workers"])
        self.flows = {}  # {rota: {hora: veic/h}}
        self.inference = {}  # {rota: {hora: resultado da bisseção}}
        for name, route in self.routes.items():
            demand = route.get("demand", OBSERVED)
            if demand not in (OBSERVED, INFERRED):
                raise ValueError(f"Rota {name}: demand deve ser {OBSERVED!r} ou {INFERRED!r}")
            if name not in self.speeds:
                raise ValueError(f"Rota {name} não tem velocidade CET em conf/cet_speeds_2019.csv")
            missing = [hour for hour in self.hours if hour not in self.speeds[name]]
            if demand == OBSERVED:
                if name not in self.volumes:
                    raise ValueError(f"Rota {name} (demand={OBSERVED}) não tem volume em conf/cet_volumes_2019.csv")
                missing += [hour for hour in self.hours if hour not in self.volumes[name]]
                self.flows[name] = {hour: self.volumes[name][hour]["pce_veh_h"] for hour in self.hours
                                    if hour in self.volumes[name]}
            if missing:
                raise ValueError(f"Rota {name} sem dado CET para as horas {sorted(set(missing))}")

    def topology_routes(self, topology):
        return {name: route for name, route in self.routes.items() if route["topology"] == topology}

    def topologies(self):
        return sorted({route["topology"] for route in self.routes.values()})

    def demand(self, name):
        return self.routes[name].get("demand", OBSERVED)

    def target_kmh(self, name, hour):
        return self.speeds[name][hour]["speed_kmh"]

    def simulate(self, topology, krauss, route_flows, seed):
        simulation = self.params["simulation"]
        with self.sumo_slots:
            return simulate(topology, self.topology_routes(topology), krauss, route_flows,
                            simulation["seconds"], min_depart=simulation["warmup_s"], seed=seed)

    def hour_flows(self, topology, hour, overrides=None):
        flows = {name: self.flows[name][hour] for name in self.topology_routes(topology) if hour in self.flows.get(name, {})}
        flows.update(overrides or {})
        return {name: round(flow) for name, flow in flows.items()}

    def mean_speeds(self, topology, krauss, route_flows, seeds):
        """Velocidade média entre seeds por rota (km/h) e estatísticas de inserção da primeira seed."""
        speeds, stats = {}, None
        for seed in seeds:
            metrics, insertion = self.simulate(topology, krauss, route_flows, seed)
            stats = stats or insertion
            for name, values in metrics.items():
                speeds.setdefault(name, []).append(values["speed_kmh"])
        return {name: statistics.fmean(values) for name, values in speeds.items()}, speeds, stats


_bottleneck_cache = {}
_bottleneck_lock = threading.Lock()


def bottleneck_lanes(topology, edges):
    """Faixas da aresta mais estreita de uma rota na rede SUMO da topologia."""
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
    import os
    if os.environ.get("SUMO_HOME"):
        return Path(os.environ["SUMO_HOME"]) / "tools"
    return Path(SUMO_BINARY).resolve().parent.parent / "tools"


def coordinate_traffic_lights(calibration):
    """Offsets de onda verde (tlsCoordinator.py) ponderados pelo fluxo médio das horas de calibração.

    Rotas de demanda inferida ainda não têm fluxo: entram com metade do menor fluxo observado da topologia, para que a
    via com contagem CET (a principal) tenha prioridade na ordenação dos pares de semáforos.
    """
    calibration_hours = calibration.params["cet"]["calibration_hours"]
    for topology in calibration.topologies():
        offsets_path = tls_offsets_path(topology)
        offsets_path.unlink(missing_ok=True)
        if topology not in calibration.params["topologies"]["signal_coordinated"]:
            continue
        routes = calibration.topology_routes(topology)
        observed = {
            name: statistics.fmean(calibration.flows[name][hour] for hour in calibration_hours)
            for name in routes if calibration.demand(name) == OBSERVED
        }
        placeholder = min(observed.values()) / 2
        weights = {name: round(observed.get(name, placeholder)) for name in routes}
        route_xml = build_route_file(topology, routes, 0, calibration.params["krauss"]["default"], weights)
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
            raise RuntimeError(f"tlsCoordinator falhou para '{topology}': {result.stderr.strip()}")
        print(f"[SEMÁFOROS] {topology}: offsets coordenados salvos em {offsets_path}")


def bisect_flow(calibration, topology, name, hour, krauss, overrides):
    """Fluxo (veic/h) da rota `name` que reproduz a velocidade CET da hora, com as demais rotas fixas.

    A velocidade cai com o fluxo; bisseção em escala log entre flow.min_veh_h e flow.max_veh_h_per_lane × faixas do
    gargalo. Um alvo fora das velocidades dos extremos é registrado como inalcançável.
    """
    flow_params = calibration.params["flow"]
    seeds = calibration.params["simulation"]["search_seeds"]
    lanes = bottleneck_lanes(topology, calibration.routes[name]["sumo_edges"])
    target = calibration.target_kmh(name, hour)
    evaluations = {}

    def evaluate(flow):
        flows = calibration.hour_flows(topology, hour, {**overrides, name: flow})
        speeds, _, stats = calibration.mean_speeds(topology, krauss, flows, seeds)
        evaluations[flow] = (speeds.get(name, 0.0), stats)
        return evaluations[flow][0]

    low, high = flow_params["min_veh_h"], round(flow_params["max_veh_h_per_lane"] * lanes)
    fastest, slowest = evaluate(low), evaluate(high)
    if target >= fastest:
        reason = "target_faster_than_interval"
    elif target <= slowest:
        reason = "target_slower_than_interval"
    else:
        reason = "iteration_budget"
        while len(evaluations) < flow_params["max_iterations"] and high - low > 10:
            middle = round(math.sqrt(low * high))
            speed = evaluate(middle)
            if abs(speed - target) <= flow_params["tolerance_kmh"]:
                reason = "converged"
                break
            if speed > target:
                low = middle
            else:
                high = middle

    best = min(evaluations, key=lambda flow: abs(evaluations[flow][0] - target))
    speed, stats = evaluations[best]
    return {
        "flow_veh_h": best,
        "target_kmh": target,
        "simulated_kmh": round(speed, 2),
        "error_kmh": round(speed - target, 2),
        "reachable": reason in ("converged", "iteration_budget"),
        "stop_reason": reason,
        "trials": len(evaluations),
        "speed_at_interval_ends_kmh": [round(fastest, 2), round(slowest, 2)],
        "insertion": stats,
    }


def infer_demand(calibration, topology, krauss):
    """Bisseção hora a hora das rotas de demanda inferida (uma passada, em ordem de nome)."""
    inferred = sorted(name for name in calibration.topology_routes(topology) if calibration.demand(name) == INFERRED)
    if not inferred:
        return

    def infer_hour(hour):
        flow_params = calibration.params["flow"]
        current = {name: calibration.flows.get(name, {}).get(hour) or round(math.sqrt(
            flow_params["min_veh_h"] * flow_params["max_veh_h_per_lane"])) for name in inferred}
        results = {}
        for name in inferred:
            others = {other: flow for other, flow in current.items() if other != name}
            results[name] = bisect_flow(calibration, topology, name, hour, krauss, others)
            current[name] = results[name]["flow_veh_h"]
        return hour, results

    with ThreadPoolExecutor(len(calibration.hours)) as pool:
        for hour, results in pool.map(infer_hour, calibration.hours):
            for name, result in results.items():
                calibration.flows.setdefault(name, {})[hour] = result["flow_veh_h"]
                calibration.inference.setdefault(name, {})[hour] = result
                status = "OK" if result["reachable"] else "AVISO"
                print(f"[DEMANDA {status}] {name} {hour:02d}h: {result['flow_veh_h']} veic/h -> "
                      f"{result['simulated_kmh']:.1f} km/h (CET {result['target_kmh']:.1f}, {result['stop_reason']})")


def calibration_rmse(calibration, topology, krauss):
    """RMSE (km/h) da velocidade nas horas de calibração, só nas rotas de demanda observada."""
    hours = calibration.params["cet"]["calibration_hours"]
    seeds = calibration.params["simulation"]["search_seeds"]
    observed = [name for name in calibration.topology_routes(topology) if calibration.demand(name) == OBSERVED]

    def run_hour(hour):
        speeds, _, _ = calibration.mean_speeds(topology, krauss, calibration.hour_flows(topology, hour), seeds)
        return hour, speeds

    errors = []
    with ThreadPoolExecutor(len(hours)) as pool:
        for hour, speeds in pool.map(run_hour, hours):
            for name in observed:
                # Nenhuma viagem completa (rede travada): conta como velocidade 0, o maior erro possível.
                errors.append(speeds.get(name, 0.0) - calibration.target_kmh(name, hour))
    return math.sqrt(statistics.fmean(error ** 2 for error in errors))


def calibrate_krauss(calibration, topology, n_trials=None):
    krauss_params = calibration.params["krauss"]
    default = krauss_params["default"]
    bounds = krauss_params["bounds"]
    n_trials = n_trials or krauss_params["n_trials"]
    hours = calibration.params["cet"]["calibration_hours"]
    observed = [name for name in calibration.topology_routes(topology) if calibration.demand(name) == OBSERVED]
    reference = statistics.fmean(calibration.target_kmh(name, hour) for name in observed for hour in hours)

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=krauss_params["sampler_seed"],
                                           n_startup_trials=krauss_params["n_startup_trials"]),
    )
    study.enqueue_trial({key: default[key] for key in bounds})
    stop = {"reason": None, "best_number": None, "streak": 0}
    stop_lock = threading.Lock()

    def stop_rule(study, trial):
        with stop_lock:
            if study.best_value / reference <= krauss_params["target_relative_rmse"]:
                stop["reason"] = "target_relative_rmse"
                study.stop()
                return
            if trial.number < krauss_params["n_startup_trials"]:
                return
            if study.best_trial.number == stop["best_number"]:
                stop["streak"] += 1
            else:
                stop["best_number"], stop["streak"] = study.best_trial.number, 0
            if stop["streak"] >= krauss_params["stagnation_patience"]:
                stop["reason"] = "best_trial_unchanged"
                study.stop()

    def objective(trial):
        krauss = {**default, **{key: trial.suggest_float(key, *interval) for key, interval in bounds.items()}}
        return calibration_rmse(calibration, topology, krauss)

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(objective, n_trials=n_trials, n_jobs=2, callbacks=[stop_rule])
    best = {**default, **study.best_params}
    print(f"[KRAUSS] {topology}: RMSE {study.best_value:.2f} km/h ({study.best_value / reference:.1%} da velocidade "
          f"CET média), {len(study.trials)} trials, parada: {stop['reason'] or 'trial_budget'}")
    return best, {
        "rmse_kmh": round(study.best_value, 3),
        "relative_rmse": round(study.best_value / reference, 4),
        "reference_speed_kmh": round(reference, 2),
        "trials": len(study.trials),
        "stop_reason": stop["reason"] or "trial_budget",
        "free_parameters": sorted(bounds),
    }


def error_summary(rows):
    errors = [row["error_kmh"] for row in rows]
    if not errors:
        return None
    return {
        "n": len(errors),
        "rmse_kmh": round(math.sqrt(statistics.fmean(error ** 2 for error in errors)), 3),
        "mae_kmh": round(statistics.fmean(abs(error) for error in errors), 3),
        "bias_kmh": round(statistics.fmean(errors), 3),
    }


def report(calibration, topology, krauss):
    """Todas as horas de cet.flow_level_hours com simulation.report_seeds: erro por rota e hora."""
    seeds = calibration.params["simulation"]["report_seeds"]
    cet = calibration.params["cet"]
    acceptance = calibration.params["acceptance"]

    def run_hour(hour):
        flows = calibration.hour_flows(topology, hour)
        mean, by_seed, stats = calibration.mean_speeds(topology, krauss, flows, seeds)
        return hour, flows, mean, by_seed, stats

    rows = []
    with ThreadPoolExecutor(len(calibration.hours)) as pool:
        for hour, flows, mean, by_seed, stats in pool.map(run_hour, calibration.hours):
            for name in sorted(calibration.topology_routes(topology)):
                target = calibration.target_kmh(name, hour)
                simulated = mean.get(name)
                role = "calibration" if hour in cet["calibration_hours"] else (
                    "validation" if hour in cet["validation_hours"] else "level_only")
                rows.append({
                    "route": name,
                    "hour": hour,
                    "role": role,
                    "demand": calibration.demand(name),
                    "flow_veh_h": flows[name],
                    "cet_kmh": target,
                    "simulated_kmh": None if simulated is None else round(simulated, 2),
                    "seed_stdev_kmh": round(statistics.stdev(by_seed[name]), 2) if len(by_seed.get(name, [])) > 1 else None,
                    "error_kmh": None if simulated is None else round(simulated - target, 2),
                    "insertion_ok": stats["max_depart_delay_s"] <= acceptance["max_depart_delay_s"]
                                    and stats["waiting_at_end"] <= acceptance["max_waiting_fraction"] * max(stats["loaded"], 1),
                    "max_depart_delay_s": round(stats["max_depart_delay_s"], 1),
                    "waiting_at_end": stats["waiting_at_end"],
                })
    observed = [row for row in rows if row["demand"] == OBSERVED and row["error_kmh"] is not None]
    summary = {
        "calibration": error_summary([row for row in observed if row["role"] == "calibration"]),
        "validation": error_summary([row for row in observed if row["role"] == "validation"]),
        # A velocidade das rotas de demanda inferida foi usada para achar o fluxo: o erro delas não valida o modelo.
        "inferred_demand_fit": error_summary([row for row in rows if row["demand"] == INFERRED
                                              and row["error_kmh"] is not None]),
    }
    for role in ("calibration", "validation"):
        if summary[role]:
            print(f"[RELATÓRIO] {topology} {role}: RMSE {summary[role]['rmse_kmh']:.2f} km/h, "
                  f"MAE {summary[role]['mae_kmh']:.2f}, viés {summary[role]['bias_kmh']:+.2f} (n={summary[role]['n']})")
    bad = [row for row in rows if not row["insertion_ok"]]
    if bad:
        print(f"[AVISO] {topology}: fila de inserção acima de acceptance.* em "
              f"{sorted({(row['route'], row['hour']) for row in bad})}")
    return {"summary": summary, "rows": rows}


def calibrate_topology(calibration, topology, n_trials=None):
    default = calibration.params["krauss"]["default"]
    print(f"[DEMANDA] {topology}: inferindo as rotas sem contagem com krauss.default...")
    infer_demand(calibration, topology, default)
    print(f"[KRAUSS] {topology}: buscando o perfil...")
    krauss, search = calibrate_krauss(calibration, topology, n_trials)
    print(f"[DEMANDA] {topology}: refazendo a inferência com o perfil escolhido...")
    infer_demand(calibration, topology, krauss)
    return {"params": krauss, "search": search, "report": report(calibration, topology, krauss)}


def write_route_flows(calibration, topologies):
    output = {
        "metadata": {
            "source": "CET MSVP 2019; fluxo em equivalente-carro (cet.pce)",
            "pce": calibration.params["cet"]["pce"],
            "demand_methods": {OBSERVED: "volume CET da hora", INFERRED: "bisseção contra a velocidade CET da hora"},
        },
        "routes": {},
    }
    if ROUTE_FLOWS_OUTPUT_PATH.is_file():
        # Mantém só rotas no formato CET de outras topologias (rodadas com --topology); descarta formatos antigos.
        previous = json.loads(ROUTE_FLOWS_OUTPUT_PATH.read_text(encoding="utf-8")).get("routes", {})
        output["routes"] = {name: route for name, route in previous.items() if "hourly_flow_veh_h" in route}
    for name, route in sorted(calibration.routes.items()):
        if route["topology"] not in topologies:
            continue
        output["routes"][name] = {
            "topology": route["topology"],
            "demand": calibration.demand(name),
            "bottleneck_lanes": bottleneck_lanes(route["topology"], route["sumo_edges"]),
            "hourly_flow_veh_h": {str(hour): round(flow, 1) for hour, flow in sorted(calibration.flows[name].items())},
            **({"inference": {str(hour): result for hour, result in sorted(calibration.inference[name].items())}}
               if name in calibration.inference else {}),
        }
    ROUTE_FLOWS_OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Fluxos por rota e hora salvos em {ROUTE_FLOWS_OUTPUT_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Etapa 1.2: calibração Krauss com os dados CET MSVP 2019")
    parser.add_argument("--topology", nargs="+", help="Só estas topologias (as demais ficam como estão nos JSONs)")
    parser.add_argument("--trials", type=int, help="Teste curto: substitui krauss.n_trials")
    args = parser.parse_args()

    params = load_calibration_params()
    if not SCENARIOS_PATH.is_file():
        raise FileNotFoundError(f"{SCENARIOS_PATH} não existe; rode fetch_data.py primeiro")
    scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    calibration = Calibration(params, scenarios, args.topology)

    coordinate_traffic_lights(calibration)
    previous = json.loads(KRAUSS_OUTPUT_PATH.read_text(encoding="utf-8")) if KRAUSS_OUTPUT_PATH.is_file() else {}
    # Perfis de outras topologias (rodadas com --topology) no formato CET; descarta formatos antigos.
    output = {name: value for name, value in previous.items() if isinstance(value, dict) and "params" in value}
    output["metadata"] = {
        "source": "CET MSVP 2019 (docs/relatorio-msvp-2019-revisao-2-junho-22.pdf)",
        "calibration_hours": params["cet"]["calibration_hours"],
        "validation_hours": params["cet"]["validation_hours"],
        "objective": "RMSE da velocidade média espacial (km/h) por rota e hora, rotas de demanda observada",
    }
    output_lock = threading.Lock()
    finished = set()

    def run(topology):
        result = calibrate_topology(calibration, topology, args.trials)
        with output_lock:
            finished.add(topology)
            output[topology] = result
            KRAUSS_OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
            write_route_flows(calibration, finished)

    with ThreadPoolExecutor(len(calibration.topologies())) as pool:
        list(pool.map(run, calibration.topologies()))
    print(f"[OK] Perfis Krauss e relatório salvos em {KRAUSS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
