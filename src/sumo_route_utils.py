import os
import shutil
import statistics
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
OSM_DIR = PROJECT_DIR / "osmFiles"
TOPOLOGY_FILE_NAMES = {"urban_road": "urbanRoad"}


def import_sumolib():
    sumo_home = os.environ.get("SUMO_HOME")
    candidate_tools = []
    if sumo_home:
        candidate_tools.append(Path(sumo_home) / "tools")

    netconvert = os.environ.get("NETCONVERT_BINARY") or shutil.which("netconvert")
    if netconvert:
        candidate_tools.append(Path(netconvert).resolve().parent.parent / "tools")

    sumo = os.environ.get("SUMO_BINARY") or shutil.which("sumo")
    if sumo:
        candidate_tools.append(Path(sumo).resolve().parent.parent / "tools")

    for tools_path in candidate_tools:
        if tools_path.is_dir() and str(tools_path) not in sys.path:
            sys.path.insert(0, str(tools_path))

    try:
        import sumolib
    except ImportError as error:
        raise RuntimeError("Cannot import sumolib; install SUMO and set SUMO_HOME") from error
    return sumolib


def network_path(topology):
    return OSM_DIR / f"{TOPOLOGY_FILE_NAMES.get(topology, topology)}.net.xml"


def load_network(topology):
    path = network_path(topology)
    if not path.is_file():
        raise FileNotFoundError(f"SUMO network does not exist: {path}")
    sumolib = import_sumolib()
    return sumolib.net.readNet(str(path), withInternal=False), path


def _shape_points(shape):
    for point in shape or []:
        if isinstance(point, dict):
            longitude = point.get("longitude", point.get("lon"))
            latitude = point.get("latitude", point.get("lat"))
            if longitude is not None and latitude is not None:
                yield float(longitude), float(latitude)
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            yield float(point[0]), float(point[1])


def _edge_candidates(net, longitude, latitude, search_radius_m):
    x, y = net.convertLonLat2XY(longitude, latitude)
    nearby = net.getNeighboringEdges(
        x,
        y,
        r=search_radius_m,
        includeJunctions=False,
    )
    candidates = []
    for edge, distance in nearby:
        if edge.getID().startswith(":") or not edge.allows("passenger"):
            continue
        candidates.append((edge, distance))
    return x, y, sorted(candidates, key=lambda candidate: candidate[1])


def _transition(net, previous_edge, candidates):
    if previous_edge is None:
        return (candidates[0][0], [candidates[0][0]]) if candidates else (None, None)

    best = None
    for edge, distance in candidates:
        path, cost = net.getShortestPath(previous_edge, edge, vClass="passenger")
        if path is None:
            continue
        score = distance + cost * 0.001
        if best is None or score < best[0]:
            best = (score, edge, path)
    if best is None:
        return None, None
    return best[1], best[2]


def map_route_segments_to_edges(net, segments, route_name, search_radius_m=100):
    route_edges = []
    previous_edge = None
    mapped_points = 0

    for segment in segments:
        points = list(_shape_points(segment.get("shape_lon_lat")))
        for longitude, latitude in points:
            _, _, candidates = _edge_candidates(net, longitude, latitude, search_radius_m)
            if not candidates:
                raise ValueError(
                    f"No passenger SUMO edge within {search_radius_m}m of TomTom route '{route_name}' "
                    f"at ({latitude:.6f}, {longitude:.6f})"
                )

            edge, path = _transition(net, previous_edge, candidates)
            if path is None:
                raise ValueError(
                    f"TomTom route '{route_name}' cannot be connected through SUMO passenger edges "
                    f"near ({latitude:.6f}, {longitude:.6f})"
                )
            for path_edge in path:
                if not route_edges or route_edges[-1] != path_edge.getID():
                    route_edges.append(path_edge.getID())
            previous_edge = edge
            mapped_points += 1

    if len(route_edges) < 2 or mapped_points < 2:
        raise ValueError(f"TomTom route '{route_name}' did not map to a traversable SUMO path")
    return route_edges


def build_topology_edge_routes(calibration_data):
    by_topology = {}
    for route_name, route_data in calibration_data.get("routes", {}).items():
        by_topology.setdefault(route_data["topology"], []).append((route_name, route_data))

    edge_routes = {}
    for topology, routes in by_topology.items():
        net, _ = load_network(topology)
        edge_routes[topology] = {}
        for route_name, data in routes:
            edge_ids = map_route_segments_to_edges(net, data.get("segments", []), route_name)
            edges = [net.getEdge(edge_id) for edge_id in edge_ids]
            lane_count = max(1, round(statistics.median(edge.getLaneNumber() for edge in edges)))
            speed_limits = [edge.getSpeed() * 3.6 for edge in edges if edge.getSpeed() > 0]
            if not speed_limits:
                raise ValueError(f"SUMO route '{route_name}' has no usable speed limits")
            edge_routes[topology][route_name] = {
                "edges": edge_ids,
                "lanes_per_direction": lane_count,
                "speed_limit_kmh": statistics.median(speed_limits),
            }
    return edge_routes