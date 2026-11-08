"""Etapa 1.3: varredura de demanda no modelo calibrado e nível de serviço (LOS) do HCM por topologia.

Para cada fator de los.factors, a demanda de cada rota é fator × fluxo médio da rota nos níveis observados de
cet.synthetic.reference_year (a mesma base dos níveis sintéticos), simulada com o perfil Krauss calibrado em cada seed
de los.seeds. A medida do HCM depende do tipo de via:

- highway (basic freeway segment): densidade média nas rotas de demanda observada, em equivalente-carro por km por
  faixa (veículo-segundos nas arestas da rota ÷ duração ÷ faixa-km);
- urban_road (urban street segment): velocidade de percurso das viagens inteiras (com o departDelay, como na
  calibração) ÷ velocidade de fluxo livre (limite da via × speedFactor do perfil);
- intersection (signalized intersection): atraso de controle por semáforo: (timeLoss + departDelay) ÷ número de
  semáforos da rota, médio entre os veículos de todas as rotas.

O valor da topologia é a média entre as rotas (sentidos); o LOS vem dos limites los.<medida> (A/B, B/C, C/D, D/E, E/F).
Quando a fila de inserção passa de acceptance.* (demanda acima da capacidade da rede), o LOS é F, como v/c > 1 no HCM.
Só a janela depois de simulation.warmup_s entra nas medidas.

Saída: conf/los_sweep.json, com as linhas da varredura e, por topologia, um fator de demanda por LOS alcançado
("levels", ver representative_levels). generate_routes.py usa esses fatores como níveis do experimento V2V.
"""
import argparse
import json
import statistics
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.analysis.flow_levels import level_year
from src.env import CONFIG_DIR
from src.traffic.generate_routes import safe_id
from src.traffic.params import load_calibration_params
from src.traffic.route_utils import import_sumolib, network_path
from src.traffic.simulation import read_insertion_stats, run_sumo

OUTPUT_PATH = CONFIG_DIR / "los_sweep.json"
OBSERVED = "cet_volume"
LOS_LETTERS = "ABCDEF"
MEASURES = {
    "highway": ("highway_density_pc_km_ln", "densidade (cp/km/faixa)"),
    "urban_road": ("urban_road_speed_ratio", "velocidade / fluxo livre"),
    "intersection": ("intersection_delay_s", "atraso por semáforo (s/veíc)"),
}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def los_letter(value, limits, increasing):
    for letter, limit in zip(LOS_LETTERS, limits):
        if (value <= limit) if increasing else (value > limit):
            return letter
    return "F"


def route_geometry(topology, routes):
    """Por rota: faixa-km das arestas, arestas, número de semáforos e velocidade de fluxo livre do limite (km/h)."""
    sumolib = import_sumolib()
    net = sumolib.net.readNet(str(network_path(topology)))
    geometry = {}
    for name, route in routes.items():
        edges = [net.getEdge(edge_id) for edge_id in route["sumo_edges"]]
        lane_km = sum(edge.getLength() / 1000 * sum(lane.allows("passenger") for lane in edge.getLanes())
                      for edge in edges)
        signals = sum(1 for edge, following in zip(edges, edges[1:])
                      if any(c.getTLSID() for c in edge.getOutgoing().get(following, [])))
        geometry[name] = {"edges": set(route["sumo_edges"]), "lane_km": lane_km, "signals": signals,
                          "speed_limit_kmh": route["speed_limit_kmh"]}
    return geometry


def simulate_factor(topology, routes, krauss, flows, params, seed, geometry):
    simulation = params["simulation"]
    warmup, seconds = simulation["warmup_s"], simulation["seconds"]
    edge_data = (f'<additional><edgeData id="los" file="edges.xml" begin="{warmup}" end="{seconds}"/></additional>')
    flow_to_route = {f"flow_{safe_id(name)}": name for name in routes}

    def read(tmp):
        trips = {name: [] for name in routes}
        for trip in ET.parse(tmp / "tripinfo.xml").getroot().findall("tripinfo"):
            name = flow_to_route.get(trip.get("id", "").rsplit(".", 1)[0])
            delay = float(trip.get("departDelay", 0))
            if name is None or float(trip.get("depart", 0)) - delay < warmup:
                continue
            trips[name].append({"duration": float(trip.get("duration", 0)) + delay,
                                "length": float(trip.get("routeLength", 0)),
                                "loss": float(trip.get("timeLoss", 0)) + delay})
        seconds_on = {}
        for interval in ET.parse(tmp / "edges.xml").getroot().findall("interval"):
            for edge in interval.findall("edge"):
                seconds_on[edge.get("id")] = seconds_on.get(edge.get("id"), 0.0) + float(edge.get("sampledSeconds", 0))
        return trips, seconds_on, read_insertion_stats(tmp / "tripinfo.xml", tmp / "statistics.xml")

    trips, seconds_on, insertion = run_sumo(topology, routes, krauss, flows, seconds, seed, read,
                                            additional_xml=edge_data)
    window = seconds - warmup
    per_route = {}
    for name, rows in trips.items():
        shape = geometry[name]
        density = sum(seconds_on.get(edge, 0.0) for edge in shape["edges"]) / window / shape["lane_km"]
        result = {"density_pc_km_ln": density, "trips": len(rows)}
        if rows:
            speed = sum(row["length"] for row in rows) / sum(row["duration"] for row in rows) * 3.6
            free_flow = shape["speed_limit_kmh"] * krauss.get("speedFactor", 1.0)
            result.update(speed_kmh=speed, speed_ratio=speed / free_flow,
                          delay_per_signal_s=statistics.fmean(row["loss"] for row in rows) / max(shape["signals"], 1))
        per_route[name] = result
    return per_route, insertion


def topology_value(topology, per_route, observed):
    """Valor da medida HCM da topologia: média entre as rotas de demanda observada (intersection: todas, por veículo)."""
    if topology == "highway":
        return statistics.fmean(per_route[name]["density_pc_km_ln"] for name in observed)
    if topology == "urban_road":
        return statistics.fmean(per_route[name].get("speed_ratio", 0.0) for name in observed)
    trips = sum(route["trips"] for route in per_route.values())
    return sum(route.get("delay_per_signal_s", 0.0) * route["trips"] for route in per_route.values()) / max(trips, 1)


def sweep_topology(topology, scenarios, calibrated, krauss_profiles, params, pool, factors):
    routes = {name: route for name, route in scenarios.items() if route["topology"] == topology}
    krauss = krauss_profiles[topology]["params"]
    observed = sorted(name for name in routes if routes[name].get("demand", OBSERVED) == OBSERVED)
    reference_year = params["cet"]["synthetic"]["reference_year"]
    reference = [level for level in krauss_profiles[topology]["levels"] if level_year(level) == reference_year]
    base = {name: statistics.fmean(calibrated[name]["flow_veh_h_by_level"][level] for level in reference)
            for name in routes}
    lanes = sum(route["lanes_per_direction"] for route in routes.values())
    geometry = route_geometry(topology, routes)
    limits_key, label = MEASURES[topology]
    limits = params["los"][limits_key]
    increasing = topology != "urban_road"
    acceptance = params["acceptance"]

    jobs = {}
    for factor in factors:
        flows = {name: max(1, round(factor * flow)) for name, flow in base.items()}
        for seed in params["los"]["seeds"]:
            jobs[(factor, seed)] = pool.submit(simulate_factor, topology, routes, krauss, flows, params, seed, geometry)

    rows = []
    for factor in factors:
        flows = {name: max(1, round(factor * flow)) for name, flow in base.items()}
        results = [jobs[(factor, seed)].result() for seed in params["los"]["seeds"]]
        values = [topology_value(topology, per_route, observed) for per_route, _ in results]
        value = statistics.fmean(values)
        overloaded = any(stats["max_depart_delay_s"] > acceptance["max_depart_delay_s"]
                         or stats["waiting_at_end"] > acceptance["max_waiting_fraction"] * max(stats["loaded"], 1)
                         for _, stats in results)
        letter = "F" if overloaded else los_letter(value, limits, increasing)
        routes_summary = {}
        for name in routes:
            keys = [key for key in ("density_pc_km_ln", "speed_kmh", "speed_ratio", "delay_per_signal_s")
                    if all(key in per_route[name] for per_route, _ in results)]
            routes_summary[name] = {key: round(statistics.fmean(per_route[name][key] for per_route, _ in results), 3)
                                    for key in keys}
        row = {
            "factor": factor,
            "veh_h_per_lane": round(sum(flows.values()) / lanes, 1),
            "route_flows_veh_h": flows,
            "value": round(value, 3),
            "seed_values": [round(v, 3) for v in values],
            "los": letter,
            "demand_above_capacity": overloaded,
            "max_depart_delay_s": round(max(stats["max_depart_delay_s"] for _, stats in results), 1),
            "waiting_at_end": max(stats["waiting_at_end"] for _, stats in results),
            "routes": routes_summary,
        }
        rows.append(row)
        print(f"[LOS] {topology} {factor:>4.2f}× ({row['veh_h_per_lane']:7.1f} veic/h/faixa): {label} = {value:7.2f} "
              f"-> LOS {letter}{' (fila de inserção: demanda acima da capacidade)' if overloaded else ''}")
    return {
        "measure": limits_key,
        "measure_label": label,
        "limits": limits,
        "reference_year": reference_year,
        "base_flow_veh_h": {name: round(flow, 1) for name, flow in base.items()},
        "krauss": krauss,
        "rows": rows,
    }


def representative_levels(rows, los_params):
    """{LOS: fator} com um nível de demanda por LOS alcançado na varredura.

    O pico CET (fator 1,0, média das horas observadas de cet.synthetic.reference_year) representa o LOS em que cai.
    Os demais LOS ficam com o meio do intervalo de fatores classificados nele, arredondado a 0,05 (dentro do
    intervalo; se o arredondamento sair dele, o fator varrido mais próximo do meio). O LOS F, aberto para cima, fica
    com los.saturated_factor quando o pico não é F (ou com o menor fator F, se ele for maior).
    """
    by_letter = {}
    for row in rows:
        by_letter.setdefault(row["los"], []).append(row["factor"])
    levels = {}
    for letter in LOS_LETTERS:
        factors = sorted(by_letter.get(letter, []))
        if not factors:
            continue
        if 1.0 in factors:
            levels[letter] = 1.0
        elif letter == "F":
            levels[letter] = max(los_params["saturated_factor"], factors[0])
        else:
            middle = (factors[0] + factors[-1]) / 2
            rounded = round(round(middle / 0.05) * 0.05, 2)
            levels[letter] = rounded if factors[0] <= rounded <= factors[-1] else min(factors, key=lambda f: abs(f - middle))
    return levels


def main():
    parser = argparse.ArgumentParser(description="Etapa 1.3: varredura de demanda e LOS do HCM por topologia")
    parser.add_argument("--topology", nargs="+", help="Só estas topologias (as demais ficam como estão no JSON)")
    parser.add_argument("--factors", nargs="+", type=float,
                        help="Varredura fina: simula só estes fatores e junta as linhas às já gravadas (substitui as "
                             "de mesmo fator); sem a opção, usa los.factors e refaz a topologia")
    args = parser.parse_args()

    params = load_calibration_params()
    scenarios = load_json(CONFIG_DIR / "cet_scenarios.json")["routes"]
    calibrated = load_json(CONFIG_DIR / "route_flows_calibrated.json")["routes"]
    krauss_profiles = load_json(CONFIG_DIR / "krauss_calibration.json")
    if krauss_profiles.get("metadata", {}).get("smoke"):
        raise ValueError("krauss_calibration.json veio de --smoke; rode a calibração completa antes")
    topologies = args.topology or sorted({route["topology"] for route in scenarios.values()})
    output = load_json(OUTPUT_PATH) if OUTPUT_PATH.is_file() else {}
    output["metadata"] = {
        "source": "src/map_los.py: demanda = fator × fluxo médio de cet.synthetic.reference_year, perfil Krauss calibrado",
        "hcm": "HCM 6ª/7ª ed.: freeway (densidade), urban street (velocidade de percurso / fluxo livre), "
               "signalized intersection (atraso de controle)",
        "seeds": params["los"]["seeds"],
    }
    with ThreadPoolExecutor(params["execution"]["max_workers"]) as pool:
        for topology in topologies:
            result = sweep_topology(topology, scenarios, calibrated, krauss_profiles, params, pool,
                                    args.factors or params["los"]["factors"])
            if args.factors and topology in output:
                kept = [row for row in output[topology]["rows"] if row["factor"] not in args.factors]
                result["rows"] = sorted(kept + result["rows"], key=lambda row: row["factor"])
            result["levels"] = representative_levels(result["rows"], params["los"])
            print(f"[LOS] {topology}: níveis do experimento "
                  + ", ".join(f"{letter} {factor:.2f}×" for letter, factor in result["levels"].items()))
            output[topology] = result
            OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Varredura de LOS salva em {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
