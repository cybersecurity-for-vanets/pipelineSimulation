"""Leitura dos dados CET MSVP (conf/cet_volumes.csv e conf/cet_speeds.csv), de vários anos.

As linhas foram transcritas das tabelas por faixa horária dos relatórios MSVP (docs/relatorio-msvp-2019-revisao-2-junho-22.pdf
e docs/msvp_<ano>/). Cada linha tem o ano em `ano` e a rota da simulação em `route`. As chaves devolvidas são
(ano, hora de início).
"""
import csv
from pathlib import Path


from src.env import DATA_DIR

CET_VOLUMES_PATH = DATA_DIR / "cet_volumes.csv"
CET_SPEEDS_PATH = DATA_DIR / "cet_speeds.csv"


def _read_rows(path, years):
    if not path.is_file():
        raise FileNotFoundError(f"Dados CET não encontrados: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return [
            row for row in csv.DictReader(source)
            if row.get("route", "").strip() and (years is None or int(row["ano"]) in years)
        ]


def passenger_car_equivalent(row, pce):
    """Volume do SUMO (só automóveis) em equivalente-carro, com os fatores de cet.pce."""
    return sum(factor * int(row[column] or 0) for column, factor in pce.items())


def load_hourly_volumes(pce, years=None, hours=None, path=CET_VOLUMES_PATH):
    """{rota: {(ano, hora): {"total", "pce_veh_h", "date", "point"}}}, só os anos e horas pedidos (None = todos)."""
    volumes = {}
    for row in _read_rows(path, years):
        key = (int(row["ano"]), int(row["hora_inicio"]))
        if hours is not None and key[1] not in hours:
            continue
        route = volumes.setdefault(row["route"].strip(), {})
        if key in route:
            raise ValueError(f"Volume CET repetido para {row['route']} em {key} em {path.name}")
        route[key] = {
            "total": int(row["total"]),
            "pce_veh_h": passenger_car_equivalent(row, pce),
            "date": row["data"],
            "point": row["ponto"],
        }
    return volumes


def load_hourly_speeds(years=None, hours=None, path=CET_SPEEDS_PATH):
    """{rota: {(ano, hora): {"speed_kmh", "distance_m", "month"}}} (carro-teste com GPS)."""
    speeds = {}
    for row in _read_rows(path, years):
        key = (int(row["ano"]), int(row["hora_inicio"]))
        if hours is not None and key[1] not in hours:
            continue
        route = speeds.setdefault(row["route"].strip(), {})
        if key in route:
            raise ValueError(f"Velocidade CET repetida para {row['route']} em {key} em {path.name}")
        route[key] = {
            "speed_kmh": float(row["velocidade_kmh"]),
            "distance_m": float(row["distancia_m"]),
            "month": row["mes_ano"],
        }
    return speeds
