import csv
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = BASE_DIR / "conf"
CET_VOLUMES_PATH = CONF_DIR / "cet_volumes_2019.csv"
CET_SPEEDS_PATH = CONF_DIR / "cet_speeds_2019.csv"

# Passenger-car equivalents used to turn CET classified counts into the passenger-only
# flow simulated in SUMO. Assumptions, not CET values: tune for the study area.
PCE_AUTOMOBILE = 1.0
PCE_BUS_TRUCK = 1.5
PCE_MOTORCYCLE = 0.4


def _read_rows(path):
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def _passenger_car_equivalent(row):
    heavy = sum(int(row[column] or 0) for column in ("onibus_urbano", "onibus_fretado", "caminhoes"))
    return (
        PCE_AUTOMOBILE * int(row["automoveis"])
        + PCE_BUS_TRUCK * heavy
        + PCE_MOTORCYCLE * int(row["motos"])
    )


def load_cet_hourly_flows(path=CET_VOLUMES_PATH):
    """{route_name: {start_hour: passenger-car-equivalent veh/h}} for rows mapped to a route."""
    flows = {}
    for row in _read_rows(path):
        route = row.get("route", "").strip()
        if route:
            flows.setdefault(route, {})[int(row["hora_inicio"])] = _passenger_car_equivalent(row)
    return flows


def load_cet_hourly_speeds(path=CET_SPEEDS_PATH):
    """{route_name: {start_hour: km/h}} (GPS floating-car runs) for rows mapped to a route."""
    speeds = {}
    for row in _read_rows(path):
        route = row.get("route", "").strip()
        if route:
            speeds.setdefault(route, {})[int(row["hora_inicio"])] = float(row["velocidade_kmh"])
    return speeds
