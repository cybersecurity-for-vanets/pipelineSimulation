"""Níveis de fluxo do experimento: uma hora pesquisada pela CET (MSVP 2019) por nível.

Contrato entre as etapas:
- generate_routes.py grava conf/flow_levels.json depois de gerar as rotas de cada nível;
- generate_stage2_configs.py cria uma seção [Config Config_<topologia>_<protocolo>_<nível>] por nível;
- a etapa 4 e os gráficos tiram o nível do sufixo do nome do config e o fluxo de conf/flow_levels.json.

O id do nível é "h" + hora de início com dois dígitos (h07 = 07:00–08:00).

veh_h_per_lane = fluxo total da topologia no nível ÷ soma das faixas típicas (mediana, lanes_per_direction) das rotas.

Formato de conf/flow_levels.json:
{
  "levels": ["h07", ...],                      # em ordem de hora
  "labels": {"h07": "07h–08h", ...},
  "topologies": {
    "<topologia do arquivo, ex. urbanRoad>": {
      "h07": {"total_veh_h": ..., "veh_h_per_lane": ..., "routes": {"<rota>": veh/h}}
    }
  }
}
"""
import json
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
FLOW_LEVELS_PATH = BASE_DIR / "conf" / "flow_levels.json"
LEVEL_PATTERN = re.compile(r"h(\d{2})")
# Sufixo de nível no nome de um config ou arquivo, ex. Config_highway_FF1_AES_h07.
LEVEL_SUFFIX_PATTERN = re.compile(r"_(h\d{2})$")


def level_id(hour):
    return f"h{int(hour):02d}"


def level_hour(level):
    match = LEVEL_PATTERN.fullmatch(level)
    if not match:
        raise ValueError(f"Nível de fluxo inválido: {level!r} (esperado h + hora com dois dígitos, ex. h07)")
    return int(match.group(1))


def level_label(level):
    hour = level_hour(level)
    return f"{hour:02d}h–{hour + 1:02d}h"


def split_level(name):
    """('Config_highway_FF1_AES', 'h07') para 'Config_highway_FF1_AES_h07'; (name, None) sem sufixo de nível."""
    match = LEVEL_SUFFIX_PATTERN.search(name)
    if not match:
        return name, None
    return name[:match.start()], match.group(1)


def load_flow_levels(path=FLOW_LEVELS_PATH):
    if not path.is_file():
        raise FileNotFoundError(f"{path} não existe; rode src/generate_routes.py primeiro")
    with path.open("r", encoding="utf-8") as source:
        data = json.load(source)
    data["levels"] = sorted(data["levels"], key=level_hour)
    return data


def flow_level_ids(path=FLOW_LEVELS_PATH):
    return load_flow_levels(path)["levels"]


def flow_per_lane(topology, level, path=FLOW_LEVELS_PATH):
    """veh/h/faixa do nível na topologia (nome de arquivo, ex. urbanRoad)."""
    return load_flow_levels(path)["topologies"][topology][level]["veh_h_per_lane"]
