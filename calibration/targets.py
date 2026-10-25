"""T1: alvo de velocidade por rota e regime (pico, entrepico) a partir de conf/tomtom_multiday.json.

Uma célula (rota, data, faixa horária) entra no alvo se tiver velocidade e mediana de amostra dos
segmentos >= target.min_normalized_sample_size. Hora sem dado ou com amostra baixa fica de fora, sem
interpolação. O alvo de cada regime agrega as células válidas com target.aggregation. A incerteza é o
espalhamento dos valores diários. Tudo que é constante vem de conf/calibration_params.json.

Das mesmas células válidas sai o alvo de dispersão (T3): CV do tempo de viagem da célula,
travel_time_std_s / avg_travel_time_s, agregado do mesmo jeito.

Saída: conf/calibration_targets.json. Horas são inícios de faixa: 7 significa 07:00-08:00.
"""
import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

try:
    from .params import PROJECT_DIR, load_calibration_params
except ImportError:
    from params import PROJECT_DIR, load_calibration_params


CONF_DIR = PROJECT_DIR / "conf"
MULTIDAY_PATH = CONF_DIR / "tomtom_multiday.json"
TARGETS_PATH = CONF_DIR / "calibration_targets.json"
AGGREGATORS = {
    "arithmetic": statistics.fmean,
    "harmonic": statistics.harmonic_mean,
    "median": statistics.median,
}


def hour_name(hour):
    return f"hour_{hour:02d}_{hour + 1:02d}"


def select_days(multiday, params):
    """Datas usadas no alvo e as excluídas, com o motivo. Dias da semana e exclude_dates vêm de calibration_params.json."""
    weekdays = {}
    for route in multiday["routes"].values():
        for day, info in route["days"].items():
            weekdays[day] = info["weekday"]
    allowed_weekdays = set(params["tomtom"]["days_of_week"])
    excluded_dates = set(params["tomtom"]["exclude_dates"])

    days_used = []
    days_excluded = {}
    for day in sorted(weekdays):
        if day in excluded_dates:
            days_excluded[day] = "tomtom.exclude_dates"
        elif weekdays[day] not in allowed_weekdays:
            days_excluded[day] = f"dia da semana {weekdays[day]} fora de tomtom.days_of_week"
        else:
            days_used.append(day)
    return days_used, days_excluded


def cell_status(cell, min_sample):
    if cell["speed_kmh"] is None:
        return "sem_dado"
    sample = cell["segment_sample_median"]
    if sample is None or sample < min_sample:
        return "amostra_baixa"
    return "valida"


def build_regime_target(route_days, hours, days_used, min_sample, aggregate):
    valid_values = []
    valid_cvs, per_day_cv = [], {}
    valid_hours = set()
    reasons_by_hour = {hour: Counter() for hour in hours}
    per_day = {}
    cells_total = 0

    for day in days_used:
        day_values, day_cvs = [], []
        for hour in hours:
            cell = route_days[day]["hours"][hour_name(hour)]
            cells_total += 1
            status = cell_status(cell, min_sample)
            if status == "valida":
                day_values.append(cell["speed_kmh"])
                valid_hours.add(hour)
                if cell.get("travel_time_std_s") is not None and cell.get("avg_travel_time_s"):
                    day_cvs.append(cell["travel_time_std_s"] / cell["avg_travel_time_s"])
            else:
                reasons_by_hour[hour][status] += 1
        per_day[day] = aggregate(day_values) if day_values else None
        valid_values.extend(day_values)
        per_day_cv[day] = aggregate(day_cvs) if day_cvs else None
        valid_cvs.extend(day_cvs)

    day_values = [value for value in per_day.values() if value is not None]
    day_cvs = [value for value in per_day_cv.values() if value is not None]
    return {
        "status": "ok" if valid_values else "sem_alvo",
        "target_kmh": aggregate(valid_values) if valid_values else None,
        "hours_used": [hour_name(hour) for hour in hours if hour in valid_hours],
        "hours_excluded": {
            hour_name(hour): dict(reasons_by_hour[hour])
            for hour in hours if hour not in valid_hours
        },
        "valid_cells": len(valid_values),
        "total_cells": cells_total,
        "per_day_kmh": per_day,
        "days_with_value": len(day_values),
        "day_std_kmh": statistics.stdev(day_values) if len(day_values) > 1 else None,
        "day_min_kmh": min(day_values) if day_values else None,
        "day_max_kmh": max(day_values) if day_values else None,
        "target_travel_time_cv": aggregate(valid_cvs) if valid_cvs else None,
        "travel_time_cv_cells": len(valid_cvs),
        "day_std_travel_time_cv": statistics.stdev(day_cvs) if len(day_cvs) > 1 else None,
    }


def build_targets(multiday, params, days=None):
    """Alvos por rota e regime; days restringe as datas (subconjunto de calibração ou validação da T4)."""
    target_params = params["target"]
    aggregate = AGGREGATORS[target_params["aggregation"]]
    min_sample = target_params["min_normalized_sample_size"]
    regimes = {name: regime["hours"] for name, regime in target_params["regimes"].items()}
    days_used, days_excluded = select_days(multiday, params)
    if days is not None:
        days_excluded.update({day: "fora do subconjunto pedido" for day in days_used if day not in days})
        days_used = [day for day in days_used if day in days]
    if not days_used:
        raise ValueError("Nenhuma data sobrou depois de tomtom.days_of_week e tomtom.exclude_dates")

    routes = {}
    for route_name, route in multiday["routes"].items():
        routes[route_name] = {
            "topology": route["topology"],
            "route_key": route["route_key"],
            "distance_m": route["distance_m"],
            "regimes": {
                regime_name: build_regime_target(route["days"], hours, days_used, min_sample, aggregate)
                for regime_name, hours in regimes.items()
            },
        }

    return {
        "source": MULTIDAY_PATH.name,
        "date_from": multiday["date_from"],
        "date_to": multiday["date_to"],
        "days_used": days_used,
        "days_excluded": days_excluded,
        "aggregation": target_params["aggregation"],
        "min_normalized_sample_size": min_sample,
        "regimes": regimes,
        "skipped_routes": multiday["skipped_routes"],
        "routes": routes,
    }


def print_summary(targets):
    print(f"Datas usadas: {len(targets['days_used'])} ({targets['days_used'][0]} a {targets['days_used'][-1]})")
    print(f"Datas excluídas: {len(targets['days_excluded'])}")
    print(f"Agregação: {targets['aggregation']}; amostra mínima: {targets['min_normalized_sample_size']}")
    for route_name, route in targets["routes"].items():
        for regime_name, regime in route["regimes"].items():
            if regime["status"] == "ok":
                spread = (
                    f"dp {regime['day_std_kmh']:.1f} km/h em {regime['days_with_value']} dias"
                    if regime["day_std_kmh"] is not None else f"{regime['days_with_value']} dia(s)"
                )
                cv = regime["target_travel_time_cv"]
                cv_text = f"{cv:.3f}" if cv is not None else "  -  "
                print(
                    f"[OK] {route_name:40s} {regime_name:9s} {regime['target_kmh']:6.1f} km/h  CV {cv_text}  "
                    f"horas {len(regime['hours_used'])}/{len(regime['hours_used']) + len(regime['hours_excluded'])}  {spread}"
                )
            else:
                print(f"[SEM ALVO] {route_name:40s} {regime_name:9s} nenhuma célula atinge a amostra mínima")


def main():
    parser = argparse.ArgumentParser(description="T1: alvo por rota e regime a partir do TomTom multi-dia")
    parser.add_argument("--input", type=Path, default=MULTIDAY_PATH, help="tomtom_multiday.json gerado pelo fetch_data")
    parser.add_argument("--output", type=Path, default=TARGETS_PATH, help="Onde gravar calibration_targets.json")
    args = parser.parse_args()

    if not args.input.is_file():
        raise FileNotFoundError(f"{args.input} não existe; rode python3 src/fetch_data.py --multi-day antes")
    params = load_calibration_params()
    multiday = json.loads(args.input.read_text(encoding="utf-8"))
    targets = build_targets(multiday, params)

    args.output.write_text(json.dumps(targets, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Alvos salvos em {args.output}")
    print_summary(targets)


if __name__ == "__main__":
    main()
