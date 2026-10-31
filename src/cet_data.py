"""Leitura dos dados CET MSVP 2019 (conf/cet_volumes_2019.csv e conf/cet_speeds_2019.csv).

As linhas foram transcritas das tabelas por faixa horária do relatório (seções 5.1.3, 5.1.4, 5.3.2 e 5.3.3 de
docs/relatorio-msvp-2019-revisao-2-junho-22.pdf). Cada linha tem a rota da simulação em `route`.
"""
import csv
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = BASE_DIR / "conf"
CET_VOLUMES_PATH = CONF_DIR / "cet_volumes_2019.csv"
CET_SPEEDS_PATH = CONF_DIR / "cet_speeds_2019.csv"


def _read_rows(path):
    if not path.is_file():
        raise FileNotFoundError(f"Dados CET não encontrados: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return [row for row in csv.DictReader(source) if row.get("route", "").strip()]


def passenger_car_equivalent(row, pce):
    """Volume do SUMO (só automóveis) em equivalente-carro, com os fatores de cet.pce."""
    return sum(factor * int(row[column] or 0) for column, factor in pce.items())


def load_hourly_volumes(pce, path=CET_VOLUMES_PATH):
    """{rota: {hora: {"total": veic/h contados, "pce_veh_h": equivalente-carro, "date": dd/mm/aaaa, "point": ...}}}."""
    volumes = {}
    for row in _read_rows(path):
        hour = int(row["hora_inicio"])
        route = volumes.setdefault(row["route"].strip(), {})
        if hour in route:
            raise ValueError(f"Volume CET repetido para {row['route']} às {hour}h em {path.name}")
        route[hour] = {
            "total": int(row["total"]),
            "pce_veh_h": passenger_car_equivalent(row, pce),
            "date": row["data"],
            "point": row["ponto"],
        }
    return volumes


def load_hourly_speeds(path=CET_SPEEDS_PATH):
    """{rota: {hora: {"speed_kmh": ..., "distance_m": ..., "month": ...}}} (carro-teste com GPS)."""
    speeds = {}
    for row in _read_rows(path):
        hour = int(row["hora_inicio"])
        route = speeds.setdefault(row["route"].strip(), {})
        if hour in route:
            raise ValueError(f"Velocidade CET repetida para {row['route']} às {hour}h em {path.name}")
        route[hour] = {
            "speed_kmh": float(row["velocidade_kmh"]),
            "distance_m": float(row["distancia_m"]),
            "month": row["mes_ano"],
        }
    return speeds
