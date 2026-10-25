"""T3: análise de sensibilidade (Morris) por rota e regime, em torno do ponto calibrado na T2.

Fatores e faixas:
- accel, decel, tau, sigma, minGap: krauss.bounds;
- speed_factor: speed_factor.interval;
- flow: sensitivity.flow_relative_range vezes o fluxo calibrado em conf/route_flows_replicated.json.

Saídas comparadas: velocidade média (km/h) e CV do tempo de viagem, as duas com a fila pontual de
simulation.py. Cada ponto roda todas as sensitivity.seeds (as mesmas em todos os pontos, para o ruído se
cancelar nas diferenças) e usa a média.

Ruído: como as sementes são as mesmas em todos os pontos, um fator dummy daria efeito exatamente zero e não
serve de piso. O ruído de cada efeito elementar é o erro padrão, entre sementes, do efeito calculado
semente a semente; o ruído do fator é a raiz da média quadrática desses erros nos seus passos.

Morris com sensitivity.levels níveis e passo levels / (2 (levels - 1)) na escala [0, 1] de cada fator.
O efeito elementar é a variação da saída dividida pelo passo, ou seja, o efeito estimado ao percorrer a
faixa inteira do fator. Um fator é sensível numa saída se mu* >= sensitivity.min_effect_kmh (ou
min_effect_cv) e mu* > sensitivity.noise_multiplier vezes o ruído do fator.

Saída: conf/calibration_sensitivity.json, com o desenho e o resultado de cada ponto para reanálise.
"""
import argparse
import json
import math
import random
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

try:
    from .params import KRAUSS_KEYS, PROJECT_DIR, load_calibration_params
    from .simulation import simulate
    from .calibrate_replicated import OUTPUT_PATH as FLOWS_PATH, insertion_ok
except ImportError:
    from params import KRAUSS_KEYS, PROJECT_DIR, load_calibration_params
    from simulation import simulate
    from calibrate_replicated import OUTPUT_PATH as FLOWS_PATH, insertion_ok

sys.path.insert(0, str(PROJECT_DIR / "src"))
from calibrate_stage1 import MAX_WORKERS, load_calibration_data  # noqa: E402


OUTPUT_PATH = PROJECT_DIR / "conf" / "calibration_sensitivity.json"
FACTORS = (*KRAUSS_KEYS, "speed_factor", "flow")
OUTPUTS = {"speed_kmh": "min_effect_kmh", "travel_time_cv": "min_effect_cv"}

_sumo_pool = ThreadPoolExecutor(MAX_WORKERS)


def factor_ranges(params, calibrated_flow):
    ranges = {key: tuple(params["krauss"]["bounds"][key]) for key in KRAUSS_KEYS}
    ranges["speed_factor"] = tuple(params["speed_factor"]["interval"])
    low, high = params["sensitivity"]["flow_relative_range"]
    ranges["flow"] = (calibrated_flow * low, calibrated_flow * high)
    return ranges


def morris_trajectories(trajectories, levels, rng):
    """Pontos na escala [0, 1]: cada trajetória tem len(FACTORS) + 1 pontos e muda um fator por passo."""
    grid = [index / (levels - 1) for index in range(levels)]
    step = levels / (2 * (levels - 1))
    design = []
    for _ in range(trajectories):
        point = {factor: rng.choice(grid) for factor in FACTORS}
        points, changed = [dict(point)], []
        for factor in rng.sample(FACTORS, len(FACTORS)):
            point[factor] = round(point[factor] + step if point[factor] + step <= 1 + 1e-9 else point[factor] - step, 9)
            points.append(dict(point))
            changed.append(factor)
        design.append({"points": points, "changed": changed})
    return design


def scale(point, ranges):
    return {factor: low + point[factor] * (high - low) for factor, (low, high) in ranges.items()}


def evaluate(route_name, route, values, params):
    """Média entre sementes das saídas, com as estatísticas de inserção, no ponto values (escala real)."""
    krauss = {key: values[key] for key in KRAUSS_KEYS}
    trial_route = {**route, "speed_factor": round(values["speed_factor"], 4)}
    flows = {route_name: round(values["flow"])}

    def run(seed):
        return simulate(route["topology"], {route_name: trial_route}, krauss, flows, params["simulation"]["seconds"],
                        min_depart=params["simulation"]["warmup_s"], seed=seed)

    results = list(_sumo_pool.map(run, params["sensitivity"]["seeds"]))
    metrics = [result.get(route_name) for result, _ in results]
    seed_values = {output: [m[output] for m in metrics] if all(metrics) else None for output in OUTPUTS}
    return {
        **{output: statistics.fmean(values) if values else None for output, values in seed_values.items()},
        "seed_values": seed_values,
        "insertion_ok": all(insertion_ok(stats, params["acceptance"]) for _, stats in results),
    }


def morris_indices(design, results, output):
    """mu, mu*, sigma e ruído dos efeitos elementares de cada fator; passos com saída ausente ficam de fora."""
    effects = {factor: [] for factor in FACTORS}
    noise = {factor: [] for factor in FACTORS}
    for trajectory, outputs in zip(design, results):
        for index, factor in enumerate(trajectory["changed"]):
            before, after = outputs[index]["seed_values"][output], outputs[index + 1]["seed_values"][output]
            if before is None or after is None:
                continue
            delta = trajectory["points"][index + 1][factor] - trajectory["points"][index][factor]
            by_seed = [(a - b) / delta for a, b in zip(after, before)]
            effects[factor].append(statistics.fmean(by_seed))
            noise[factor].append(statistics.stdev(by_seed) / math.sqrt(len(by_seed)) if len(by_seed) > 1 else 0.0)
    indices = {}
    for factor, values in effects.items():
        indices[factor] = {
            "mu": statistics.fmean(values) if values else None,
            "mu_star": statistics.fmean(abs(v) for v in values) if values else None,
            "sigma": statistics.stdev(values) if len(values) > 1 else None,
            "noise": math.sqrt(statistics.fmean(n * n for n in noise[factor])) if noise[factor] else None,
            "effects": len(values),
        }
    return indices


def sensitive_factors(indices, threshold, noise_multiplier):
    return [
        factor for factor in FACTORS
        if indices[factor]["mu_star"] is not None
        and indices[factor]["mu_star"] >= threshold
        and indices[factor]["mu_star"] > noise_multiplier * indices[factor]["noise"]
    ]


def analyze_route(route_name, route, regime_name, calibrated, params):
    settings = params["sensitivity"]
    # Sementes do desenho por rota e regime: a análise de uma rota não depende de quais outras rodam junto.
    rng = random.Random(f"{settings['sampler_seed']}:{route_name}:{regime_name}")
    design = morris_trajectories(settings["trajectories"], settings["levels"], rng)
    ranges = factor_ranges(params, calibrated["flow_veh_h"])

    points = [scale(point, ranges) for trajectory in design for point in trajectory["points"]]
    with ThreadPoolExecutor(len(points)) as pool:
        evaluations = list(pool.map(lambda values: evaluate(route_name, route, values, params), points))
    per_trajectory = len(FACTORS) + 1
    results = [evaluations[i:i + per_trajectory] for i in range(0, len(evaluations), per_trajectory)]

    report = {
        "topology": route["topology"],
        "calibrated_flow_veh_h": calibrated["flow_veh_h"],
        "ranges": {factor: [round(low, 4), round(high, 4)] for factor, (low, high) in ranges.items()},
        "points_without_output": sum(e["speed_kmh"] is None for e in evaluations),
        "points_with_insertion_queue": sum(not e["insertion_ok"] for e in evaluations),
        "outputs": {},
        "design": [
            {"changed": trajectory["changed"],
             "points": [{**{f: round(v, 4) for f, v in values.items()}, **result}
                        for values, result in zip((scale(p, ranges) for p in trajectory["points"]), outputs)]}
            for trajectory, outputs in zip(design, results)
        ],
    }
    for output, threshold_key in OUTPUTS.items():
        indices = morris_indices(design, results, output)
        report["outputs"][output] = {
            "threshold": settings[threshold_key],
            "noise_multiplier": settings["noise_multiplier"],
            "indices": indices,
            "sensitive": sensitive_factors(indices, settings[threshold_key], settings["noise_multiplier"]),
        }

    speed = report["outputs"]["speed_kmh"]
    ranking = sorted((f for f in FACTORS if speed["indices"][f]["mu_star"] is not None),
                     key=lambda f: -speed["indices"][f]["mu_star"])
    print(f"[OK] {route_name} ({regime_name}): velocidade mu* "
          + ", ".join(f"{f} {speed['indices'][f]['mu_star']:.1f}" for f in ranking)
          + f" km/h; sensíveis: {', '.join(speed['sensitive']) or 'nenhum'}")
    return report


def topology_summary(output_report):
    """Fatores sensíveis em pelo menos uma rota e regime de cada topologia, por saída."""
    summary = {}
    for regime in output_report["regimes"].values():
        for route in regime["routes"].values():
            topology = summary.setdefault(route["topology"], {output: set() for output in OUTPUTS})
            for output in OUTPUTS:
                topology[output].update(route["outputs"][output]["sensitive"])
    return {
        topology: {output: [f for f in FACTORS if f in factors] for output, factors in outputs.items()}
        for topology, outputs in sorted(summary.items())
    }


def main():
    params = load_calibration_params()
    parser = argparse.ArgumentParser(description="T3: sensibilidade (Morris) em torno do ponto calibrado da T2")
    parser.add_argument("--regime", choices=list(params["target"]["regimes"]), action="append",
                        help="Regime a analisar (repetível); padrão: todos de route_flows_replicated.json")
    parser.add_argument("--route", action="append", help="Rota a analisar (repetível); padrão: todas")
    args = parser.parse_args()

    if not FLOWS_PATH.is_file():
        raise FileNotFoundError(f"{FLOWS_PATH} não existe; rode python3 calibration/calibrate_replicated.py antes")
    calibrated = json.loads(FLOWS_PATH.read_text(encoding="utf-8"))
    routes = load_calibration_data()["routes"]

    tasks = [
        (route_name, regime_name, result)
        for regime_name, regime in calibrated["regimes"].items() if not args.regime or regime_name in args.regime
        for route_name, result in sorted(regime["routes"].items()) if not args.route or route_name in args.route
    ]
    if not tasks:
        raise ValueError("Nenhuma rota calibrada para analisar")
    settings = params["sensitivity"]
    runs = len(tasks) * settings["trajectories"] * (len(FACTORS) + 1) * len(settings["seeds"])
    print(f"Morris: {len(tasks)} rotas×regimes, {settings['trajectories']} trajetórias, "
          f"{len(settings['seeds'])} sementes -> {runs} simulações")

    def run(task):
        route_name, regime_name, result = task
        return task, analyze_route(route_name, routes[route_name], regime_name, result, params)

    with ThreadPoolExecutor(len(tasks)) as pool:
        results = list(pool.map(run, tasks))
    _sumo_pool.shutdown()

    output = {
        "metadata": {
            "method": settings["method"],
            "flows": FLOWS_PATH.name,
            "factors": list(FACTORS),
            "trajectories": settings["trajectories"],
            "levels": settings["levels"],
            "step": settings["levels"] / (2 * (settings["levels"] - 1)),
            "seeds": settings["seeds"],
            "sampler_seed": settings["sampler_seed"],
            "warmup_s": params["simulation"]["warmup_s"],
            "simulation_seconds": params["simulation"]["seconds"],
            "speed_includes_depart_delay": True,
            "effect_unit": "variação da saída ao percorrer a faixa inteira do fator",
        },
        "regimes": {},
    }
    for (route_name, regime_name, _), report in results:
        output["regimes"].setdefault(regime_name, {"routes": {}})["routes"][route_name] = report
    output["topology_summary"] = topology_summary(output)

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Sensibilidade salva em {OUTPUT_PATH}")
    for topology, outputs in output["topology_summary"].items():
        print(f"{topology}: velocidade -> {', '.join(outputs['speed_kmh']) or 'nenhum'}; "
              f"CV do tempo de viagem -> {', '.join(outputs['travel_time_cv']) or 'nenhum'}")


if __name__ == "__main__":
    main()
