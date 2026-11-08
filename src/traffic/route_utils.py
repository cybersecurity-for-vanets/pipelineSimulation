import math
import os
import shutil
import statistics
import sys
from pathlib import Path


from src.env import OSM_DIR, ROOT_DIR

TOPOLOGY_FILE_NAMES = {"urban_road": "urbanRoad"}
# A connecting path may be at most DETOUR_RATIO * straight-line distance + DETOUR_SLACK_M long.
DETOUR_RATIO = 1.5
DETOUR_SLACK_M = 150
# Route end edges shorter than this (junction stubs) are dropped: a 5 m car cannot be inserted on them.
MIN_END_EDGE_LENGTH_M = 20.0
# Candidate edges whose bearing differs from the route's travel direction by more than this are dropped.
MAX_HEADING_DIFF_DEG = 60
# Straightening: a straight-through replacement may be at most STRAIGHTEN_RATIO * the replaced part + STRAIGHTEN_SLACK_M,
# and must rejoin the route within STRAIGHTEN_LOOKAHEAD edges.
STRAIGHTEN_RATIO = 1.3
STRAIGHTEN_SLACK_M = 30.0
STRAIGHTEN_LOOKAHEAD = 12
# On-ramp vehicles start this far upstream of the merge (following single predecessors): SUMO cannot insert a ramp's
# share of the CET volume on a 35 m ramp edge.
MIN_RAMP_APPROACH_M = 100.0


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
    """Drops points that repeat the previous one: they have no travel direction."""
    kept = []
    for point, xy in zip(points, xy_points):
        if kept and math.dist(kept[-1][1], xy) < 1.0:
            continue
        kept.append((point, xy))
    return [point for point, _ in kept], [xy for _, xy in kept]


def map_route_points_to_edges(net, points, route_name, search_radius_m=100):
    """SUMO edge ids of the passenger path through points, given as (longitude, latitude) in travel order."""
    xy_points = [net.convertLonLat2XY(longitude, latitude) for longitude, latitude in points]
    points, xy_points = _distinct_consecutive(points, xy_points)
    route_edges = []
    previous_edge = None
    previous_xy = None

    for index, ((longitude, latitude), xy) in enumerate(zip(points, xy_points)):
        _, _, candidates = _edge_candidates(net, longitude, latitude, search_radius_m)
        if not candidates:
            raise ValueError(
                f"No passenger SUMO edge within {search_radius_m}m of route '{route_name}' "
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
                    f"WARNING: route '{route_name}' has no connection within the detour limit "
                    f"near ({latitude:.6f}, {longitude:.6f}); using the shortest connection."
                )
        if path is None:
            raise ValueError(
                f"Route '{route_name}' cannot be connected through SUMO passenger edges "
                f"near ({latitude:.6f}, {longitude:.6f})"
            )
        for path_edge in path:
            if not route_edges or route_edges[-1] != path_edge.getID():
                route_edges.append(path_edge.getID())
        previous_edge = edge
        previous_xy = xy

    mapped_points = len(points)
    if len(route_edges) < 2 or mapped_points < 2:
        raise ValueError(f"Route '{route_name}' did not map to a traversable SUMO path")
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
    """Driven length of the SUMO path between the route's first and last points.

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


def trim_route_ends(net, edge_ids, min_edge_length_m=MIN_END_EDGE_LENGTH_M):
    """Drops leading/trailing junction stubs and ramps from a mapped route.

    A section endpoint at a junction often maps to a stub of a few metres created by --junctions.join, or to a
    ramp (highway.*_link) next to the main carriageway. Vehicles cannot be inserted on a stub shorter than a car,
    so the insertion queue grows without bound. A leading single-lane edge on a route that has three or more lanes
    elsewhere is also dropped: it is the cross street where the section starts (Av. Cidade Jardim at the start of Av.
    Nove de Julho), and inserting the whole CET volume of the avenue on one lane queues it before the section.
    Keeps at least two edges.
    """
    def droppable(edge_id, has_main):
        edge = net.getEdge(edge_id)
        is_link = edge.getType().endswith("_link")
        return edge.getLength() < min_edge_length_m or (is_link and has_main)

    def passenger_lanes(edge_id):
        return sum(lane.allows("passenger") for lane in net.getEdge(edge_id).getLanes())

    has_main = any(not net.getEdge(edge_id).getType().endswith("_link") for edge_id in edge_ids)
    wide = max(passenger_lanes(edge_id) for edge_id in edge_ids) >= 3
    edges = list(edge_ids)
    while len(edges) > 2 and (droppable(edges[0], has_main) or (wide and passenger_lanes(edges[0]) == 1)):
        edges.pop(0)
    while len(edges) > 2 and droppable(edges[-1], has_main):
        edges.pop()
    return edges


def _is_turn(edge, following):
    """True when no connection from edge to following goes straight ('s')."""
    return "s" not in {connection.getDirection() for connection in edge.getOutgoing().get(following, [])}


def count_turns(edges):
    return sum(_is_turn(edge, following) for edge, following in zip(edges, edges[1:]))


def straighten_route(net, edge_ids):
    """Replaces turns that only switch between parallel carriageways with the straight-through path.

    The point-to-point shortest path can leave a divided avenue through a short cross-street connector and come back
    on a parallel carriageway (Av. Nove de Julho at Rua Turquia/Rua Alemanha). The CET test car follows the avenue;
    the artificial turns make the simulated flow yield inside the junction and jam. At each turn, the straight
    successor is kept when its shortest path rejoins the route within STRAIGHTEN_LOOKAHEAD edges, is not much longer
    than the part it replaces and has fewer turns.
    """
    path = [net.getEdge(edge_id) for edge_id in edge_ids]
    changed = True
    while changed:
        changed = False
        for index, (edge, following) in enumerate(zip(path, path[1:])):
            if not _is_turn(edge, following):
                continue
            straight = [
                successor for successor, connections in edge.getOutgoing().items()
                if successor.allows("passenger") and any(c.getDirection() == "s" for c in connections)
            ]
            for successor in straight:
                for rejoin in range(index + 1, min(index + 1 + STRAIGHTEN_LOOKAHEAD, len(path))):
                    replacement, _ = net.getShortestPath(successor, path[rejoin], vClass="passenger")
                    if replacement is None:
                        continue
                    replaced = path[index + 1:rejoin + 1]
                    replacement = list(replacement)
                    if (sum(e.getLength() for e in replacement)
                            > STRAIGHTEN_RATIO * sum(e.getLength() for e in replaced) + STRAIGHTEN_SLACK_M):
                        continue
                    if count_turns([edge] + replacement) >= count_turns([edge] + replaced):
                        continue
                    path = path[:index + 1] + replacement + path[rejoin + 1:]
                    changed = True
                    break
                if changed:
                    break
            if changed:
                break
    return [edge.getID() for edge in path]


def _passenger_lanes(edge):
    return [lane for lane in edge.getLanes() if lane.allows("passenger")]


def _lane_weight(own, other):
    """Share of a lane that belongs to a ramp: 1 when only the ramp uses it, 0.5 when shared with the route."""
    if other and not own:
        return 1.0
    if other and own:
        return 0.5
    return 0.0


def route_ramps(net, edge_ids, count_point, max_ramp_veh_h_per_lane):
    """On/off ramps along a route and the route edge of the CET count point (conf/routes.json `count_point`).

    The CET volume is counted at one point; upstream and downstream of it vehicles enter and leave through ramps.
    Each ramp gets the share of lanes it uses at the merge or diverge (a lane shared with the route counts half),
    assuming equal flow per lane, and each on-ramp at most max_ramp_veh_h_per_lane per ramp lane (`max_veh_h`).
    Only junctions without traffic lights are considered (ramps, not intersections). On-ramps that join the route
    only through minor (yield) links are left out: at CET flows SUMO finds no gap and none of their vehicles enter.
    Returns {"count_edge": index, "lanes": [passenger lanes per route edge], "off": [{"after", "edge", "share"}],
    "on": [{"into", "edge", "share"}]}.
    """
    route_set = set(edge_ids)
    edges = [net.getEdge(edge_id) for edge_id in edge_ids]
    x, y = net.convertLonLat2XY(count_point[1], count_point[0])
    count_edge = min(range(len(edges)), key=lambda i: edges[i].getClosestLanePosDist((x, y))[2])
    off, on = [], []
    for index, (edge, following) in enumerate(zip(edges, edges[1:])):
        if edge.getToNode().getType() == "traffic_light":
            continue
        weights, counted = {}, 0
        for lane in edge.getLanes():
            if not lane.allows("passenger"):
                continue
            targets = {c.getTo() for c in lane.getOutgoing()
                       if c.getDirection() != "t" and c.getTo().allows("passenger")}
            exits = [t for t in targets if t.getID() not in route_set]
            if not targets:
                continue
            counted += 1
            weight = _lane_weight(following in targets, bool(exits))
            for target in exits:
                weights[target.getID()] = weights.get(target.getID(), 0.0) + weight / len(exits)
        off += [{"after": index, "edge": exit_id, "share": round(weight / counted, 4)}
                for exit_id, weight in sorted(weights.items()) if weight > 0]

        weights, counted = {}, 0
        for lane in following.getLanes():
            if not lane.allows("passenger"):
                continue
            # Entradas que só têm ligação secundária (dê a preferência, estado "m") para a faixa da rota ficam de
            # fora: no SUMO o veículo da alça espera uma brecha que não aparece com o fluxo CET (> 1.000 veic/h na
            # faixa) e nenhum entra. Na via real a entrada é em zíper; o volume dela fica com a origem da rota.
            sources = {c.getFrom() for c in lane.getIncomingConnections()
                       if c.getDirection() != "t" and c.getFrom().allows("passenger")
                       and (c.getFrom() == edge or c.getState() != "m")}
            ramps = [s for s in sources if s.getID() not in route_set]
            if not sources:
                continue
            counted += 1
            weight = _lane_weight(edge in sources, bool(ramps))
            for ramp in ramps:
                weights[ramp.getID()] = weights.get(ramp.getID(), 0.0) + weight / len(ramps)
        on += [{"into": index + 1, "edge": ramp_id, "approach": _ramp_approach(net, ramp_id, route_set),
                "share": round(weight / counted, 4),
                "max_veh_h": max_ramp_veh_h_per_lane * len(_passenger_lanes(net.getEdge(ramp_id)))}
               for ramp_id, weight in sorted(weights.items()) if weight > 0]
    return {"count_edge": count_edge, "lanes": [len(_passenger_lanes(edge)) for edge in edges], "off": off, "on": on}


def _ramp_approach(net, ramp_id, route_set):
    """Edges from an insertion point at least MIN_RAMP_APPROACH_M upstream of the merge to the ramp (inclusive)."""
    path = [net.getEdge(ramp_id)]
    while sum(edge.getLength() for edge in path) < MIN_RAMP_APPROACH_M:
        predecessors = [edge for edge in path[0].getIncoming()
                        if edge.allows("passenger") and edge.getID() not in route_set and edge not in path]
        if len(predecessors) != 1:
            break
        path.insert(0, predecessors[0])
    return [edge.getID() for edge in path]


def build_topology_edge_routes(routes, max_length_ratio_error):
    """{topology: {route: {edges, lanes_per_direction, speed_limit_kmh, sumo_length_m}}}.

    routes: {route: {"topology", "points": [[lat, lon], ...] in travel order, "cet_distance_m"}}. A SUMO length
    farther than max_length_ratio_error (relative) from the CET section length is reported as a warning.
    """
    by_topology = {}
    for route_name, route_data in routes.items():
        by_topology.setdefault(route_data["topology"], []).append((route_name, route_data))

    edge_routes = {}
    for topology, topology_routes in by_topology.items():
        net, _ = load_network(topology)
        driven_net, _ = load_network(topology, with_internal=True)
        edge_routes[topology] = {}
        for route_name, data in topology_routes:
            points = [(longitude, latitude) for latitude, longitude in data["points"]]
            mapped_ids = map_route_points_to_edges(net, points, route_name)
            edge_ids = trim_route_ends(net, mapped_ids)
            if edge_ids != mapped_ids:
                dropped = [edge for edge in mapped_ids if edge not in edge_ids]
                print(f"[ROTA] {route_name}: {len(dropped)} aresta(s) de ponta removida(s) (tocos de junção ou alças): {dropped}")
            turns = count_turns([net.getEdge(edge_id) for edge_id in edge_ids])
            straightened = straighten_route(net, edge_ids)
            remaining = count_turns([net.getEdge(edge_id) for edge_id in straightened])
            if straightened != edge_ids:
                print(f"[ROTA] {route_name}: conversões entre pistas paralelas trocadas pelo movimento direto "
                      f"({turns} -> {remaining} conversões)")
                edge_ids = straightened
            edges = [net.getEdge(edge_id) for edge_id in edge_ids]
            lane_count = max(1, round(statistics.median(edge.getLaneNumber() for edge in edges)))
            speed_limits = [edge.getSpeed() * 3.6 for edge in edges if edge.getSpeed() > 0]
            if not speed_limits:
                raise ValueError(f"SUMO route '{route_name}' has no usable speed limits")
            route_xy = [net.convertLonLat2XY(longitude, latitude) for longitude, latitude in points]
            sumo_length = trimmed_route_length(driven_net, edge_ids, route_xy)
            cet_length = data.get("cet_distance_m")
            if cet_length and abs(sumo_length / cet_length - 1) > max_length_ratio_error:
                print(
                    f"[AVISO] Rota SUMO '{route_name}' tem {sumo_length:.0f} m para um trecho CET de "
                    f"{cet_length:.0f} m (razão {sumo_length / cet_length:.2f}); confira os pontos em conf/routes.json."
                )
            edge_routes[topology][route_name] = {
                "edges": edge_ids,
                "lanes_per_direction": lane_count,
                "speed_limit_kmh": statistics.median(speed_limits),
                "sumo_length_m": round(sumo_length, 1),
            }
    return edge_routes
