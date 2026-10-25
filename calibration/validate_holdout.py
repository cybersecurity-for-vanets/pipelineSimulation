"""T4: validação fora da amostra por holdout de dias, dentro de cada regime da T1.

Os dias úteis da T1 (tomtom.days_of_week, sem tomtom.exclude_dates) são divididos em calibração e validação:
validation.fraction dos dias vai para a validação, estratificada por dia da semana (cota proporcional, sobra
pelo maior resto) e sorteada com validation.seed. Cada subconjunto gera o seu alvo com targets.build_targets.

Com os dias de calibração, cada rota e regime refaz a bisseção da T2 (fluxo, ou speedFactor na highway, com
réplicas e aceite de inserção) com o Krauss fixo: o melhor perfil de conf/krauss_joint.json (--model joint) ou
krauss.default com joint.speed_dev.default (--model t2). O ponto calibrado não é simulado de novo: o erro de
validação é a mesma velocidade simulada contra o alvo dos dias de validação, e o mesmo vale para o CV do tempo
de viagem.

Vazamento conhecido: com --model joint, o Krauss da T3 foi escolhido com todos os dias (só pelo alvo de CV).

Saída: conf/calibration_holdout.json.
"""
import argparse
import json
import math
import random
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

try:
    from .params import PROJECT_DIR, WEEKDAYS, load_calibration_params
    from .targets import MULTIDAY_PATH, build_targets, select_days
    from .calibrate_replicated import _sumo_pool, calibrate_route
    from .calibrate_joint import OUTPUT_PATH as JOINT_PATH, default_profile
except ImportError:
    from params import PROJECT_DIR, WEEKDAYS, load_calibration_params
    from targets import MULTIDAY_PATH, build_targets, select_days
    from calibrate_replicated import _sumo_pool, calibrate_route
    from calibrate_joint import OUTPUT_PATH as JOINT_PATH, default_profile

sys.path.insert(0, str(PROJECT_DIR / "src"))
from calibrate_stage1 import load_calibration_data  # noqa: E402


CONF_DIR = PROJECT_DIR / "conf"
OUTPUT_PATH = CONF_DIR / "calibration_holdout.json"


def day_weekdays(multiday):
    return {day: info["weekday"] for route in multiday["routes"].values() for day, info in route["days"].items()}


def split_days(days, weekdays, fraction, seed):
    """(dias de calibração, dias de validação), estratificados por dia da semana."""
    by_weekday = {}
    for day in sorted(days):
        by_weekday.setdefault(weekdays[day], []).append(day)
    quotas = {weekday: len(group) * fraction for weekday, group in by_weekday.items()}
    counts = {weekday: math.floor(quota) for weekday, quota in quotas.items()}
    remaining = round(len(days) * fraction) - sum(counts.values())
    by_remainder = sorted(quotas, key=lambda weekday: (counts[weekday] - quotas[weekday], WEEKDAYS.index(weekday)))
    for weekday in by_remainder[:remaining]:
        counts[weekday] += 1

    rng = random.Random(seed)
    validation = []
    for weekday in sorted(by_weekday, key=WEEKDAYS.index):
        validation.extend(rng.sample(by_weekday[weekday], counts[weekday]))
    return sorted(set(days) - set(validation)), sorted(validation)


def load_profiles(choice, params):
    """Modelo usado e perfil Krauss (com speedDev) por topologia; topologia ausente usa default_profile."""
    if choice == "auto":
        choice = "joint" if JOINT_PATH.is_file() else "t2"
    if choice == "t2":
        return choice, {}, {}
    if not JOINT_PATH.is_file():
        raise FileNotFoundError(f"{JOINT_PATH} não existe; rode python3 calibration/calibrate_joint.py antes")
    topologies = json.loads(JOINT_PATH.read_text(encoding="utf-8"))["topologies"]
    return (choice, {topology: result["best_profile"] for topology, result in topologies.items()},
            {topology: f"{JOINT_PATH.name} (trial {result['best_trial']})" for topology, result in topologies.items()})


def target_summary(regime_target):
    return {
        "status": regime_target["status"],
        "target_kmh": regime_target["target_kmh"],
        "days_with_value": regime_target["days_with_value"],
        "day_std_kmh": regime_target["day_std_kmh"],
        "valid_cells": regime_target["valid_cells"],
        "target_travel_time_cv": regime_target["target_travel_time_cv"],
    }


def route_row(result, calibration_target, validation_target, tolerance):
    simulated = result["simulated_mean_kmh"]
    simulated_cv = result["simulated_travel_time_cv"]
    validation_ok = validation_target["status"] == "ok"
    validation_error = simulated - validation_target["target_kmh"] if validation_ok else None
    day_std = validation_target["day_std_kmh"]

    def cv_error(target):
        cv = target["target_travel_time_cv"]
        return simulated_cv - cv if simulated_cv is not None and cv is not None else None

    return {
        "method": result["method"],
        "flow_veh_h": result["flow_veh_h"],
        "speed_factor": result.get("speed_factor"),
        "converged": result["converged"],
        "stop_reason": result["stop_reason"],
        "insertion_ok": result["insertion_ok"],
        "simulated_kmh": simulated,
        "seed_stdev_kmh": result["seed_stdev_kmh"],
        "simulated_travel_time_cv": simulated_cv,
        "calibration": {**target_summary(calibration_target), "error_kmh": result["error_kmh"],
                        "travel_time_cv_error": cv_error(calibration_target)},
        "validation": {
            **target_summary(validation_target),
            "error_kmh": validation_error,
            "within_tolerance": abs(validation_error) <= tolerance if validation_ok else None,
            # Erro em unidades do desvio entre os dias de validação: perto de 1 é o espalhamento normal entre dias.
            "error_in_day_std": validation_error / day_std if validation_ok and day_std else None,
            "travel_time_cv_error": cv_error(validation_target) if validation_ok else None,
        },
        "target_shift_kmh": validation_target["target_kmh"] - calibration_target["target_kmh"] if validation_ok else None,
        "trace": result["trace"],
    }


def regime_summary(rows, tolerance):
    def absolute(key):
        return [abs(row[key]["error_kmh"]) for row in rows.values() if row[key]["error_kmh"] is not None]

    calibration, validation = absolute("calibration"), absolute("validation")
    return {
        "routes": len(rows),
        "converged": sum(row["converged"] for row in rows.values()),
        "calibration_mae_kmh": statistics.fmean(calibration) if calibration else None,
        "validation_mae_kmh": statistics.fmean(validation) if validation else None,
        "validation_max_abs_error_kmh": max(validation) if validation else None,
        "validation_within_tolerance": sum(error <= tolerance for error in validation),
        "validation_routes": len(validation),
    }


def format_number(value, pattern):
    return pattern.format(value) if value is not None else "-"


def print_summary(output):
    meta = output["metadata"]
    print(f"Calibração: {len(meta['calibration_days'])} dias; validação: {len(meta['validation_days'])} dias "
          f"({', '.join(meta['validation_days'])})")
    for regime_name, regime in output["regimes"].items():
        for route_name, row in regime["routes"].items():
            calibration, validation = row["calibration"], row["validation"]
            print(
                f"[{'OK' if row['converged'] else 'AVISO'}] {route_name:40s} {regime_name:9s} "
                f"sim {row['simulated_kmh']:5.1f}  calib {calibration['target_kmh']:5.1f} "
                f"({calibration['error_kmh']:+5.2f})  valid {format_number(validation['target_kmh'], '{:5.1f}')} "
                f"({format_number(validation['error_kmh'], '{:+5.2f}')}, "
                f"{format_number(validation['error_in_day_std'], '{:+.1f}')} dp entre dias)"
            )
        summary = regime["summary"]
        print(f"  {regime_name}: MAE calibração {format_number(summary['calibration_mae_kmh'], '{:.2f}')} km/h, "
              f"validação {format_number(summary['validation_mae_kmh'], '{:.2f}')} km/h "
              f"(máx. {format_number(summary['validation_max_abs_error_kmh'], '{:.2f}')}); "
              f"{summary['validation_within_tolerance']}/{summary['validation_routes']} na tolerância; "
              f"{summary['converged']}/{summary['routes']} convergidas")
        for route_name, reason in regime["skipped_routes"].items():
            print(f"[PULADA] {route_name} ({regime_name}): {reason}")


def main():
    params = load_calibration_params()
    regime_names = list(params["target"]["regimes"])
    parser = argparse.ArgumentParser(description="T4: validação por holdout de dias dentro de cada regime")
    parser.add_argument("--model", choices=("auto", "t2", "joint"), default="auto",
                        help="Krauss fixo: joint = melhor perfil de krauss_joint.json; t2 = krauss.default; "
                             "auto = joint se existir")
    parser.add_argument("--regime", choices=regime_names, action="append", help="Regime (repetível); padrão: todos")
    parser.add_argument("--route", action="append", help="Rota (repetível); padrão: todas com alvo")
    args = parser.parse_args()

    if not MULTIDAY_PATH.is_file():
        raise FileNotFoundError(f"{MULTIDAY_PATH} não existe; rode python3 src/fetch_data.py --multi-day antes")
    multiday = json.loads(MULTIDAY_PATH.read_text(encoding="utf-8"))
    days, _ = select_days(multiday, params)
    calibration_days, validation_days = split_days(days, day_weekdays(multiday), params["validation"]["fraction"],
                                                   params["validation"]["seed"])
    if not calibration_days or not validation_days:
        raise ValueError(f"Divisão vazia: {len(calibration_days)} dias de calibração e {len(validation_days)} de validação")
    calibration_targets = build_targets(multiday, params, calibration_days)
    validation_targets = build_targets(multiday, params, validation_days)
    model, profiles, sources = load_profiles(args.model, params)
    calibration_data = load_calibration_data()
    routes = calibration_data["routes"]

    tasks, skipped = [], {}
    for regime_name in args.regime or regime_names:
        for route_name, target in sorted(calibration_targets["routes"].items()):
            if args.route and route_name not in args.route:
                continue
            regime_target = target["regimes"][regime_name]
            if not routes.get(route_name, {}).get("sumo_edges"):
                skipped.setdefault(regime_name, {})[route_name] = "sem caminho SUMO"
            elif regime_target["status"] != "ok":
                skipped.setdefault(regime_name, {})[route_name] = "sem_alvo nos dias de calibração"
            else:
                tasks.append((route_name, regime_name, regime_target))
    if not tasks:
        raise ValueError("Nenhuma rota com alvo nos dias de calibração")
    print(f"Modelo: {model}; {len(tasks)} rotas×regimes; calibração em {len(calibration_days)} dias, "
          f"validação em {len(validation_days)}")

    def run(task):
        route_name, regime_name, regime_target = task
        route = routes[route_name]
        krauss = profiles.get(route["topology"], default_profile(params))
        return task, calibrate_route(route_name, route, regime_name, regime_target, calibration_data, params, krauss)

    # As threads de rota só esperam; quem limita os processos SUMO é _sumo_pool.
    with ThreadPoolExecutor(len(tasks)) as pool:
        results = list(pool.map(run, tasks))
    _sumo_pool.shutdown()

    tolerance = params["flow"]["tolerance_kmh"]
    output = {
        "metadata": {
            "model": model,
            "krauss_source": {topology: sources.get(topology, "krauss.default + joint.speed_dev.default")
                              for topology in sorted({routes[name]["topology"] for name, _, _ in tasks})},
            "krauss_leak": "o Krauss da T3 foi escolhido com todos os dias (alvo de CV)" if model == "joint" else None,
            "days_of_week": params["tomtom"]["days_of_week"],
            "validation": params["validation"],
            "calibration_days": calibration_days,
            "validation_days": validation_days,
            "aggregation": params["target"]["aggregation"],
            "seeds": params["replicas"]["seeds"],
            "tolerance_kmh": tolerance,
            "warmup_s": params["simulation"]["warmup_s"],
            "simulation_seconds": params["simulation"]["seconds"],
            "speed_includes_depart_delay": True,
        },
        "regimes": {},
    }
    for (route_name, regime_name, calibration_target), result in results:
        validation_target = validation_targets["routes"][route_name]["regimes"][regime_name]
        regime = output["regimes"].setdefault(regime_name, {"routes": {}, "skipped_routes": skipped.get(regime_name, {})})
        regime["routes"][route_name] = {"topology": routes[route_name]["topology"],
                                        **route_row(result, calibration_target, validation_target, tolerance)}
    for regime_name, names in skipped.items():
        output["regimes"].setdefault(regime_name, {"routes": {}, "skipped_routes": names})
    for regime in output["regimes"].values():
        regime["summary"] = regime_summary(regime["routes"], tolerance)

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Validação fora da amostra salva em {OUTPUT_PATH}")
    print_summary(output)


if __name__ == "__main__":
    main()
