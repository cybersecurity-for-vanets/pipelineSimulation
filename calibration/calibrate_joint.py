"""T3: otimização conjunta Krauss × fluxo, com a velocidade como restrição e o CV do tempo de viagem como objetivo.

Um perfil Krauss por topologia, comum a todas as rotas e regimes dela. Os fatores livres são os parâmetros
do Krauss que a sensibilidade (conf/calibration_sensitivity.json) marcou na velocidade ou no CV de alguma
rota da topologia, mais speedDev (joint.speed_dev), que não entrou na sensibilidade e é o parâmetro mais
direto para a dispersão. Os demais ficam em krauss.default.

Em cada trial, cada rota e regime refaz a bisseção da T2 (fluxo, ou speedFactor na highway) com o perfil
do trial, avaliada pela média de joint.seeds. A busca começa num intervalo em torno do valor da T2
(joint.flow_bracket, joint.speed_factor_bracket) e volta ao intervalo inteiro se o alvo ficar fora dele.
Assim a velocidade sempre é reajustada e o Krauss passa a ser escolhido pela dispersão.

Objetivo de um trial:
- viável (todas as rotas e regimes convergidos: velocidade na tolerância e inserção ok): o maior
  |CV simulado - CV TomTom| entre as rotas e regimes;
- inviável: joint.infeasible_penalty + número de rotas e regimes não convergidos + o maior erro de CV,
  para que menos falhas sempre ganhe.

O primeiro trial de cada topologia é krauss.default com speedDev padrão (a referência). TPE com
joint.n_startup_trials aleatórios; para em joint.n_trials, quando o melhor viável tem erro de CV
<= joint.target_max_cv_error, ou quando o melhor não muda em joint.stagnation_patience trials guiados.
Os estudos ficam em results/calibration/krauss_joint_<topologia>.db e são retomados; --fresh recomeça.

Saída: conf/krauss_joint.json.
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import optuna

try:
    from .params import KRAUSS_KEYS, PROJECT_DIR, load_calibration_params
    from .calibrate_replicated import (
        OUTPUT_PATH as FLOWS_PATH, TARGETS_PATH, _sumo_pool, bisect_replicated,
    )
    from .sensitivity import OUTPUT_PATH as SENSITIVITY_PATH
except ImportError:
    from params import KRAUSS_KEYS, PROJECT_DIR, load_calibration_params
    from calibrate_replicated import (
        OUTPUT_PATH as FLOWS_PATH, TARGETS_PATH, _sumo_pool, bisect_replicated,
    )
    from sensitivity import OUTPUT_PATH as SENSITIVITY_PATH

sys.path.insert(0, str(PROJECT_DIR / "src"))
from calibrate_stage1 import bottleneck_lanes, load_calibration_data  # noqa: E402


CONF_DIR = PROJECT_DIR / "conf"
OUTPUT_PATH = CONF_DIR / "krauss_joint.json"
# Estudos Optuna: estado de execução, não configuração, então ficam fora de conf/.
STUDIES_DIR = PROJECT_DIR / "results" / "calibration"
OUT_OF_BRACKET = ("target_faster_than_interval", "target_slower_than_interval")


def study_path(topology):
    return STUDIES_DIR / f"krauss_joint_{topology}.db"


def free_factors(sensitivity, topology):
    """Parâmetros do Krauss sensíveis na velocidade ou no CV em alguma rota da topologia, mais speedDev."""
    summary = sensitivity["topology_summary"][topology]
    sensitive = set(summary["speed_kmh"]) | set(summary["travel_time_cv"])
    return [key for key in KRAUSS_KEYS if key in sensitive] + ["speedDev"]


def factor_bounds(params, factor):
    return params["joint"]["speed_dev"]["bounds"] if factor == "speedDev" else params["krauss"]["bounds"][factor]


def default_profile(params):
    return {**params["krauss"]["default"], "speedDev": params["joint"]["speed_dev"]["default"]}


def route_cases(topology, targets, calibrated, routes):
    """(rota, regime, alvo, resultado da T2) com alvo ok e calibração na T2."""
    cases = []
    for regime_name, regime in calibrated["regimes"].items():
        for route_name, result in sorted(regime["routes"].items()):
            if routes[route_name]["topology"] != topology:
                continue
            target = targets["routes"][route_name]["regimes"][regime_name]
            if target["status"] == "ok" and target["target_travel_time_cv"] is not None:
                cases.append((route_name, regime_name, target, result))
    return cases


def bracket(params, route, result):
    """Intervalo da bisseção em torno do valor da T2 e o intervalo inteiro, cortado nos limites da T2."""
    if result.get("method") == "hcm_flow_speed_factor":
        full = tuple(params["speed_factor"]["interval"])
        width = params["joint"]["speed_factor_bracket"]
        center = result["speed_factor"]
        return "speed_factor", (round(max(full[0], center - width), 4), round(min(full[1], center + width), 4)), full
    lanes = bottleneck_lanes(route["topology"], route["sumo_edges"])
    full = (params["flow"]["min_veh_h"], params["flow"]["max_veh_h_per_lane"] * lanes)
    low, high = params["joint"]["flow_bracket"]
    center = result["flow_veh_h"]
    return "flow_veh_h", (max(full[0], round(center * low)), min(full[1], round(center * high))), full


def evaluate_case(case, profile, routes, params):
    route_name, regime_name, target, result = case
    route = routes[route_name]
    field, near, full = bracket(params, route, result)
    fixed_flow = result["flow_veh_h"] if field == "speed_factor" else None
    joint_params = {**params, "replicas": {"seeds": params["joint"]["seeds"]}}
    found = bisect_replicated(route_name, route, target["target_kmh"], field, near, fixed_flow, profile, joint_params)
    widened = found["stop_reason"] in OUT_OF_BRACKET
    if widened:
        found = bisect_replicated(route_name, route, target["target_kmh"], field, full, fixed_flow, profile, joint_params)
    cv = found["simulated_travel_time_cv"]
    return {
        "route": route_name,
        "regime": regime_name,
        field: found[field],
        "target_kmh": target["target_kmh"],
        "simulated_kmh": found["simulated_mean_kmh"],
        "error_kmh": found["error_kmh"],
        "insertion_ok": found["insertion_ok"],
        "converged": found["converged"],
        "stop_reason": found["stop_reason"],
        "widened_interval": widened,
        "target_travel_time_cv": target["target_travel_time_cv"],
        "simulated_travel_time_cv": cv,
        "cv_error": cv - target["target_travel_time_cv"] if cv is not None else None,
    }


def trial_objective(cases_results, params):
    failures = sum(not case["converged"] for case in cases_results)
    cv_errors = [abs(case["cv_error"]) for case in cases_results if case["cv_error"] is not None]
    max_cv_error = max(cv_errors) if cv_errors else 1.0
    if failures:
        return params["joint"]["infeasible_penalty"] + failures + max_cv_error, False, max_cv_error
    return max_cv_error, True, max_cv_error


def should_stop(study, params):
    settings = params["joint"]
    trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    if len(trials) >= settings["n_trials"]:
        return "n_trials"
    feasible = [t for t in trials if t.user_attrs.get("feasible")]
    if feasible and min(t.value for t in feasible) <= settings["target_max_cv_error"]:
        return "target_max_cv_error"
    guided = [t for t in trials if t.number >= settings["n_startup_trials"]]
    best = min(trials, key=lambda t: t.value) if trials else None
    if best is not None and len([t for t in guided if t.number > best.number]) >= settings["stagnation_patience"]:
        return "best_trial_unchanged"
    return None


def calibrate_topology(topology, cases, factors, routes, params, fresh):
    settings = params["joint"]
    path = study_path(topology)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fresh:
        path.unlink(missing_ok=True)
    study = optuna.create_study(
        study_name=f"joint_{topology}", storage=f"sqlite:///{path}", load_if_exists=True, direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=settings["sampler_seed"], n_startup_trials=settings["n_startup_trials"]),
    )
    # Trials interrompidos numa execução anterior não têm valor; o TPE os ignora.
    if not study.trials:
        study.enqueue_trial({factor: default_profile(params)[factor] for factor in factors})

    stop_reason = should_stop(study, params)
    while stop_reason is None:
        trial = study.ask()
        profile = default_profile(params)
        for factor in factors:
            low, high = factor_bounds(params, factor)
            profile[factor] = trial.suggest_float(factor, low, high)
        with ThreadPoolExecutor(len(cases)) as pool:
            cases_results = list(pool.map(lambda case: evaluate_case(case, profile, routes, params), cases))
        value, feasible, max_cv_error = trial_objective(cases_results, params)
        trial.set_user_attr("feasible", feasible)
        trial.set_user_attr("max_cv_error", max_cv_error)
        trial.set_user_attr("profile", profile)
        trial.set_user_attr("cases", cases_results)
        study.tell(trial, value)
        failures = sum(not case["converged"] for case in cases_results)
        print(f"[{topology}] trial {trial.number}: erro máx. de CV {max_cv_error:.3f}, "
              f"{'viável' if feasible else f'{failures} rota(s)×regime não convergida(s)'}; "
              + ", ".join(f"{k} {v:.2f}" for k, v in profile.items() if k in factors))
        stop_reason = should_stop(study, params)

    trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    best = min(trials, key=lambda t: t.value)
    reference = trials[0]
    return {
        "free_factors": factors,
        "fixed_factors": {k: v for k, v in default_profile(params).items() if k not in factors},
        "stop_reason": stop_reason,
        "trials": len(trials),
        "best_trial": best.number,
        "best_feasible": best.user_attrs["feasible"],
        "best_objective": best.value,
        "best_max_cv_error": best.user_attrs["max_cv_error"],
        "best_profile": best.user_attrs["profile"],
        "best_cases": best.user_attrs["cases"],
        "reference_max_cv_error": reference.user_attrs["max_cv_error"],
        "reference_feasible": reference.user_attrs["feasible"],
        "reference_cases": reference.user_attrs["cases"],
        "history": [
            {"trial": t.number, "objective": t.value, "feasible": t.user_attrs["feasible"],
             "max_cv_error": t.user_attrs["max_cv_error"],
             "profile": {k: round(v, 4) for k, v in t.user_attrs["profile"].items()}}
            for t in trials
        ],
    }


def main():
    params = load_calibration_params()
    parser = argparse.ArgumentParser(description="T3: Krauss × fluxo com velocidade como restrição e CV como objetivo")
    parser.add_argument("--topology", action="append", help="Topologia a calibrar (repetível); padrão: todas")
    parser.add_argument("--fresh", action="store_true", help="Apaga os estudos salvos e recomeça")
    args = parser.parse_args()

    for path, step in ((TARGETS_PATH, "targets.py"), (FLOWS_PATH, "calibrate_replicated.py"),
                       (SENSITIVITY_PATH, "sensitivity.py")):
        if not path.is_file():
            raise FileNotFoundError(f"{path} não existe; rode python3 calibration/{step} antes")
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    calibrated = json.loads(FLOWS_PATH.read_text(encoding="utf-8"))
    sensitivity = json.loads(SENSITIVITY_PATH.read_text(encoding="utf-8"))
    routes = load_calibration_data()["routes"]
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    topologies = [t for t in sorted(sensitivity["topology_summary"]) if not args.topology or t in args.topology]
    jobs = {}
    for topology in topologies:
        cases = route_cases(topology, targets, calibrated, routes)
        if not cases:
            print(f"[PULADA] {topology}: nenhuma rota com alvo de velocidade e CV")
            continue
        jobs[topology] = (cases, free_factors(sensitivity, topology))
        print(f"{topology}: {len(cases)} rotas×regimes; fatores livres {', '.join(jobs[topology][1])}")

    def run(topology):
        cases, factors = jobs[topology]
        return topology, calibrate_topology(topology, cases, factors, routes, params, args.fresh)

    with ThreadPoolExecutor(len(jobs)) as pool:
        results = dict(pool.map(run, jobs))
    _sumo_pool.shutdown()

    output = {
        "metadata": {
            "targets": TARGETS_PATH.name,
            "start_flows": FLOWS_PATH.name,
            "sensitivity": SENSITIVITY_PATH.name,
            "seeds": params["joint"]["seeds"],
            "tolerance_kmh": params["flow"]["tolerance_kmh"],
            "acceptance": params["acceptance"],
            "warmup_s": params["simulation"]["warmup_s"],
            "simulation_seconds": params["simulation"]["seconds"],
            "speed_includes_depart_delay": True,
            "objective": "maior |CV simulado - CV TomTom| entre rotas e regimes, com todas convergidas na velocidade",
        },
        "topologies": results,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Calibração conjunta salva em {OUTPUT_PATH}")
    for topology, result in results.items():
        print(f"{topology}: trial {result['best_trial']} de {result['trials']} ({result['stop_reason']}), "
              f"erro máx. de CV {result['reference_max_cv_error']:.3f} -> {result['best_max_cv_error']:.3f}, "
              f"{'viável' if result['best_feasible'] else 'INVIÁVEL'}; "
              + ", ".join(f"{k} {result['best_profile'][k]:.2f}" for k in result["free_factors"]))


if __name__ == "__main__":
    main()
