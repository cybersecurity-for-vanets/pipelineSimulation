"""Etapa 1.1: reparos da rede SUMO ao longo das rotas CET, depois do netconvert e do mapeamento das rotas.

O netconvert gera, a partir do OSM, duas coisas que não existem na via real e que travam o fluxo observado da CET:

1. Conexões de faixa. Sem `turn:lanes` no OSM, o netconvert distribui as faixas entre as saídas de cada junção. Em
   junções agrupadas (--junctions.join) da R. da Consolação, só 1 das 4 faixas segue em frente (as outras viram à
   esquerda ou vão para a faixa de ônibus no contrafluxo). Com ~2.300 veic/h, isso limita a vazão a ~500 veic/h.
   Reparo: nas junções da rota em que menos faixas seguem em frente do que min(faixas de entrada, faixas de saída
   que chegam ao fim da rota), as faixas da direita passam a seguir em frente também (faixas compartilhadas, como é comum em São Paulo). As
   conversões existentes ficam.
2. Planos semafóricos. O OSM não traz os planos da CET; o netconvert cria um plano de 90 s que divide o verde entre
   todas as aproximações. Em junções com muitas aproximações, a via principal fica com 15–28% de verde.
   Reparo: em cada semáforo da rota, o verde de cada sentido de via com contagem CET (demand = cet_volume) passa a ser
   pelo menos network.min_main_green_share do ciclo. O ciclo e as fases de amarelo não mudam; as demais fases verdes
   ficam com pelo menos network.min_other_green_s (ou a duração original, se menor). Entre os planos que atendem ao
   mínimo, um programa linear escolhe o mais próximo do plano original (menor soma das mudanças de duração). Se não
   houver plano que atenda ao mínimo, maximiza o menor verde entre os sentidos.

Os dois reparos são idempotentes: rodar de novo sobre uma rede já reparada não muda nada.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

from src.traffic.route_utils import import_sumolib


OBSERVED = "cet_volume"


def _netconvert():
    binary = os.environ.get("NETCONVERT_BINARY") or shutil.which("netconvert")
    if not binary:
        raise FileNotFoundError("netconvert não está no PATH; defina NETCONVERT_BINARY")
    return binary


def _rebuild(net_path, option, patch_text, suffix):
    """Reescreve net_path com netconvert -s e um arquivo de correção (conexões ou planos semafóricos)."""
    with tempfile.TemporaryDirectory(prefix="net_repair_") as tmp_dir:
        patch = Path(tmp_dir) / f"patch{suffix}"
        patch.write_text(patch_text, encoding="utf-8")
        output = Path(tmp_dir) / "net.xml"
        result = subprocess.run(
            [_netconvert(), "-s", str(net_path), option, str(patch), "-o", str(output), "--no-warnings"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"netconvert falhou ao reparar {net_path}: {result.stderr.strip()}")
        shutil.move(str(output), str(net_path))


def _passenger_lanes(edge):
    return [lane for lane in edge.getLanes() if lane.allows("passenger")]


def repair_through_lanes(net_path, routes):
    """Faixas compartilhadas para o movimento direto das rotas. Devolve [(de, para, faixas antes, depois)].

    Conta como "seguindo em frente" só a faixa ligada a uma faixa da aresta seguinte que, por sua vez, chega ao fim da
    rota (passada de trás para a frente). Uma faixa que só leva a uma faixa de saída mais adiante não conta: na Nove
    de Julho sentido bairro, a faixa da direita entre os semáforos da R. Alemanha e da R. Turquia seguia para uma faixa
    que sai da avenida 50 m depois, e todo o volume ficava numa faixa só (~1.000 veic/h).
    """
    sumolib = import_sumolib()
    net = sumolib.net.readNet(str(net_path))
    added, report = set(), []
    for edges in routes.values():
        path = [net.getEdge(edge_id) for edge_id in edges]
        usable = {lane.getIndex() for lane in _passenger_lanes(path[-1])}
        for edge, following in zip(reversed(path[:-1]), reversed(path[1:])):
            from_lanes = _passenger_lanes(edge)
            to_lanes = [lane for lane in _passenger_lanes(following) if lane.getIndex() in usable]
            through = {c.getFromLane().getIndex() for c in edge.getOutgoing().get(following, [])
                       if c.getToLane().getIndex() in usable}
            through |= {from_lane for (from_id, to_id, from_lane, _) in added
                        if from_id == edge.getID() and to_id == following.getID()}
            wanted = min(len(from_lanes), len(to_lanes))
            if len(through) < wanted:
                before = len(through)
                # Índice 0 é a faixa da direita: as faixas da direita seguem em frente, alinhadas pela direita.
                for position, lane in enumerate(from_lanes[:wanted]):
                    if lane.getIndex() not in through:
                        to_lane = to_lanes[min(position, len(to_lanes) - 1)]
                        added.add((edge.getID(), following.getID(), lane.getIndex(), to_lane.getIndex()))
                        through.add(lane.getIndex())
                report.append((edge.getID(), following.getID(), before, len(through)))
            usable = through
    if added:
        lines = [f'    <connection from="{from_id}" to="{to_id}" fromLane="{from_lane}" toLane="{to_lane}"/>'
                 for from_id, to_id, from_lane, to_lane in sorted(added)]
        _rebuild(net_path, "--connection-files", "<connections>\n" + "\n".join(lines) + "\n</connections>\n", ".con.xml")
    return sorted(set(report))


def _balance_green(phases, cycle, members, min_share, min_other_s):
    """Durações das fases verdes (índices em `phases`) que dão a cada sentido pelo menos min_share do ciclo.

    members: {sentido: vetor 0/1 de quais fases verdes dão verde ao sentido}. Devolve as novas durações.
    """
    original = np.array([duration for _, duration in phases])
    count = len(original)
    lower = np.minimum(original, min_other_s)
    # Variáveis: durações d e desvios u >= |d - original|; minimiza sum(u).
    cost = np.r_[np.zeros(count), np.ones(count)]
    a_ub, b_ub = [], []
    for i in range(count):
        row = np.zeros(2 * count)
        row[i], row[count + i] = 1, -1
        a_ub.append(row)
        b_ub.append(original[i])
        row = np.zeros(2 * count)
        row[i], row[count + i] = -1, -1
        a_ub.append(row)
        b_ub.append(-original[i])
    for member in members.values():
        a_ub.append(np.r_[-member, np.zeros(count)])
        b_ub.append(-min_share * cycle)
    result = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=[np.r_[np.ones(count), np.zeros(count)]],
                     b_eq=[original.sum()], bounds=[(low, None) for low in lower] + [(0, None)] * count)
    if result.status == 0:
        return result.x[:count]
    # Inviável: maximiza t, o menor verde (fração do ciclo) entre os sentidos.
    cost = np.r_[np.zeros(count), -1.0]
    a_ub = [np.r_[-member, cycle] for member in members.values()]
    result = linprog(cost, A_ub=a_ub, b_ub=np.zeros(len(a_ub)), A_eq=[np.r_[np.ones(count), 0.0]],
                     b_eq=[original.sum()], bounds=[(low, None) for low in lower] + [(0, 1)])
    if result.status != 0:
        return original
    return result.x[:count]


def repair_main_green(net_path, observed_routes, min_share, min_other_s):
    """Verde mínimo dos sentidos com contagem CET em cada semáforo da rota. Devolve [(tls, {sentido: (antes, depois)})]."""
    sumolib = import_sumolib()
    net = sumolib.net.readNet(str(net_path), withPrograms=True)
    links = {}  # {tls: {rota: {índice do link}}}
    for name, edges in observed_routes.items():
        for from_id, to_id in zip(edges, edges[1:]):
            for connection in net.getEdge(from_id).getOutgoing().get(net.getEdge(to_id), []):
                if connection.getTLSID():
                    links.setdefault(connection.getTLSID(), {}).setdefault(name, set()).add(connection.getTLLinkIndex())

    programs, report = [], []
    for tls_id, by_route in sorted(links.items()):
        programs_by_id = net.getTLS(tls_id).getPrograms()
        program_id, program = next(iter(programs_by_id.items()))
        phases = program.getPhases()
        cycle = sum(phase.duration for phase in phases)
        green = [i for i, phase in enumerate(phases)
                 if "y" not in phase.state and any(ch in "Gg" for ch in phase.state)]
        members = {
            name: np.array([1.0 if any(phases[i].state[k] in "Gg" for k in indices) else 0.0 for i in green])
            for name, indices in by_route.items()
        }
        durations = [phase.duration for phase in phases]
        before = {name: float(member @ np.array([durations[i] for i in green])) / cycle
                  for name, member in members.items()}
        if green and min(before.values()) < min_share - 1e-6:
            new = _balance_green([(i, durations[i]) for i in green], cycle, members, min_share, min_other_s)
            for position, i in enumerate(green):
                durations[i] = round(float(new[position]), 1)
            # Arredondamento: o ciclo não muda.
            durations[green[-1]] = round(durations[green[-1]] + cycle - sum(durations), 1)
        after = {name: float(member @ np.array([durations[i] for i in green])) / cycle
                 for name, member in members.items()}
        if any(abs(after[name] - before[name]) > 1e-3 for name in members):
            report.append((tls_id, {name: (before[name], after[name]) for name in sorted(members)}))
            offset = getattr(program, "_offset", 0) or 0
            lines = [f'    <tlLogic id="{tls_id}" type="static" programID="{program_id}" offset="{offset}">']
            lines += [f'        <phase duration="{duration}" state="{phase.state}"/>'
                      for phase, duration in zip(phases, durations)]
            lines.append("    </tlLogic>")
            programs.append("\n".join(lines))
    if programs:
        _rebuild(net_path, "--tllogic-files", "<additional>\n" + "\n".join(programs) + "\n</additional>\n", ".add.xml")
    return report


def repair_network(net_path, routes, network_params):
    """Aplica os dois reparos a uma topologia. routes: {rota: {"sumo_edges", "demand"}}."""
    lane_report = repair_through_lanes(net_path, {name: route["sumo_edges"] for name, route in routes.items()})
    for from_id, to_id, before, wanted in lane_report:
        print(f"[REDE] {net_path.name}: {from_id} -> {to_id}: {before} -> {wanted} faixa(s) seguindo em frente")
    observed = {name: route["sumo_edges"] for name, route in routes.items() if route.get("demand", OBSERVED) == OBSERVED}
    green_report = repair_main_green(net_path, observed, network_params["min_main_green_share"],
                                     network_params["min_other_green_s"])
    for tls_id, shares in green_report:
        text = ", ".join(f"{name} {before:.0%} -> {after:.0%}" for name, (before, after) in shares.items())
        print(f"[REDE] {net_path.name}: semáforo {tls_id}: verde {text}")
    if not lane_report and not green_report:
        print(f"[REDE] {net_path.name}: nada a reparar")
