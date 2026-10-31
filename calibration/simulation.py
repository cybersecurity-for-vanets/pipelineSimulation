"""Uma simulação SUMO das rotas de calibração e as métricas por rota que os scripts de calibração comparam.

Fila pontual: o tempo de viagem inclui o departDelay. Nas rotas cuja primeira aresta é curta e termina em
semáforo, o veículo que espera inserção está, na prática, parado no primeiro vermelho; sem esse tempo a
velocidade fica alta demais. O warm-up usa a partida pretendida (depart - departDelay), quando o veículo
chega à fila. A velocidade comparável à da CET (carro-teste no trecho) é a velocidade média espacial
das viagens: distância total / tempo total.
"""
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from .params import PROJECT_DIR
except ImportError:
    from params import PROJECT_DIR

sys.path.insert(0, str(PROJECT_DIR / "src"))
from generate_routes import SUMO_STEP_LENGTH, build_route_file, safe_id, tls_offsets_path  # noqa: E402
from sumo_route_utils import network_path  # noqa: E402


RAW_DATA_DIR = PROJECT_DIR / "results" / "rawData"
SUMO_BINARY = os.environ.get("SUMO_BINARY") or shutil.which("sumo")


def read_insertion_stats(tripinfo_path, statistic_path):
    """Fila de inserção e teleportes de uma simulação."""
    trips = ET.parse(tripinfo_path).getroot().findall("tripinfo")
    delays = [float(trip.get("departDelay", 0)) for trip in trips]
    statistics_root = ET.parse(statistic_path).getroot()
    vehicles = statistics_root.find("vehicles")
    teleports = statistics_root.find("teleports")
    return {
        "loaded": int(vehicles.get("loaded", 0)),
        "inserted": int(vehicles.get("inserted", 0)),
        "waiting_at_end": int(vehicles.get("waiting", 0)),
        "teleports": int(teleports.get("total", 0)),
        "completed_trips": len(trips),
        # Atrasos de até um passo são quantização do instante de inserção, não fila.
        "delayed_departures": sum(1 for delay in delays if delay > SUMO_STEP_LENGTH),
        "max_depart_delay_s": max(delays, default=0.0),
    }


def read_trip_metrics(tripinfo_path, route_names, min_depart=0.0):
    """Por rota: velocidade média espacial (km/h) e CV do tempo de viagem das viagens completas.

    Só entram as viagens com partida pretendida a partir de min_depart. Rotas sem viagem ficam de fora.
    """
    flow_to_route = {f"flow_{safe_id(name)}": name for name in route_names}
    trips = {name: [] for name in route_names}
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
            trips[route_name].append((distance, duration))

    metrics = {}
    for route_name, rows in trips.items():
        if not rows:
            continue
        durations = [duration for _, duration in rows]
        mean_duration = statistics.fmean(durations)
        metrics[route_name] = {
            "speed_kmh": sum(distance for distance, _ in rows) / sum(durations) * 3.6,
            "travel_time_cv": statistics.pstdev(durations) / mean_duration if len(rows) > 1 else 0.0,
            "trips": len(rows),
        }
    return metrics


def run_sumo(topology, routes, krauss, route_flows, seconds, seed, read_outputs, extra_args=()):
    """Uma simulação; read_outputs(pasta) lê as saídas antes de a pasta temporária ser apagada.

    A pasta tem tripinfo.xml e statistics.xml; extra_args pode pedir outras saídas dentro dela, com caminhos
    relativos a ela (o SUMO roda com cwd na pasta temporária).
    """
    if not SUMO_BINARY:
        raise FileNotFoundError("Binário do SUMO não encontrado; defina SUMO_BINARY ou coloque sumo no PATH")

    route_xml = build_route_file(topology, routes, 0, krauss, route_flows)
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"calib_{topology}_", dir=RAW_DATA_DIR) as tmp_dir:
        tmp_path = Path(tmp_dir)
        route_file = tmp_path / "calibration.rou.xml"
        tripinfo_file = tmp_path / "tripinfo.xml"
        statistic_file = tmp_path / "statistics.xml"
        route_file.write_text(route_xml, encoding="utf-8")

        command = [
            SUMO_BINARY,
            "--net-file", str(network_path(topology)),
            "--route-files", str(route_file),
            "--tripinfo-output", str(tripinfo_file),
            "--statistic-output", str(statistic_file),
            "--end", str(seconds),
            "--step-length", str(SUMO_STEP_LENGTH),
            "--seed", str(seed),
            "--no-step-log", "true",
            "--duration-log.disable", "true",
            *extra_args,
        ]
        if tls_offsets_path(topology).is_file():
            command += ["--additional-files", str(tls_offsets_path(topology))]
        result = subprocess.run(command, capture_output=True, text=True, cwd=tmp_path)
        if result.returncode != 0:
            raise RuntimeError(f"SUMO falhou na topologia {topology}: {result.stderr.strip()}")
        return read_outputs(tmp_path)


def simulate(topology, routes, krauss, route_flows, seconds, min_depart=0.0, seed=1):
    """Uma simulação; devolve (métricas por rota de read_trip_metrics, estatísticas de inserção)."""
    def read_outputs(tmp_path):
        tripinfo_file = tmp_path / "tripinfo.xml"
        return (read_trip_metrics(tripinfo_file, list(routes), min_depart),
                read_insertion_stats(tripinfo_file, tmp_path / "statistics.xml"))

    return run_sumo(topology, routes, krauss, route_flows, seconds, seed, read_outputs)
