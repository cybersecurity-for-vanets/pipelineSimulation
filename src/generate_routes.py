"""Etapa 1.4: rotas SUMO de cada nível de demanda do experimento V2V e conf/flow_levels.json.

Os níveis são um por nível de serviço (LOS) do HCM alcançado na topologia: fator × fluxo médio de cada rota nos
níveis observados de cet.synthetic.reference_year, com os fatores escolhidos por map_los.py (conf/los_sweep.json,
"levels"). O fator 1,0 é o pico CET (média das horas observadas). Os volumes observados de cada ano e hora ficaram na
calibração e não viram cenário.

Entradas: conf/cet_scenarios.json (fetch_data.py), conf/route_flows_calibrated.json e
conf/krauss_calibration.json (calibrate_stage1.py), conf/los_sweep.json (map_los.py).
Saídas: trafficXmlFiles/<topologia>_<nível>.rou.xml e .sumocfg, e conf/flow_levels.json (contrato em flow_levels.py).
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CONF_DIR = BASE_DIR / "conf"
OSM_DIR = BASE_DIR / "osmFiles"
TRAFFIC_DIR = BASE_DIR / "trafficXmlFiles"
SCENARIOS_PATH = CONF_DIR / "cet_scenarios.json"
KRAUSS_CALIBRATION_PATH = CONF_DIR / "krauss_calibration.json"
ROUTE_FLOWS_PATH = CONF_DIR / "route_flows_calibrated.json"
LOS_SWEEP_PATH = CONF_DIR / "los_sweep.json"
TOPOLOGY_FILE_NAMES = {"urban_road": "urbanRoad"}
ROUTE_END_SECONDS = 3600
# SUMO step and Veins TraCI update interval: ETSI CAM triggers are checked every 100 ms.
SUMO_STEP_LENGTH = 0.1


def safe_id(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")


def file_topology(topology):
    return TOPOLOGY_FILE_NAMES.get(topology, topology)


def tls_offsets_path(topology):
    """Coordinated traffic light programs written by calibrate_stage1.py, if the topology uses them."""
    return OSM_DIR / f"{file_topology(topology)}.tls_offsets.add.xml"


def load_json(path, required=True):
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"Arquivo necessário não encontrado: {path}")
        return {}
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def _ramp_flows(edges, ramps, flow):
    """Fluxo na primeira aresta, em cada alça de entrada e fração de cada saída, com `flow` na aresta ramps.count_edge.

    Uma entrada leva `share` do fluxo da aresta em que entra, limitado a `max_veh_h` (faixas da alça ×
    network.max_ramp_veh_h_per_lane); o que passa do limite fica com a origem da via principal.

    As frações das saídas vêm da divisão das faixas, que não diz quanto sai de fato. Antes do ponto de contagem, cada
    saída aumenta o fluxo que tem de entrar pela origem; com as frações cheias, as arestas a montante ficam acima de
    2.000 veic/h/faixa e a fila de inserção, não o modelo de seguimento, define a velocidade. A CET mediu tráfego
    fluindo no ponto de contagem, então as saídas antes da contagem são reduzidas na mesma proporção (bisseção) até
    nenhuma aresta da rota receber mais veic/h por faixa que ele. Se nem sem essas saídas isso vale (uma aresta com
    menos faixas entre a última entrada e a contagem), elas ficam zeradas. Sem `lanes` em ramps, não há limite.
    Devolve (fluxo na origem, {entrada: veic/h}, {saída: fração}).
    """
    count = len(edges)
    lanes = ramps.get("lanes")
    count_edge = ramps["count_edge"]

    def solve(upstream_scale):
        off_shares = [ramp["share"] * (upstream_scale if ramp["after"] < count_edge else 1.0) for ramp in ramps["off"]]
        off_share = [0.0] * count
        for ramp, share in zip(ramps["off"], off_shares):
            off_share[ramp["after"]] += share
        fixed = {}

        def forward(start):
            flows, ramp_flows = [start], {}
            for index in range(1, count):
                arriving = flows[-1] * (1 - off_share[index - 1])
                entering = [k for k, ramp in enumerate(ramps["on"]) if ramp["into"] == index]
                proportional = sum(ramps["on"][k]["share"] for k in entering if k not in fixed)
                if proportional >= 1:
                    raise ValueError(f"Alças de entrada com fração >= 1 na aresta {edges[index]}")
                total = (arriving + sum(fixed[k] for k in entering if k in fixed)) / (1 - proportional)
                for k in entering:
                    ramp_flows[k] = fixed.get(k, total * ramps["on"][k]["share"])
                flows.append(total)
            return flows, ramp_flows

        for _ in range(len(ramps["on"]) + 1):
            base, _ = forward(0.0)
            unit, _ = forward(1.0)
            start = (flow - base[count_edge]) / (unit[count_edge] - base[count_edge])
            flows, ramp_flows = forward(start)
            over = {k: ramps["on"][k]["max_veh_h"] for k, value in ramp_flows.items()
                    if k not in fixed and value > ramps["on"][k].get("max_veh_h", float("inf"))}
            if not over:
                return max(start, 0.0), ramp_flows, dict(enumerate(off_shares)), flows
            fixed.update(over)
        raise RuntimeError("Distribuição das alças não convergiu")

    def overloaded(flows):
        limit = flow / lanes[count_edge] * (1 + 1e-6)
        return any(value / lane_count > limit for value, lane_count in zip(flows, lanes) if lane_count)

    result = solve(1.0)
    if not lanes or not overloaded(result[3]):
        return result[:3]
    low, high = 0.0, 1.0
    if overloaded(solve(0.0)[3]):
        return solve(0.0)[:3]
    for _ in range(30):
        middle = (low + high) / 2
        low, high = (middle, high) if not overloaded(solve(middle)[3]) else (low, middle)
    return solve(low)[:3]


def route_demands(route_data, flow):
    """[(sufixo do id, arestas, veic/h)] de uma rota: a viagem inteira e, com alças, as que entram ou saem por elas.

    Sem `ramps` (conf/cet_scenarios.json), flow é o fluxo da rota inteira. Com `ramps` (rotas com count_point em
    conf/routes.json), flow é o volume no ponto de contagem CET (ramps.count_edge). Cada alça leva a fração das faixas
    que usa (sumo_route_utils.route_ramps): uma saída depois da aresta i tira `share` do fluxo da aresta i; uma
    entrada na aresta i responde por `share` do fluxo da aresta i, até `max_veh_h`. Quem sai por uma alça é uma fração
    igual do fluxo presente, qualquer que seja a origem. O sufixo vazio é a viagem inteira, a única usada na
    velocidade da calibração.
    """
    edges = route_data["sumo_edges"]
    ramps = route_data.get("ramps")
    if not ramps or not (ramps["on"] or ramps["off"]):
        return [("", edges, flow)]
    start, ramp_flows, off_shares = _ramp_flows(edges, ramps, flow)
    off_share = [0.0] * len(edges)
    for k, ramp in enumerate(ramps["off"]):
        off_share[ramp["after"]] += off_shares[k]
    origins = [("", [], 0, start)] + [
        (f"__in_{safe_id(ramp['edge'])}", ramp.get("approach", [ramp["edge"]]), ramp["into"], ramp_flows[k])
        for k, ramp in enumerate(ramps["on"])
    ]
    demands = []
    for tag, prefix, first, origin_flow in origins:
        surviving = 1.0
        for index in range(first, len(edges) - 1):
            for k, ramp in enumerate(ramps["off"]):
                if ramp["after"] != index:
                    continue
                demands.append((f"{tag}__out_{safe_id(ramp['edge'])}", prefix + edges[first:index + 1] + [ramp["edge"]],
                                origin_flow * surviving * off_shares[k]))
            surviving *= 1 - off_share[index]
        demands.append((tag, prefix + edges[first:], origin_flow * surviving))
    return demands


def build_route_file(topology, routes, flow_rate, krauss, route_flows=None):
    """flow_rate is veh/h/lane; route_flows optionally overrides it per route with total veh/h.

    krauss holds the car-following parameters plus speedFactor and speedDev; a route's own speed_factor wins.
    Routes with ramps (route_demands) get one flow per entry/exit pair; the whole-route flow keeps the id flow_<route>.
    """
    route_flows = route_flows or {}
    root = ET.Element("routes")

    for route_name, route_data in sorted(routes.items()):
        edges = route_data.get("sumo_edges", [])
        if len(edges) < 2:
            raise ValueError(f"Route '{route_name}' has no connected SUMO edge path")

        suffix = safe_id(route_name)
        vehicle_type_id = f"car_{suffix}"
        ET.SubElement(root, "vType", {
            "id": vehicle_type_id,
            "vClass": "passenger",
            "carFollowModel": "Krauss",
            "length": "5.0",
            "minGap": str(krauss["minGap"]),
            "maxSpeed": f"{route_data['speed_limit_kmh'] / 3.6:.2f}",
            "accel": str(krauss["accel"]),
            "decel": str(krauss["decel"]),
            "tau": str(krauss["tau"]),
            "sigma": str(krauss["sigma"]),
            "speedFactor": str(route_data.get("speed_factor", krauss.get("speedFactor", 1))),
            "speedDev": str(krauss.get("speedDev", 0.1)),
        })
        flow = route_flows.get(route_name, flow_rate * route_data["lanes_per_direction"])
        for tag, demand_edges, veh_h in route_demands(route_data, flow):
            if tag and veh_h < 0.5:
                continue
            ET.SubElement(root, "route", {"id": f"route_{suffix}{tag}", "edges": " ".join(demand_edges)})
            ET.SubElement(root, "flow", {
                "id": f"flow_{suffix}{tag}",
                "type": vehicle_type_id,
                "route": f"route_{suffix}{tag}",
                "begin": "0",
                "end": str(ROUTE_END_SECONDS),
                "vehsPerHour": f"{veh_h:.1f}" if isinstance(veh_h, float) else str(veh_h),
                "departLane": "allowed",
                "departSpeed": "max",
            })

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def build_sumocfg(topology, route_path, net_path):
    root = ET.Element("configuration")
    inputs = ET.SubElement(root, "input")
    ET.SubElement(inputs, "net-file", {"value": str(net_path.resolve())})
    ET.SubElement(inputs, "route-files", {"value": str(route_path.resolve())})
    # veins_launchd copies only the .sumocfg, so the absolute path keeps the offsets reachable.
    if tls_offsets_path(topology).is_file():
        ET.SubElement(inputs, "additional-files", {"value": str(tls_offsets_path(topology).resolve())})
    time = ET.SubElement(root, "time")
    ET.SubElement(time, "begin", {"value": "0"})
    ET.SubElement(time, "end", {"value": str(ROUTE_END_SECONDS)})
    processing = ET.SubElement(root, "processing")
    ET.SubElement(processing, "step-length", {"value": str(SUMO_STEP_LENGTH)})
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def write_level(topology, level, topology_routes, krauss, route_flows, net_path, lanes):
    prefix = f"{file_topology(topology)}_{level}"
    route_path = TRAFFIC_DIR / f"{prefix}.rou.xml"
    sumocfg_path = TRAFFIC_DIR / f"{prefix}.sumocfg"
    route_path.write_text(build_route_file(topology, topology_routes, 0, krauss, route_flows), encoding="utf-8")
    sumocfg_path.write_text(build_sumocfg(topology, route_path, net_path), encoding="utf-8")
    total = sum(route_flows.values())
    print(f"[OK] {prefix}: {len(topology_routes)} rotas, {total} veic/h ({total / lanes:.0f} veic/h/faixa)")
    return {"total_veh_h": total, "veh_h_per_lane": round(total / lanes, 1), "routes": route_flows}


def generate_sumo_files():
    sys.path.insert(0, str(BASE_DIR))
    from calibration.params import load_calibration_params
    from flow_levels import FLOW_LEVELS_PATH, level_label, level_sort_key, level_year, synthetic_level_id

    params = load_calibration_params()
    reference_year = params["cet"]["synthetic"]["reference_year"]
    routes = load_json(SCENARIOS_PATH)["routes"]
    if not routes:
        raise ValueError(f"{SCENARIOS_PATH} não tem rotas; rode fetch_data.py")
    calibrated = load_json(ROUTE_FLOWS_PATH)["routes"]
    krauss_profiles = load_json(KRAUSS_CALIBRATION_PATH)
    if krauss_profiles.get("metadata", {}).get("smoke"):
        print("[AVISO] krauss_calibration.json veio de calibrate_stage1.py --smoke: não é uma calibração válida.")
    if not LOS_SWEEP_PATH.is_file():
        raise FileNotFoundError(f"{LOS_SWEEP_PATH} não existe; rode src/map_los.py")
    los_sweep = load_json(LOS_SWEEP_PATH)

    missing = set(routes) - set(calibrated)
    if missing:
        raise ValueError(f"Sem fluxo calibrado para {sorted(missing)}; rode calibrate_stage1.py")

    TRAFFIC_DIR.mkdir(parents=True, exist_ok=True)
    for old in TRAFFIC_DIR.glob("*.rou.xml"):
        old.unlink()
    for old in TRAFFIC_DIR.glob("*.sumocfg"):
        old.unlink()
    topologies = {}
    for topology in sorted({route["topology"] for route in routes.values()}):
        topology_routes = {name: route for name, route in routes.items() if route["topology"] == topology}
        profile = krauss_profiles.get(topology)
        if not profile or "levels" not in profile:
            raise ValueError(f"Sem perfil Krauss para '{topology}' em {KRAUSS_CALIBRATION_PATH}; rode calibrate_stage1.py")
        los_levels = los_sweep.get(topology, {}).get("levels")
        if not los_levels:
            raise ValueError(f"Sem níveis de LOS para '{topology}' em {LOS_SWEEP_PATH}; rode src/map_los.py")
        krauss = profile["params"]
        net_path = OSM_DIR / f"{file_topology(topology)}.net.xml"
        if not net_path.is_file():
            raise FileNotFoundError(f"Rede SUMO não encontrada: {net_path}")
        # Faixas típicas (mediana) de cada rota: o gargalo das pontas não representa a seção da contagem CET.
        lanes = sum(route["lanes_per_direction"] for route in topology_routes.values())
        levels = topologies.setdefault(file_topology(topology), {})

        reference = [level for level in profile["levels"] if level_year(level) == reference_year]
        if not reference:
            raise ValueError(f"'{topology}' não tem nível observado em {reference_year} (cet.synthetic.reference_year)")
        for los, factor in los_levels.items():
            level = synthetic_level_id(factor)
            route_flows = {
                name: max(1, round(factor * sum(calibrated[name]["flow_veh_h_by_level"][ref] for ref in reference)
                                   / len(reference)))
                for name in topology_routes
            }
            levels[level] = {"kind": "synthetic", "factor": factor, "reference_year": reference_year, "los": los,
                             **write_level(topology, level, topology_routes, krauss, route_flows, net_path, lanes)}

    all_levels = sorted({level for levels in topologies.values() for level in levels}, key=level_sort_key)
    flow_levels = {
        "source": "Um nível de demanda por LOS do HCM (conf/los_sweep.json): fator × fluxo médio CET "
                  f"{reference_year} (equivalente-carro, cet.pce); 1,00× = pico CET",
        "levels": all_levels,
        "labels": {level: level_label(level) for level in all_levels},
        "topologies": topologies,
    }
    FLOW_LEVELS_PATH.write_text(json.dumps(flow_levels, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Níveis de fluxo salvos em {FLOW_LEVELS_PATH} "
          f"({', '.join(f'{topology}: {len(levels)}' for topology, levels in topologies.items())})")


if __name__ == "__main__":
    generate_sumo_files()
