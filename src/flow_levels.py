"""Níveis de fluxo: volumes CET observados (ano e hora, usados na calibração) e níveis de demanda do experimento.

Contrato entre as etapas:
- generate_routes.py grava conf/flow_levels.json depois de gerar as rotas de cada nível;
- generate_stage2_configs.py cria uma seção [Config Config_<topologia>_<protocolo>_<nível>] por nível da topologia;
- a etapa 4 e os gráficos tiram o nível do sufixo do nome do config e o fluxo de conf/flow_levels.json.

Ids de nível:
- observado: "y" + ano + "h" + hora de início com dois dígitos (y2019h07 = volume CET de 2019, 07:00–08:00);
- sintético: "s" + fator × 100 com três dígitos (s050 = 0,50 × fluxo médio observado no ano de referência).
  Desde 2026-11-06 o experimento V2V usa só níveis sintéticos, um por nível de serviço (LOS) do HCM alcançado na
  topologia (map_los.py); s100 é o pico CET (média das horas observadas). Cada entrada tem "los".

veh_h_per_lane = fluxo total da topologia no nível ÷ soma das faixas típicas (mediana, lanes_per_direction) das rotas.
Cada topologia tem a sua lista de níveis: nem todo ano da CET pesquisou todas as vias.

Formato de conf/flow_levels.json:
{
  "levels": ["y2016h07", ..., "s025", ...],      # união das topologias, em ordem de level_sort_key
  "labels": {"y2019h07": "07h–08h/2019", "s050": "0,50× (sintético)", ...},
  "topologies": {
    "<topologia do arquivo, ex. urbanRoad>": {
      "y2019h07": {"kind": "observed", "total_veh_h": ..., "veh_h_per_lane": ..., "routes": {"<rota>": veh/h}},
      "s050": {"kind": "synthetic", "factor": 0.5, "reference_year": 2019, "los": "C", ...}
    }
  }
}
"""
import json
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
FLOW_LEVELS_PATH = BASE_DIR / "conf" / "flow_levels.json"
OBSERVED_PATTERN = re.compile(r"y(\d{4})h(\d{2})")
SYNTHETIC_PATTERN = re.compile(r"s(\d{3})")
# Sufixo de nível no nome de um config ou arquivo, ex. Config_highway_FF1_AES_y2019h07.
LEVEL_SUFFIX_PATTERN = re.compile(r"_(y\d{4}h\d{2}|s\d{3})$")


def level_id(year, hour):
    return f"y{int(year)}h{int(hour):02d}"


def synthetic_level_id(factor):
    return f"s{round(factor * 100):03d}"


def parse_level(level):
    """{"kind": "observed", "year", "hour"} ou {"kind": "synthetic", "factor"}."""
    match = OBSERVED_PATTERN.fullmatch(level)
    if match:
        return {"kind": "observed", "year": int(match.group(1)), "hour": int(match.group(2))}
    match = SYNTHETIC_PATTERN.fullmatch(level)
    if match:
        return {"kind": "synthetic", "factor": int(match.group(1)) / 100}
    raise ValueError(f"Nível de fluxo inválido: {level!r} (esperado y<ano>h<hora>, ex. y2019h07, ou s<fator×100>, ex. s050)")


def is_synthetic(level):
    return parse_level(level)["kind"] == "synthetic"


def level_hour(level):
    """Hora de início do nível observado; None para nível sintético."""
    return parse_level(level).get("hour")


def level_year(level):
    """Ano do nível observado; None para nível sintético."""
    return parse_level(level).get("year")


def level_sort_key(level):
    """Observados por (ano, hora), depois sintéticos por fator."""
    info = parse_level(level)
    if info["kind"] == "observed":
        return (0, info["year"], info["hour"], 0.0)
    return (1, 0, 0, info["factor"])


def level_label(level):
    info = parse_level(level)
    if info["kind"] == "observed":
        return f"{info['hour']:02d}h–{info['hour'] + 1:02d}h/{info['year']}"
    if info["factor"] == 1.0:
        return "1,00× (pico CET)"
    return f"{info['factor']:.2f}×".replace(".", ",")


def level_los(topology, level, path=FLOW_LEVELS_PATH):
    """LOS do HCM do nível na topologia (nome de arquivo, ex. urbanRoad); None para níveis sem LOS."""
    return load_flow_levels(path)["topologies"][topology][level].get("los")


def split_level(name):
    """('Config_highway_FF1_AES', 'y2019h07') para 'Config_highway_FF1_AES_y2019h07'; (name, None) sem nível."""
    match = LEVEL_SUFFIX_PATTERN.search(name)
    if not match:
        return name, None
    return name[:match.start()], match.group(1)


def load_flow_levels(path=FLOW_LEVELS_PATH):
    if not path.is_file():
        raise FileNotFoundError(f"{path} não existe; rode src/generate_routes.py primeiro")
    with path.open("r", encoding="utf-8") as source:
        data = json.load(source)
    data["levels"] = sorted(data["levels"], key=level_sort_key)
    return data


def flow_level_ids(path=FLOW_LEVELS_PATH):
    """Todos os níveis (união das topologias), em ordem de level_sort_key."""
    return load_flow_levels(path)["levels"]


def topology_level_ids(topology, path=FLOW_LEVELS_PATH):
    """Níveis de uma topologia (nome de arquivo, ex. urbanRoad), em ordem de level_sort_key."""
    return sorted(load_flow_levels(path)["topologies"][topology], key=level_sort_key)


def flow_per_lane(topology, level, path=FLOW_LEVELS_PATH):
    """veh/h/faixa do nível na topologia (nome de arquivo, ex. urbanRoad)."""
    return load_flow_levels(path)["topologies"][topology][level]["veh_h_per_lane"]


def is_current_config(config_name, path=FLOW_LEVELS_PATH):
    """True se o config (ex. Config_urbanRoad_FF1_AES_y2019h07) é de um nível atual de conf/flow_levels.json.

    Resultados de rodadas antigas (low_flow, h07, níveis que não existem mais) dão False, para que a etapa 4 não os
    misture com os atuais.
    """
    name, level = split_level(config_name.removeprefix("Config_"))
    if level is None:
        return False
    topologies = load_flow_levels(path)["topologies"]
    return any(name.startswith(f"{topology}_") and level in levels for topology, levels in topologies.items())


def current_result_files(paths, path=FLOW_LEVELS_PATH):
    """(atuais, ignorados) entre arquivos de resultado do OMNeT++ (<config>-<...>.sca/.vec), pelo nome do config."""
    import os
    current, ignored = [], []
    for result_path in paths:
        config = os.path.basename(result_path).split("-", 1)[0]
        (current if is_current_config(config, path) else ignored).append(result_path)
    return current, ignored
