import math
import os
import shutil
import statistics
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
OSM_DIR = PROJECT_DIR / "osmFiles"
TOPOLOGY_FILE_NAMES = {"urban_road": "urbanRoad"}
# A connecting path may be at most DETOUR_RATIO * straight-line distance + DETOUR_SLACK_M long.
DETOUR_RATIO = 1.5
DETOUR_SLACK_M = 150
# Candidate edges whose bearing differs from the TomTom travel direction by more than this are dropped.
MAX_HEADING_DIFF_DEG = 60
# Mapped SUMO route length / TomTom segment length above this is reported as a warning.
MAX_LENGTH_RATIO = 1.15


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


def load_network(topology, with_internal=False):
    path = network_path(topology)
    if not path.is_file():
        raise FileNotFoundError(f"SUMO network does not exist: {path}")
    sumolib = import_sumolib()
    return sumolib.net.readNet(str(path), withInternal=with_internal), path


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


def _bearing(origin, target):
    """Compass bearing in degrees (0 = north) from origin to target, in network XY metres."""
    return math.degrees(math.atan2(target[0] - origin[0], target[1] - origin[1])) % 360


def _heading_diff(first, second):
    difference = abs(first - second) % 360
    return min(difference, 360 - difference)


def _edge_bearing(edge):
    shape = edge.getShape()
    return _bearing(shape[0], shape[-1])


def _travel_heading(xy_points, index):
    """Direction of travel at a point, or None when neighbouring points coincide."""
    if len(xy_points) < 2:
        return None
    origin, target = (xy_points[index - 1], xy_points[index]) if index > 0 else (xy_points[0], xy_points[1])
    if math.dist(origin, target) < 1.0:
        return None
    return _bearing(origin, target)


def _transition(net, previous_edge, previous_xy, xy, candidates, heading, enforce_detour_limit=True):
    if heading is not None:
        aligned = [
            candidate for candidate in candidates
            if _heading_diff(_edge_bearing(candidate[0]), heading) <= MAX_HEADING_DIFF_DEG
        ]
        candidates = aligned or candidates

    if previous_edge is None:
        return (candidates[0][0], [candidates[0][0]]) if candidates else (None, None)

    straight = math.dist(previous_xy, xy)
    best = None
    for edge, distance in candidates:
        path, _ = net.getShortestPath(previous_edge, edge, vClass="passenger")
        if path is None:
            continue
        # Only the edges strictly between the two are detour; the first and last are partly covered by the points.
        travelled = sum(path_edge.getLength() for path_edge in path[1:-1])
        if enforce_detour_limit and travelled > DETOUR_RATIO * straight + DETOUR_SLACK_M:
            continue
        score = distance + travelled - straight
        if best is None or score < best[0]:
            best = (score, edge, path)
    if best is None:
        return None, None
    return best[1], best[2]


def _distinct_consecutive(points, xy_points):
    """Drops points that repeat the previous one: TomTom repeats them at segment joins, and they have no travel direction."""
    kept = []
    for point, xy in zip(points, xy_points):
        if kept and math.dist(kept[-1][1], xy) < 1.0:
            continue
        kept.append((point, xy))
    return [point for point, _ in kept], [xy for _, xy in kept]


def map_route_segments_to_edges(net, segments, route_name, search_radius_m=100):
    points = [
        point
        for segment in segments
        for point in _shape_points(segment.get("shape_lon_lat"))
    ]
    xy_points = [net.convertLonLat2XY(longitude, latitude) for longitude, latitude in points]
    points, xy_points = _distinct_consecutive(points, xy_points)
    route_edges = []
    previous_edge = None
    previous_xy = None

    for index, ((longitude, latitude), xy) in enumerate(zip(points, xy_points)):
        _, _, candidates = _edge_candidates(net, longitude, latitude, search_radius_m)
        if not candidates:
            raise ValueError(
                f"No passenger SUMO edge within {search_radius_m}m of TomTom route '{route_name}' "
                f"at ({latitude:.6f}, {longitude:.6f})"
            )

        heading = _travel_heading(xy_points, index)
        edge, path = _transition(net, previous_edge, previous_xy, xy, candidates, heading)
        if path is None:
            # No connection within the detour limit: keep the shortest one and warn, so the route is still built.
            edge, path = _transition(
                net, previous_edge, previous_xy, xy, candidates, heading, enforce_detour_limit=False,
            )
            if path is not None:
                print(
                    f"WARNING: TomTom route '{route_name}' has no connection within the detour limit "
                    f"near ({latitude:.6f}, {longitude:.6f}); using the shortest connection."
                )
        if path is None:
            raise ValueError(
                f"TomTom route '{route_name}' cannot be connected through SUMO passenger edges "
                f"near ({latitude:.6f}, {longitude:.6f})"
            )
        for path_edge in path:
            if not route_edges or route_edges[-1] != path_edge.getID():
                route_edges.append(path_edge.getID())
        previous_edge = edge
        previous_xy = xy

    mapped_points = len(points)
    if len(route_edges) < 2 or mapped_points < 2:
        raise ValueError(f"TomTom route '{route_name}' did not map to a traversable SUMO path")
    return route_edges


def _offset_on_edge(edge, xy):
    """Distance along the edge's first lane from its start to the projection of xy."""
    position, _ = edge.getLanes()[0].getClosestLanePosAndDist(xy)
    return position


def _junction_connector_length(net, previous, following):
    """Length of the internal junction lanes a vehicle drives between two consecutive route edges."""
    path, _ = net.getShortestPath(previous, following, vClass="passenger", withInternal=True)
    if not path:
        return 0.0
    return sum(edge.getLength() for edge in path if edge.getID().startswith(":"))


def trimmed_route_length(net, edge_ids, route_xy):
    """Driven length of the SUMO path covered by the TomTom geometry.

    Junction connectors count, because vehicles drive through them; the first and last edges are cut at the end points.
    `net` must be loaded with internal lanes.
    """
    edges = [net.getEdge(edge_id) for edge_id in edge_ids]
    start_offset = _offset_on_edge(edges[0], route_xy[0])
    end_offset = _offset_on_edge(edges[-1], route_xy[-1])
    if len(edges) == 1:
        return max(end_offset - start_offset, 0.0)
    middle = sum(edge.getLength() for edge in edges[1:-1])
    connectors = sum(
        _junction_connector_length(net, previous, following)
        for previous, following in zip(edges, edges[1:])
    )
    return (edges[0].getLength() - start_offset) + middle + connectors + end_offset


def build_topology_edge_routes(calibration_data):
    by_topology = {}
    for route_name, route_data in calibration_data.get("routes", {}).items():
        by_topology.setdefault(route_data["topology"], []).append((route_name, route_data))

    edge_routes = {}
    for topology, routes in by_topology.items():
        net, _ = load_network(topology)
        driven_net = None
        edge_routes[topology] = {}
        for route_name, data in routes:
            edge_ids = map_route_segments_to_edges(net, data.get("segments", []), route_name)
            edges = [net.getEdge(edge_id) for edge_id in edge_ids]
            lane_count = max(1, round(statistics.median(edge.getLaneNumber() for edge in edges)))
            speed_limits = [edge.getSpeed() * 3.6 for edge in edges if edge.getSpeed() > 0]
            if not speed_limits:
                raise ValueError(f"SUMO route '{route_name}' has no usable speed limits")
            route_xy = [
                net.convertLonLat2XY(longitude, latitude)
                for segment in data.get("segments", [])
                for longitude, latitude in _shape_points(segment.get("shape_lon_lat"))
            ]
            if driven_net is None:
                driven_net, _ = load_network(topology, with_internal=True)
            sumo_length = trimmed_route_length(driven_net, edge_ids, route_xy)
            tomtom_length = sum(segment.get("distance_m") or 0 for segment in data.get("segments", []))
            if tomtom_length and sumo_length / tomtom_length > MAX_LENGTH_RATIO:
                print(
                    f"WARNING: SUMO route '{route_name}' is {sumo_length:.0f} m for a TomTom route of "
                    f"{tomtom_length:.0f} m (ratio {sumo_length / tomtom_length:.2f}); the map match may be detouring."
                )
            edge_routes[topology][route_name] = {
                "edges": edge_ids,
                "lanes_per_direction": lane_count,
                "speed_limit_kmh": statistics.median(speed_limits),
            }
    return edge_routes