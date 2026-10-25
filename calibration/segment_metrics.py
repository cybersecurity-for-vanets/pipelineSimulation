"""T5: perfil de velocidade por segmento TomTom e dispersão do tempo de viagem, simulado × TomTom.

TomTom: os segmentos de cada rota vêm dos brutos dos jobs (raw_file em conf/tomtom_multiday.json). A
velocidade de um segmento num regime agrega, com target.aggregation, o harmonicAverageSpeed das células
(data, hora) com os dias da T1 (conf/calibration_targets.json) e normalizedSampleSize do segmento
>= segments.min_normalized_sample_size. Segmento com menos de segments.min_valid_cells células válidas fica
de fora e é listado com o motivo.

SUMO: cada rota roda sozinha, como na T2, com o modelo escolhido em --model (o melhor perfil de
conf/krauss_joint.json, ou krauss.default com o fluxo/speedFactor de conf/route_flows_replicated.json),
uma vez por semente de segments.seeds, com FCD a cada segments.fcd_period_s. As fronteiras dos segmentos
TomTom são projetadas no caminho SUMO (arestas e conectores de junção). O instante em que cada veículo
cruza cada fronteira sai da interpolação linear da trajetória, e a velocidade do segmento é
distância total / tempo total (média harmônica, como o harmonicAverageSpeed). Fila pontual, como na T2:
o departDelay entra no tempo do primeiro segmento. Só entram viagens completas com partida pretendida a
partir de simulation.warmup_s.

Dispersão (dispersion.metric), no trecho coberto pela TomTom:
- travel_time_cv: desvio padrão / média do tempo de viagem;
- travel_time_p95_median: percentil 95 / mediana (na TomTom, último de travelTimePercentiles, que tem os
  19 percentis de 5% a 95%, sobre medianTravelTime).
O valor TomTom agrega as células válidas da T1 (mesmo critério de targets.cell_status).

Saída: conf/calibration_segments.json.
"""
import argparse
import bisect
import json
import math
import re
import statistics
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

try:
    from .params import PROJECT_DIR, load_calibration_params
    from .simulation import run_sumo
    from .targets import AGGREGATORS, MULTIDAY_PATH, TARGETS_PATH, cell_status, hour_name
    from .calibrate_replicated import OUTPUT_PATH as FLOWS_PATH
    from .calibrate_joint import OUTPUT_PATH as JOINT_PATH, default_profile
except ImportError:
    from params import PROJECT_DIR, load_calibration_params
    from simulation import run_sumo
    from targets import AGGREGATORS, MULTIDAY_PATH, TARGETS_PATH, cell_status, hour_name
    from calibrate_replicated import OUTPUT_PATH as FLOWS_PATH
    from calibrate_joint import OUTPUT_PATH as JOINT_PATH, default_profile

sys.path.insert(0, str(PROJECT_DIR / "src"))
from calibrate_stage1 import MAX_WORKERS, load_calibration_data  # noqa: E402
from generate_routes import safe_id  # noqa: E402
from sumo_route_utils import _shape_points, load_network  # noqa: E402


CONF_DIR = PROJECT_DIR / "conf"
OUTPUT_PATH = CONF_DIR / "calibration_segments.json"


# ---------------------------------------------------------------- TomTom

def load_raw_routes(multiday, route_names):
    """Por rota: segmentos na ordem da rota (com as células de cada um) e resumos por (data, hora)."""
    routes = {name: {"segments": [], "summaries": {}} for name in route_names}
    for job in multiday["jobs"]:
        raw = json.loads((CONF_DIR / job["raw_file"]).read_text(encoding="utf-8"))
        dates = {str(item["@id"]): item.get("from") or item.get("name") for item in raw["dateRanges"]}
        hours = {str(item["@id"]): item["name"] for item in raw["timeSets"]}

        def key(item):
            return dates.get(str(item.get("dateRange"))), hours.get(str(item.get("timeSet")))

        for route in raw["routes"]:
            entry = routes.get(route["routeName"])
            if entry is None:
                continue
            for summary in route.get("summaries", []):
                entry["summaries"][key(summary)] = summary
            segments = entry["segments"]
            for index, segment in enumerate(route["segmentResults"]):
                segment_id = segment.get("newSegmentId") or str(segment["segmentId"])
                if index == len(segments):
                    segments.append({
                        "segment_id": segment_id,
                        "street": segment.get("streetName"),
                        "distance_m": float(segment["distance"]),
                        "shape": list(_shape_points(segment.get("shape"))),
                        "cells": {},
                    })
                elif segments[index]["segment_id"] != segment_id:
                    raise ValueError(f"{route['routeName']}: segmento {index} difere entre os jobs "
                                     f"({segments[index]['segment_id']} × {segment_id})")
                for item in segment.get("segmentTimeResults", []):
                    segments[index]["cells"][key(item)] = (item.get("harmonicAverageSpeed"),
                                                           item.get("normalizedSampleSize"))
    return routes


def segment_target(segment, days, hours, params, aggregate):
    """Velocidade TomTom do segmento no regime, com a contagem das células excluídas por motivo."""
    settings = params["segments"]
    values, no_data, low_sample = [], 0, 0
    for day in days:
        for hour in hours:
            speed, sample = segment["cells"].get((day, hour_name(hour)), (None, None))
            if not speed or speed <= 0:
                no_data += 1
            elif sample is None or sample < settings["min_normalized_sample_size"]:
                low_sample += 1
            else:
                values.append(float(speed))
    ok = len(values) >= settings["min_valid_cells"]
    return {
        "status": "ok" if ok else "amostra_insuficiente",
        "tomtom_kmh": aggregate(values) if values else None,
        "valid_cells": len(values),
        "cells_without_data": no_data,
        "cells_low_sample": low_sample,
    }


def cell_dispersion(summary, metric):
    if metric == "travel_time_cv":
        mean, std = summary.get("averageTravelTime"), summary.get("travelTimeStandardDeviation")
        return std / mean if mean and std is not None else None
    percentiles, median = summary.get("travelTimePercentiles") or [], summary.get("medianTravelTime")
    return percentiles[-1] / median if len(percentiles) == 19 and median else None


def tomtom_dispersion(route_days, summaries, days, hours, params, aggregate):
    """Dispersão do tempo de viagem nas células válidas da T1."""
    metric = params["dispersion"]["metric"]
    min_sample = params["target"]["min_normalized_sample_size"]
    values = []
    for day in days:
        for hour in hours:
            if cell_status(route_days[day]["hours"][hour_name(hour)], min_sample) != "valida":
                continue
            value = cell_dispersion(summaries.get((day, hour_name(hour)), {}), metric)
            if value is not None:
                values.append(value)
    return aggregate(values) if values else None, len(values)


# ---------------------------------------------------------------- SUMO

def lonlat_to_xy(net, longitude, latitude):
    """Coordenada da rede SUMO; sem pyproj, faz a projeção UTM WGS84 do projParameter da rede (série de Krüger)."""
    try:
        return net.convertLonLat2XY(longitude, latitude)
    except RuntimeError:
        pass
    proj = net._location["projParameter"]
    zone = re.search(r"\+zone=(\d+)", proj)
    if "+proj=utm" not in proj or zone is None or ("WGS84" not in proj and "wgs84" not in proj):
        raise RuntimeError(f"Sem pyproj, só projeções UTM WGS84 são suportadas; a rede usa {proj!r}")
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    n = f / (2 - f)
    big_a = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    alphas = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16, 13 * n ** 2 / 48 - 3 * n ** 3 / 5, 61 * n ** 3 / 240)
    phi = math.radians(latitude)
    delta = math.radians(longitude - ((int(zone.group(1)) - 1) * 6 - 180 + 3))
    c = 2 * math.sqrt(n) / (1 + n)
    t = math.sinh(math.atanh(math.sin(phi)) - c * math.atanh(c * math.sin(phi)))
    xi, eta = math.atan2(t, math.cos(delta)), math.atanh(math.sin(delta) / math.sqrt(1 + t * t))
    easting = 500000 + k0 * big_a * (eta + sum(alpha * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
                                               for j, alpha in enumerate(alphas, 1)))
    northing = k0 * big_a * (xi + sum(alpha * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
                                      for j, alpha in enumerate(alphas, 1)))
    if "+south" in proj:
        northing += 10000000
    offset_x, offset_y = net.getLocationOffset()
    return easting + offset_x, northing + offset_y


class RouteGeometry:
    """Caminho SUMO de uma rota em posição corrida (m), com as arestas internas dos conectores de junção.

    starts e ends cobrem as arestas normais e as internas do caminho mais curto entre arestas seguidas (o
    mesmo de _junction_connector_length). Um veículo numa aresta interna fora desse caminho cai no fallback
    de read_trajectories.
    """

    def __init__(self, net, edge_ids):
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("Rota SUMO repete aresta; a posição corrida fica ambígua")
        self.edge_ids = edge_ids
        self.starts, self.ends = {}, {}
        position = 0.0
        for index, edge_id in enumerate(edge_ids):
            edge = net.getEdge(edge_id)
            self.starts[edge_id] = position
            position += edge.getLength()
            self.ends[edge_id] = position
            if index + 1 < len(edge_ids):
                path, _ = net.getShortestPath(edge, net.getEdge(edge_ids[index + 1]), vClass="passenger",
                                              withInternal=True)
                for internal in path or []:
                    if internal.getID().startswith(":"):
                        self.starts[internal.getID()] = position
                        position += internal.getLength()
                        self.ends[internal.getID()] = position
        self.length = position
        self.next_start = {edge_ids[i]: self.starts[edge_ids[i + 1]] for i in range(len(edge_ids) - 1)}

    def project(self, net, points):
        """Posição no caminho de cada ponto (lon, lat), sem voltar atrás, e a distância da projeção (m)."""
        positions, distances = [], []
        current = 0
        for longitude, latitude in points:
            xy = lonlat_to_xy(net, longitude, latitude)
            best = None
            for index in range(current, len(self.edge_ids)):
                lane = net.getEdge(self.edge_ids[index]).getLanes()[0]
                offset, distance = lane.getClosestLanePosAndDist(xy)
                if best is None or distance < best[0]:
                    best = (distance, index, offset)
            distance, current, offset = best
            position = self.starts[self.edge_ids[current]] + offset
            positions.append(max(position, positions[-1]) if positions else position)
            distances.append(distance)
        return positions, distances


def segment_bounds(net, geometry, segments):
    """As n+1 fronteiras dos n segmentos TomTom no caminho SUMO: início do primeiro e fim de cada um."""
    points = [segments[0]["shape"][0]] + [segment["shape"][-1] for segment in segments]
    return geometry.project(net, points)


def read_trajectories(fcd_path, flow_id, geometry):
    """Por veículo da rota: lista de (tempo, posição corrida no caminho, velocidade), com posição não decrescente."""
    trajectories = {}
    last_edge = {}
    for _, element in ET.iterparse(fcd_path, events=("end",)):
        if element.tag != "timestep":
            continue
        time = float(element.get("time"))
        for vehicle in element.iter("vehicle"):
            vehicle_id = vehicle.get("id")
            if vehicle_id.rsplit(".", 1)[0] != flow_id:
                continue
            edge_id = vehicle.get("lane").rsplit("_", 1)[0]
            pos = float(vehicle.get("pos"))
            if edge_id in geometry.starts:
                if not edge_id.startswith(":"):
                    last_edge[vehicle_id] = edge_id
                position = geometry.starts[edge_id] + pos
            elif edge_id.startswith(":") and vehicle_id in last_edge:
                # Aresta interna fora do caminho mais curto: a partir do fim da última aresta normal, sem passar
                # o início da próxima.
                previous = last_edge[vehicle_id]
                position = min(geometry.ends[previous] + pos, geometry.next_start.get(previous, geometry.length))
            else:
                continue
            trajectory = trajectories.setdefault(vehicle_id, [])
            if trajectory:
                position = max(position, trajectory[-1][1])
            trajectory.append((time, position, float(vehicle.get("speed"))))
        element.clear()
    return trajectories


def crossing_time(times, positions, speeds, bound):
    """Instante em que a trajetória passa pela posição bound (interpolação linear; extrapola nas pontas)."""
    index = bisect.bisect_left(positions, bound)
    if index == 0:
        return times[0] - (positions[0] - bound) / speeds[0] if speeds[0] > 0 else times[0]
    if index == len(positions):
        return times[-1] + (bound - positions[-1]) / speeds[-1] if speeds[-1] > 0 else None
    x0, x1 = positions[index - 1], positions[index]
    t0, t1 = times[index - 1], times[index]
    return t0 + (t1 - t0) * (bound - x0) / (x1 - x0) if x1 > x0 else t1


def read_trips(tripinfo_path, flow_id, min_depart):
    """Viagens completas da rota com partida pretendida a partir de min_depart: id -> (departDelay, chegada)."""
    trips = {}
    for trip in ET.parse(tripinfo_path).getroot().findall("tripinfo"):
        if trip.get("id", "").rsplit(".", 1)[0] != flow_id:
            continue
        delay = float(trip.get("departDelay", 0))
        if float(trip.get("depart", 0)) - delay >= min_depart:
            trips[trip.get("id")] = (delay, float(trip.get("arrival")))
    return trips


def segment_times(trips, trajectories, geometry, bounds):
    """Tempo de cada veículo em cada segmento (s) e no trecho inteiro, com o departDelay no primeiro."""
    per_segment = [[] for _ in range(len(bounds) - 1)]
    stretch = []
    for vehicle_id, (delay, arrival) in trips.items():
        trajectory = trajectories.get(vehicle_id)
        if not trajectory:
            continue
        # A chegada é no fim da última aresta (arrivalPos padrão), que o FCD pode não ter amostrado.
        if arrival > trajectory[-1][0]:
            trajectory = trajectory + [(arrival, geometry.length, trajectory[-1][2])]
        times, positions, speeds = zip(*trajectory)
        crossings = [crossing_time(times, positions, speeds, bound) for bound in bounds]
        if None in crossings:
            continue
        durations = [later - earlier for earlier, later in zip(crossings, crossings[1:])]
        durations[0] += delay
        for index, duration in enumerate(durations):
            per_segment[index].append(duration)
        stretch.append(sum(durations))
    return per_segment, stretch


def dispersion_value(durations, metric):
    if len(durations) < 2:
        return None
    if metric == "travel_time_cv":
        return statistics.pstdev(durations) / statistics.fmean(durations)
    return statistics.quantiles(durations, n=20)[-1] / statistics.median(durations)


def simulate_route(task, params):
    """Uma semente de uma rota e regime: velocidade (km/h) por segmento e no trecho, e a dispersão."""
    route_name, route, profile, flow, geometry, bounds, seed = task
    flow_id = f"flow_{safe_id(route_name)}"
    settings = params["simulation"]
    extra_args = ["--fcd-output", "fcd.xml", "--device.fcd.period", str(params["segments"]["fcd_period_s"]),
                  "--fcd-output.attributes", "lane,pos,speed"]

    def read_outputs(tmp_path):
        trips = read_trips(tmp_path / "tripinfo.xml", flow_id, settings["warmup_s"])
        trajectories = read_trajectories(tmp_path / "fcd.xml", flow_id, geometry)
        return segment_times(trips, trajectories, geometry, bounds)

    per_segment, stretch = run_sumo(route["topology"], {route_name: route}, profile, {route_name: flow},
                                    settings["seconds"], seed, read_outputs, extra_args)
    lengths = [later - earlier for earlier, later in zip(bounds, bounds[1:])]
    return {
        "segment_kmh": [length * len(times) / sum(times) * 3.6 if times and sum(times) > 0 else None
                        for length, times in zip(lengths, per_segment)],
        "stretch_kmh": (bounds[-1] - bounds[0]) * len(stretch) / sum(stretch) * 3.6 if stretch else None,
        "dispersion": dispersion_value(stretch, params["dispersion"]["metric"]),
        "trips": len(stretch),
    }


# ---------------------------------------------------------------- modelo

def load_model(choice, params):
    """Perfil Krauss por topologia e valor calibrado (fluxo e speedFactor) por (rota, regime)."""
    if not FLOWS_PATH.is_file():
        raise FileNotFoundError(f"{FLOWS_PATH} não existe; rode python3 calibration/calibrate_replicated.py antes")
    replicated = json.loads(FLOWS_PATH.read_text(encoding="utf-8"))
    if choice == "auto":
        choice = "joint" if JOINT_PATH.is_file() else "t2"
    values = {
        (route_name, regime_name): {"flow_veh_h": result["flow_veh_h"], "speed_factor": result.get("speed_factor")}
        for regime_name, regime in replicated["regimes"].items()
        for route_name, result in regime["routes"].items()
    }
    profiles, sources = {}, {}
    if choice == "joint":
        if not JOINT_PATH.is_file():
            raise FileNotFoundError(f"{JOINT_PATH} não existe; rode python3 calibration/calibrate_joint.py antes")
        for topology, result in json.loads(JOINT_PATH.read_text(encoding="utf-8"))["topologies"].items():
            profiles[topology] = result["best_profile"]
            sources[topology] = f"{JOINT_PATH.name} (trial {result['best_trial']})"
            for case in result["best_cases"]:
                value = values[(case["route"], case["regime"])]
                if "speed_factor" in case:
                    value["speed_factor"] = case["speed_factor"]
                else:
                    value["flow_veh_h"] = case["flow_veh_h"]
    return choice, profiles, sources, values


# ---------------------------------------------------------------- relatório

def route_report(segments, targets, bounds, distances, runs, tomtom_disp, metric, max_projection):
    """Linhas por segmento e o resumo da rota num regime."""
    rows, errors, weights = [], [], []
    for index, (segment, target) in enumerate(zip(segments, targets)):
        seed_values = [run["segment_kmh"][index] for run in runs if run["segment_kmh"][index] is not None]
        simulated = statistics.fmean(seed_values) if seed_values else None
        projection = max(distances[index], distances[index + 1])
        if projection > max_projection:
            status = "fora_do_caminho_sumo"
        elif bounds[index + 1] <= bounds[index]:
            status = "comprimento_sumo_nulo"
        elif simulated is None:
            status = "sem_viagem_simulada"
        else:
            status = target["status"]
        error = simulated - target["tomtom_kmh"] if status == "ok" else None
        if error is not None:
            errors.append(error)
            weights.append(segment["distance_m"])
        rows.append({
            "index": index,
            "segment_id": segment["segment_id"],
            "street": segment["street"],
            "tomtom_distance_m": segment["distance_m"],
            "sumo_start_m": round(bounds[index], 1),
            "sumo_length_m": round(bounds[index + 1] - bounds[index], 1),
            "projection_distance_m": round(projection, 1),
            **target,
            "simulated_kmh": simulated,
            "simulated_seed_stdev_kmh": statistics.stdev(seed_values) if len(seed_values) > 1 else None,
            "error_kmh": error,
            "status": status,
        })

    worst = max((row for row in rows if row["error_kmh"] is not None), key=lambda row: abs(row["error_kmh"]),
                default=None)
    disp_values = [run["dispersion"] for run in runs if run["dispersion"] is not None]
    simulated_disp = statistics.fmean(disp_values) if disp_values else None
    stretch = [run["stretch_kmh"] for run in runs if run["stretch_kmh"] is not None]
    return {
        "segments_ok": len(errors),
        "segments_total": len(rows),
        "excluded_segments": {row["index"]: row["status"] for row in rows if row["status"] != "ok"},
        "mae_kmh": statistics.fmean(abs(e) for e in errors) if errors else None,
        "length_weighted_mae_kmh": sum(abs(e) * w for e, w in zip(errors, weights)) / sum(weights) if errors else None,
        "bias_kmh": statistics.fmean(errors) if errors else None,
        "worst_segment": {"index": worst["index"], "street": worst["street"], "error_kmh": worst["error_kmh"]}
        if worst else None,
        "simulated_stretch_kmh": statistics.fmean(stretch) if stretch else None,
        "simulated_trips_per_seed": [run["trips"] for run in runs],
        "dispersion": {
            "metric": metric,
            "tomtom": tomtom_disp[0],
            "tomtom_cells": tomtom_disp[1],
            "simulated": simulated_disp,
            "simulated_by_seed": [run["dispersion"] for run in runs],
            "error": simulated_disp - tomtom_disp[0] if simulated_disp is not None and tomtom_disp[0] is not None
            else None,
        },
        "segments": rows,
    }


def format_number(value, pattern):
    return pattern.format(value) if value is not None else "-"


def print_summary(output):
    metric = output["metadata"]["dispersion_metric"]
    for regime_name, regime in output["regimes"].items():
        for route_name, route in regime["routes"].items():
            disp = route["dispersion"]
            worst = route["worst_segment"]
            worst_text = f"pior #{worst['index']} {worst['error_kmh']:+.1f} km/h" if worst else "sem segmento válido"
            print(
                f"{route_name:40s} {regime_name:9s} segmentos {route['segments_ok']:2d}/{route['segments_total']:2d}  "
                f"MAE {format_number(route['mae_kmh'], '{:5.1f}')} km/h "
                f"(pond. {format_number(route['length_weighted_mae_kmh'], '{:5.1f}')}, "
                f"viés {format_number(route['bias_kmh'], '{:+5.1f}')})  {worst_text}  "
                f"{metric} {format_number(disp['simulated'], '{:.3f}')} × {format_number(disp['tomtom'], '{:.3f}')} "
                f"(erro {format_number(disp['error'], '{:+.3f}')})"
            )
        for route_name, reason in regime["skipped_routes"].items():
            print(f"[PULADA] {route_name} ({regime_name}): {reason}")


def main():
    params = load_calibration_params()
    regime_names = list(params["target"]["regimes"])
    parser = argparse.ArgumentParser(description="T5: perfil por segmento e dispersão, simulado × TomTom")
    parser.add_argument("--model", choices=("auto", "t2", "joint"), default="auto",
                        help="Modelo simulado: joint = melhor perfil de krauss_joint.json; t2 = krauss.default "
                             "com route_flows_replicated.json; auto = joint se existir")
    parser.add_argument("--regime", choices=regime_names, action="append", help="Regime (repetível); padrão: todos")
    parser.add_argument("--route", action="append", help="Rota (repetível); padrão: todas com alvo")
    args = parser.parse_args()

    for path, step in ((MULTIDAY_PATH, "src/fetch_data.py --multi-day"), (TARGETS_PATH, "calibration/targets.py")):
        if not path.is_file():
            raise FileNotFoundError(f"{path} não existe; rode python3 {step} antes")
    multiday = json.loads(MULTIDAY_PATH.read_text(encoding="utf-8"))
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    routes = load_calibration_data()["routes"]
    model, profiles, sources, values = load_model(args.model, params)
    aggregate = AGGREGATORS[params["target"]["aggregation"]]
    days = targets["days_used"]
    metric = params["dispersion"]["metric"]

    cases, skipped = [], {}
    for regime_name in args.regime or regime_names:
        for route_name in sorted(targets["routes"]):
            if args.route and route_name not in args.route:
                continue
            regime_target = targets["routes"][route_name]["regimes"].get(regime_name)
            if not routes.get(route_name, {}).get("sumo_edges"):
                skipped.setdefault(regime_name, {})[route_name] = "sem caminho SUMO"
            elif regime_target is None or regime_target["status"] != "ok":
                skipped.setdefault(regime_name, {})[route_name] = "sem_alvo na T1"
            elif (route_name, regime_name) not in values:
                skipped.setdefault(regime_name, {})[route_name] = f"sem resultado em {FLOWS_PATH.name}"
            else:
                cases.append((route_name, regime_name))
    if not cases:
        raise ValueError("Nenhuma rota com alvo e calibração para comparar")

    raw = load_raw_routes(multiday, {name for name, _ in cases})
    nets, geometries = {}, {}
    for route_name in {name for name, _ in cases}:
        topology = routes[route_name]["topology"]
        if topology not in nets:
            nets[topology], _ = load_network(topology, with_internal=True)
        geometry = RouteGeometry(nets[topology], routes[route_name]["sumo_edges"])
        bounds, distances = segment_bounds(nets[topology], geometry, raw[route_name]["segments"])
        geometries[route_name] = (geometry, bounds, distances)

    tasks = []
    for route_name, regime_name in cases:
        route = routes[route_name]
        value = values[(route_name, regime_name)]
        profile = profiles.get(route["topology"], default_profile(params))
        if value["speed_factor"] is not None:
            route = {**route, "speed_factor": value["speed_factor"]}
        geometry, bounds, _ = geometries[route_name]
        for seed in params["segments"]["seeds"]:
            tasks.append((route_name, route, profile, value["flow_veh_h"], geometry, bounds, seed))
    print(f"Modelo: {model}; {len(cases)} rotas×regimes, {len(tasks)} simulações com FCD")
    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        runs = list(pool.map(lambda task: simulate_route(task, params), tasks))
    seeds = len(params["segments"]["seeds"])

    output = {
        "metadata": {
            "model": model,
            "krauss_source": {topology: sources.get(topology, "krauss.default + joint.speed_dev.default")
                              for topology in sorted({routes[name]["topology"] for name, _ in cases})},
            "flows_source": FLOWS_PATH.name if model == "t2" else f"{JOINT_PATH.name} (best_cases)",
            "targets": TARGETS_PATH.name,
            "days_used": days,
            "aggregation": params["target"]["aggregation"],
            "segments": params["segments"],
            "dispersion_metric": metric,
            "warmup_s": params["simulation"]["warmup_s"],
            "simulation_seconds": params["simulation"]["seconds"],
            "depart_delay_in_first_segment": True,
        },
        "regimes": {},
    }
    for case_index, (route_name, regime_name) in enumerate(cases):
        route = routes[route_name]
        hours = params["target"]["regimes"][regime_name]["hours"]
        segments = raw[route_name]["segments"]
        segment_targets = [segment_target(segment, days, hours, params, aggregate) for segment in segments]
        disp = tomtom_dispersion(multiday["routes"][route_name]["days"], raw[route_name]["summaries"], days, hours,
                                 params, aggregate)
        _, bounds, distances = geometries[route_name]
        report = route_report(segments, segment_targets, bounds, distances,
                              runs[case_index * seeds:(case_index + 1) * seeds], disp, metric,
                              params["segments"]["max_projection_distance_m"])
        value = values[(route_name, regime_name)]
        regime = output["regimes"].setdefault(regime_name, {"routes": {}, "skipped_routes": skipped.get(regime_name, {})})
        regime["routes"][route_name] = {
            "topology": route["topology"],
            "flow_veh_h": value["flow_veh_h"],
            "speed_factor": value["speed_factor"],
            "krauss": profiles.get(route["topology"], default_profile(params)),
            "target_kmh": targets["routes"][route_name]["regimes"][regime_name]["target_kmh"],
            **report,
        }
    for regime_name, names in skipped.items():
        output["regimes"].setdefault(regime_name, {"routes": {}, "skipped_routes": names})

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Métricas de segmento salvas em {OUTPUT_PATH}")
    print_summary(output)


if __name__ == "__main__":
    main()
