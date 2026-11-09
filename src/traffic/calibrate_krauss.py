"""Etapa 1.2: calibração com os dados CET MSVP (volume e velocidade por ano, hora, sentido e trecho).

Método (adaptado de Khaleghian et al., SMARTCOMP 2023, docs/resumo_calibrating_real_world_city_traffic_simulation.md):
- Níveis observados: cada (ano, hora) de cet.years × cet.hours em que todas as rotas de demanda observada da topologia
  têm volume CET. Cada nível vira um cenário do experimento V2V (id y<ano>h<hora>, ver flow_levels.py).
- Demanda observada: o fluxo de cada rota com `demand = "cet_volume"` é o volume CET do nível, em equivalente-carro
  (cet.pce). Não é inferido da velocidade.
- Demanda inferida: rotas com `demand = "speed_bisection"` (a Av. São Gabriel da intersection, sem contagem CET) têm o
  fluxo de cada nível achado por bisseção contra a velocidade CET do mesmo nível, com as demais rotas no fluxo
  observado. Num nível sem velocidade da rota, usa o fluxo inferido na mesma hora do ano de calibração.
- Semáforos: nas topologias de topologies.signal_coordinated, offsets de onda verde do tlsCoordinator.py do SUMO,
  ponderados pelo fluxo médio dos níveis de calibração.
- Krauss: um perfil por topologia (parâmetros de krauss.bounds; os demais ficam em krauss.default), escolhido por TPE
  para minimizar o RMSE da velocidade nos níveis de calibração (cet.calibration: ano e horas), só nas rotas de demanda
  observada. Depois, a bisseção das rotas de demanda inferida é refeita com o perfil escolhido.
- Validação: todo nível observado fora da calibração que tenha velocidade CET (as outras horas do ano de calibração e
  todos os outros anos). O relatório final simula todos os níveis com simulation.report_seeds e separa os erros.

Entradas: conf/cet_scenarios.json (fetch_data.py), conf/cet_volumes.csv, conf/cet_speeds.csv,
conf/calibration_params.json.
Saídas: conf/route_flows_calibrated.json (fluxo por rota e nível), conf/krauss_calibration.json (perfil e relatório),
osmFiles/<topologia>.tls_offsets.add.xml.
"""
import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import optuna

from src.analysis.flow_levels import level_id
from src.env import CONFIG_DIR, ROOT_DIR, SUMO_BINARY
from src.traffic.cet_data import load_hourly_speeds, load_hourly_volumes
from src.traffic.generate_routes import build_route_file, tls_offsets_path
from src.traffic.params import load_calibration_params
from src.traffic.route_utils import network_path
from src.traffic.simulation import simulate

SCENARIOS_PATH = CONFIG_DIR / "cet_scenarios.json"
KRAUSS_OUTPUT_PATH = CONFIG_DIR / "krauss_calibration.json"
ROUTE_FLOWS_OUTPUT_PATH = CONFIG_DIR / "route_flows_calibrated.json"
OBSERVED = "cet_volume"
INFERRED = "speed_bisection"


def key_id(key):
    return level_id(*key)


class Calibration:
    """Estado compartilhado: parâmetros, rotas e dados CET, e um limite global de processos SUMO simultâneos.

    Os dados e fluxos são indexados por chave (ano, hora).
    """

    def __init__(self, params, scenarios, topologies=None):
        self.params = params
        cet = params["cet"]
        years, hours = set(cet["years"]), set(cet["hours"])
        self.volumes = load_hourly_volumes(cet["pce"], years, hours)
        self.speeds = load_hourly_speeds(years, hours)
        self.routes = {
            name: route for name, route in scenarios["routes"].items()
            if topologies is None or route["topology"] in topologies
        }
        if not self.routes:
            raise ValueError("Nenhuma rota para calibrar; confira conf/cet_scenarios.json e --topology")
        self.sumo_slots = threading.BoundedSemaphore(params["execution"]["max_workers"])
        self.flows = {}  # {rota: {(ano, hora): veic/h}}
        self.inference = {}  # {rota: {(ano, hora): resultado da bisseção ou do fallback}}
        self.keys = {}  # {topologia: [(ano, hora), ...]} níveis observados

        for name, route in self.routes.items():
            demand = route.get("demand", OBSERVED)
            if demand not in (OBSERVED, INFERRED):
                raise ValueError(f"Rota {name}: demand deve ser {OBSERVED!r} ou {INFERRED!r}")
            calibration_keys = self.calibration_keys(route["topology"])
            missing = [key for key in calibration_keys if key not in self.speeds.get(name, {})]
            if demand == OBSERVED:
                missing += [key for key in calibration_keys if key not in self.volumes.get(name, {})]
                self.flows[name] = {key: value["pce_veh_h"] for key, value in self.volumes.get(name, {}).items()}
            if missing:
                raise ValueError(f"Rota {name} sem dado CET nos níveis de calibração {[key_id(k) for k in missing]}")

        for topology in self.topologies():
            observed = [name for name in self.topology_routes(topology) if self.demand(name) == OBSERVED]
            if not observed:
                raise ValueError(f"Topologia {topology} não tem rota com demanda observada")
            self.keys[topology] = sorted(set.intersection(*(set(self.flows[name]) for name in observed)))
            print(f"[NÍVEIS] {topology}: {len(self.keys[topology])} níveis observados "
                  f"({', '.join(key_id(key) for key in self.keys[topology])})")

    def calibration_keys(self, topology):
        """Níveis (ano, hora) de calibração da topologia: cet.calibration.hours, ou hours_by_topology[topologia]."""
        calibration = self.params["cet"]["calibration"]
        hours = calibration.get("hours_by_topology", {}).get(topology, calibration["hours"])
        return sorted((calibration["year"], hour) for hour in hours)

    def topology_routes(self, topology):
        return {name: route for name, route in self.routes.items() if route["topology"] == topology}

    def topologies(self):
        return sorted({route["topology"] for route in self.routes.values()})

    def demand(self, name):
        return self.routes[name].get("demand", OBSERVED)

    def target_kmh(self, name, key):
        """Velocidade CET da rota no nível, ou None se a CET não mediu aquele trecho naquele ano e hora."""
        speed = self.speeds.get(name, {}).get(key)
        return None if speed is None else speed["speed_kmh"]

    def simulate(self, topology, krauss, route_flows, seed):
        simulation = self.params["simulation"]
        with self.sumo_slots:
            return simulate(topology, self.topology_routes(topology), krauss, route_flows,
                            simulation["seconds"], min_depart=simulation["warmup_s"], seed=seed)

    def key_flows(self, topology, key, overrides=None):
        flows = {name: self.flows[name][key] for name in self.topology_routes(topology) if key in self.flows.get(name, {})}
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
    if os.environ.get("SUMO_HOME"):
        return Path(os.environ["SUMO_HOME"]) / "tools"
    return Path(SUMO_BINARY).resolve().parent.parent / "tools"


def coordinate_traffic_lights(calibration):
    """Offsets de onda verde (tlsCoordinator.py) ponderados pelo fluxo médio dos níveis de calibração.

    Rotas de demanda inferida ainda não têm fluxo: entram com metade do menor fluxo observado da topologia, para que a
    via com contagem CET (a principal) tenha prioridade na ordenação dos pares de semáforos.
    """
    for topology in calibration.topologies():
        offsets_path = tls_offsets_path(topology)
        offsets_path.unlink(missing_ok=True)
        if topology not in calibration.params["topologies"]["signal_coordinated"]:
            continue
        routes = calibration.topology_routes(topology)
        observed = {
            name: statistics.fmean(calibration.flows[name][key] for key in calibration.calibration_keys(topology))
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
            ], capture_output=True, text=True, cwd=ROOT_DIR)
        if result.returncode != 0:
            raise RuntimeError(f"tlsCoordinator falhou para '{topology}': {result.stderr.strip()}")
        print(f"[SEMÁFOROS] {topology}: offsets coordenados salvos em {offsets_path}")


def bisect_flow(calibration, topology, name, key, krauss, overrides):
    """Fluxo (veic/h) da rota `name` que reproduz a velocidade CET do nível, com as demais rotas fixas.

    A velocidade cai com o fluxo; bisseção em escala log entre flow.min_veh_h e flow.max_veh_h_per_lane × faixas do
    gargalo. Um alvo fora das velocidades dos extremos é registrado como inalcançável.
    """
    flow_params = calibration.params["flow"]
    seeds = calibration.params["simulation"]["search_seeds"]
    lanes = bottleneck_lanes(topology, calibration.routes[name]["sumo_edges"])
    target = calibration.target_kmh(name, key)
    evaluations = {}

    def evaluate(flow):
        flows = calibration.key_flows(topology, key, {**overrides, name: flow})
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
        "method": "bisection",
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
    """Fluxo das rotas de demanda inferida em cada nível observado da topologia.

    Níveis com velocidade CET da rota: bisseção (uma passada, em ordem de nome). Níveis sem: o fluxo inferido na
    mesma hora do ano de calibração (ou, se não houver, a média dos inferidos).
    """
    inferred = sorted(name for name in calibration.topology_routes(topology) if calibration.demand(name) == INFERRED)
    if not inferred:
        return
    keys = calibration.keys[topology]
    flow_params = calibration.params["flow"]
    start = round(math.sqrt(flow_params["min_veh_h"] * flow_params["max_veh_h_per_lane"]))

    def infer_key(key):
        current = {name: calibration.flows.get(name, {}).get(key) or start for name in inferred}
        results = {}
        for name in inferred:
            if calibration.target_kmh(name, key) is None:
                continue
            others = {other: flow for other, flow in current.items() if other != name}
            results[name] = bisect_flow(calibration, topology, name, key, krauss, others)
            current[name] = results[name]["flow_veh_h"]
        return key, results

    with ThreadPoolExecutor(max(1, len(keys))) as pool:
        for key, results in pool.map(infer_key, keys):
            for name, result in results.items():
                calibration.flows.setdefault(name, {})[key] = result["flow_veh_h"]
                calibration.inference.setdefault(name, {})[key] = result
                status = "OK" if result["reachable"] else "AVISO"
                print(f"[DEMANDA {status}] {name} {key_id(key)}: {result['flow_veh_h']} veic/h -> "
                      f"{result['simulated_kmh']:.1f} km/h (CET {result['target_kmh']:.1f}, {result['stop_reason']})")

    calibration_year = calibration.params["cet"]["calibration"]["year"]
    for name in inferred:
        bisected = dict(calibration.flows.get(name, {}))
        if not any(calibration.target_kmh(name, key) is not None for key in bisected):
            raise ValueError(f"Rota {name} (demand={INFERRED}) não tem nenhum nível com velocidade CET")
        for key in keys:
            if calibration.target_kmh(name, key) is not None:
                continue
            source = (calibration_year, key[1])
            if source in bisected and calibration.target_kmh(name, source) is not None:
                flow, origin = bisected[source], key_id(source)
            else:
                measured = [bisected[k] for k in keys if calibration.target_kmh(name, k) is not None and k in bisected]
                flow, origin = round(statistics.fmean(measured)), "média dos níveis com velocidade"
            calibration.flows.setdefault(name, {})[key] = flow
            calibration.inference.setdefault(name, {})[key] = {"method": "fallback", "flow_veh_h": flow, "source": origin}
            print(f"[DEMANDA] {name} {key_id(key)}: sem velocidade CET; usando {flow} veic/h de {origin}")


def calibration_rmse(calibration, topology, krauss):
    """RMSE (km/h) da velocidade nos níveis de calibração, só nas rotas de demanda observada."""
    keys = calibration.calibration_keys(topology)
    seeds = calibration.params["simulation"]["search_seeds"]
    observed = [name for name in calibration.topology_routes(topology) if calibration.demand(name) == OBSERVED]

    def run_key(key):
        speeds, _, _ = calibration.mean_speeds(topology, krauss, calibration.key_flows(topology, key), seeds)
        return key, speeds

    errors = []
    with ThreadPoolExecutor(len(keys)) as pool:
        for key, speeds in pool.map(run_key, keys):
            for name in observed:
                # Nenhuma viagem completa (rede travada): conta como velocidade 0, o maior erro possível.
                errors.append(speeds.get(name, 0.0) - calibration.target_kmh(name, key))
    return math.sqrt(statistics.fmean(error ** 2 for error in errors))


def calibrate_krauss(calibration, topology, n_trials=None):
    krauss_params = calibration.params["krauss"]
    default = krauss_params["default"]
    bounds = krauss_params["bounds"]
    n_trials = n_trials or krauss_params["n_trials"]
    observed = [name for name in calibration.topology_routes(topology) if calibration.demand(name) == OBSERVED]
    reference = statistics.fmean(calibration.target_kmh(name, key)
                                 for name in observed for key in calibration.calibration_keys(topology))

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
    """RMSE, MAE e viés (km/h) das linhas do relatório; RMSE relativo = RMSE / velocidade CET média das linhas."""
    errors = [row["error_kmh"] for row in rows]
    if not errors:
        return None
    rmse = math.sqrt(statistics.fmean(error ** 2 for error in errors))
    return {
        "n": len(errors),
        "rmse_kmh": round(rmse, 3),
        "relative_rmse": round(rmse / statistics.fmean(row["cet_kmh"] for row in rows), 4),
        "mae_kmh": round(statistics.fmean(abs(error) for error in errors), 3),
        "bias_kmh": round(statistics.fmean(errors), 3),
    }


def validation_verdict(rows, max_relative_rmse):
    """Critério de validação (Khaleghian et al. 2023): RMSE relativo de validação <= max_relative_rmse.

    Só as rotas de demanda observada: a velocidade das rotas de demanda inferida foi usada para achar o fluxo. A
    topologia passa pelo RMSE relativo de todas as linhas de validação; o resultado por rota é informativo.
    """
    validation = [row for row in rows if row["role"] == "validation" and row["demand"] == OBSERVED
                  and row["error_kmh"] is not None]
    overall = error_summary(validation)
    by_route = {}
    for route in sorted({row["route"] for row in validation}):
        summary = error_summary([row for row in validation if row["route"] == route])
        by_route[route] = {**summary, "passed": summary["relative_rmse"] <= max_relative_rmse}
    return {
        "criterion": "RMSE relativo da validação (rotas de demanda observada) <= max_relative_rmse",
        "max_relative_rmse": max_relative_rmse,
        "relative_rmse": overall["relative_rmse"] if overall else None,
        "passed": bool(overall) and overall["relative_rmse"] <= max_relative_rmse,
        "routes": by_route,
    }


def print_verdict(topology, verdict):
    if verdict["relative_rmse"] is None:
        print(f"[VALIDAÇÃO] {topology}: sem níveis de validação")
        return
    status = "APROVADA" if verdict["passed"] else "REPROVADA"
    print(f"[VALIDAÇÃO] {topology}: {status} (RMSE relativo {verdict['relative_rmse']:.1%}, "
          f"limite {verdict['max_relative_rmse']:.0%})")
    for route, result in verdict["routes"].items():
        print(f"    {route}: RMSE {result['rmse_kmh']:.1f} km/h, relativo {result['relative_rmse']:.1%}, "
              f"viés {result['bias_kmh']:+.1f} ({'ok' if result['passed'] else 'acima'}, n={result['n']})")


def report(calibration, topology, krauss):
    """Todos os níveis observados da topologia com simulation.report_seeds: erro por rota e nível."""
    seeds = calibration.params["simulation"]["report_seeds"]
    acceptance = calibration.params["acceptance"]
    keys = calibration.keys[topology]

    def run_key(key):
        flows = calibration.key_flows(topology, key)
        mean, by_seed, stats = calibration.mean_speeds(topology, krauss, flows, seeds)
        return key, flows, mean, by_seed, stats

    rows = []
    with ThreadPoolExecutor(max(1, len(keys))) as pool:
        for key, flows, mean, by_seed, stats in pool.map(run_key, keys):
            for name in sorted(calibration.topology_routes(topology)):
                target = calibration.target_kmh(name, key)
                simulated = mean.get(name)
                if key in calibration.calibration_keys(topology):
                    role = "calibration"
                elif target is not None:
                    role = "validation"
                else:
                    role = "level_only"
                inference = calibration.inference.get(name, {}).get(key, {})
                rows.append({
                    "route": name,
                    "level": key_id(key),
                    "year": key[0],
                    "hour": key[1],
                    "role": role,
                    "demand": calibration.demand(name) if inference.get("method") != "fallback" else "fallback",
                    "flow_veh_h": flows[name],
                    "cet_kmh": target,
                    "simulated_kmh": None if simulated is None else round(simulated, 2),
                    "seed_stdev_kmh": round(statistics.stdev(by_seed[name]), 2) if len(by_seed.get(name, [])) > 1 else None,
                    "error_kmh": None if simulated is None or target is None else round(simulated - target, 2),
                    "insertion_ok": stats["max_depart_delay_s"] <= acceptance["max_depart_delay_s"]
                                    and stats["waiting_at_end"] <= acceptance["max_waiting_fraction"] * max(stats["loaded"], 1),
                    "max_depart_delay_s": round(stats["max_depart_delay_s"], 1),
                    "waiting_at_end": stats["waiting_at_end"],
                })
    observed = [row for row in rows if row["demand"] == OBSERVED and row["error_kmh"] is not None]
    summary = {
        "calibration": error_summary([row for row in observed if row["role"] == "calibration"]),
        "validation": error_summary([row for row in observed if row["role"] == "validation"]),
        "validation_by_year": {
            str(year): error_summary([row for row in observed if row["role"] == "validation" and row["year"] == year])
            for year in sorted({row["year"] for row in observed if row["role"] == "validation"})
        },
        # A velocidade das rotas de demanda inferida foi usada para achar o fluxo: o erro delas não valida o modelo.
        "inferred_demand_fit": error_summary([row for row in rows if row["demand"] == INFERRED
                                              and row["error_kmh"] is not None]),
    }
    for role in ("calibration", "validation"):
        if summary[role]:
            print(f"[RELATÓRIO] {topology} {role}: RMSE {summary[role]['rmse_kmh']:.2f} km/h, "
                  f"MAE {summary[role]['mae_kmh']:.2f}, viés {summary[role]['bias_kmh']:+.2f} (n={summary[role]['n']})")
    summary["verdict"] = validation_verdict(rows, acceptance["max_validation_relative_rmse"])
    print_verdict(topology, summary["verdict"])
    bad = sorted({row["level"] for row in rows if not row["insertion_ok"]})
    if bad:
        print(f"[AVISO] {topology}: fila de inserção acima de acceptance.* nos níveis {bad}")
    return {"summary": summary, "rows": rows}


def calibrate_topology(calibration, topology, n_trials=None):
    default = calibration.params["krauss"]["default"]
    print(f"[DEMANDA] {topology}: inferindo as rotas sem contagem com krauss.default...")
    infer_demand(calibration, topology, default)
    print(f"[KRAUSS] {topology}: buscando o perfil...")
    krauss, search = calibrate_krauss(calibration, topology, n_trials)
    print(f"[DEMANDA] {topology}: refazendo a inferência com o perfil escolhido...")
    infer_demand(calibration, topology, krauss)
    return {
        "params": krauss,
        "search": search,
        "levels": [key_id(key) for key in calibration.keys[topology]],
        "report": report(calibration, topology, krauss),
    }


def write_route_flows(calibration, topologies):
    output = {
        "metadata": {
            "source": "CET MSVP; fluxo em equivalente-carro (cet.pce), por nível y<ano>h<hora>",
            "pce": calibration.params["cet"]["pce"],
            "demand_methods": {OBSERVED: "volume CET do nível", INFERRED: "bisseção contra a velocidade CET do nível"},
        },
        "routes": {},
    }
    if ROUTE_FLOWS_OUTPUT_PATH.is_file():
        # Mantém só rotas no formato atual de outras topologias (rodadas com --topology); descarta formatos antigos.
        previous = json.loads(ROUTE_FLOWS_OUTPUT_PATH.read_text(encoding="utf-8")).get("routes", {})
        output["routes"] = {name: route for name, route in previous.items() if "flow_veh_h_by_level" in route}
    for name, route in sorted(calibration.routes.items()):
        if route["topology"] not in topologies:
            continue
        keys = calibration.keys[route["topology"]]
        output["routes"][name] = {
            "topology": route["topology"],
            "demand": calibration.demand(name),
            "bottleneck_lanes": bottleneck_lanes(route["topology"], route["sumo_edges"]),
            "flow_veh_h_by_level": {key_id(key): round(calibration.flows[name][key], 1) for key in keys},
            **({"inference": {key_id(key): result for key, result in sorted(calibration.inference[name].items())}}
               if name in calibration.inference else {}),
        }
    ROUTE_FLOWS_OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Fluxos por rota e nível salvos em {ROUTE_FLOWS_OUTPUT_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Etapa 1.2: calibração Krauss com os dados CET MSVP")
    parser.add_argument("--topology", nargs="+", help="Só estas topologias (as demais ficam como estão nos JSONs)")
    parser.add_argument("--trials", type=int, help="Teste curto: substitui krauss.n_trials")
    parser.add_argument("--smoke", action="store_true",
                        help="Só para testar o código (minutos): 2 trials, simulações de 600 s, bisseção curta, 2 seeds "
                             "no relatório. O resultado não serve como calibração")
    parser.add_argument("--verdict-only", action="store_true",
                        help="Não simula: aplica acceptance.max_validation_relative_rmse ao relatório já gravado em "
                             "conf/krauss_calibration.json")
    args = parser.parse_args()

    params = load_calibration_params()
    if args.verdict_only:
        output = json.loads(KRAUSS_OUTPUT_PATH.read_text(encoding="utf-8"))
        for topology, result in sorted(output.items()):
            if topology == "metadata" or "report" not in result:
                continue
            rows = result["report"]["rows"]
            # Relatórios antigos não têm relative_rmse: recalcula os resumos com as linhas gravadas.
            observed = [row for row in rows if row["demand"] == OBSERVED and row["error_kmh"] is not None]
            for role in ("calibration", "validation"):
                result["report"]["summary"][role] = error_summary([row for row in observed if row["role"] == role])
            result["report"]["summary"]["verdict"] = validation_verdict(
                rows, params["acceptance"]["max_validation_relative_rmse"])
            print_verdict(topology, result["report"]["summary"]["verdict"])
        KRAUSS_OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    if args.smoke:
        params["simulation"].update(seconds=600, warmup_s=120, report_seeds=params["simulation"]["report_seeds"][:2])
        params["flow"]["max_iterations"] = 4
        args.trials = args.trials or 2
        print("[SMOKE] Teste de código: os JSONs gravados não servem como calibração; rode de novo sem --smoke.")
    if not SCENARIOS_PATH.is_file():
        raise FileNotFoundError(f"{SCENARIOS_PATH} não existe; rode fetch_data.py primeiro")
    scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    calibration = Calibration(params, scenarios, args.topology)

    coordinate_traffic_lights(calibration)
    previous = json.loads(KRAUSS_OUTPUT_PATH.read_text(encoding="utf-8")) if KRAUSS_OUTPUT_PATH.is_file() else {}
    # Perfis de outras topologias (rodadas com --topology) no formato atual; descarta formatos antigos.
    output = {name: value for name, value in previous.items() if isinstance(value, dict) and "levels" in value}
    output["metadata"] = {
        "source": "CET MSVP (docs/relatorio-msvp-2019-revisao-2-junho-22.pdf e docs/msvp_<ano>/)",
        "calibration": params["cet"]["calibration"],
        "validation": "todo nível observado fora da calibração com velocidade CET",
        "objective": "RMSE da velocidade média espacial (km/h) por rota e nível, rotas de demanda observada",
        "smoke": args.smoke,
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
