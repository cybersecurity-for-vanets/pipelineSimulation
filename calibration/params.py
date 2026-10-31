"""Único ponto de leitura e validação de conf/calibration_params.json.

Os scripts da etapa 1 importam daqui; nenhum lê o JSON diretamente.
Executado direto, só valida o arquivo e mostra um resumo.
"""
import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
PARAMS_PATH = PROJECT_DIR / "conf" / "calibration_params.json"
KRAUSS_KEYS = ("accel", "decel", "tau", "sigma", "minGap", "speedFactor", "speedDev")
# Colunas de conf/cet_volumes.csv que entram no equivalente-carro.
CET_VEHICLE_CLASSES = ("automoveis", "onibus_urbano", "onibus_fretado", "caminhoes", "motos", "bicicletas")
TOPOLOGIES = ("highway", "urban_road", "intersection")


class CalibrationParamsError(ValueError):
    """conf/calibration_params.json está ausente, não é JSON ou tem valor inválido."""


def load_calibration_params(path=PARAMS_PATH):
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo de parâmetros não encontrado: {path}")
    try:
        params = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CalibrationParamsError(f"{path.name} não é JSON válido: {error}") from error
    validate_calibration_params(params)
    return params


def validate_calibration_params(params):
    if not isinstance(params, dict):
        raise CalibrationParamsError("A raiz de calibration_params.json deve ser um objeto")

    pce = _field(params, "cet.pce")
    if not isinstance(pce, dict) or set(pce) != set(CET_VEHICLE_CLASSES):
        raise CalibrationParamsError(f"cet.pce deve ter exatamente as chaves {CET_VEHICLE_CLASSES}")
    for name, factor in pce.items():
        if not _is_number(factor) or factor < 0:
            raise CalibrationParamsError(f"cet.pce.{name} deve ser número não negativo")
    years = _field(params, "cet.years")
    if not _is_list(years) or not years or len(set(years)) != len(years) \
            or not all(_is_integer(year) and 1977 <= year <= 2100 for year in years):
        raise CalibrationParamsError("cet.years deve ser lista não vazia de anos do MSVP, sem repetição")
    hours = _hours(params, "cet.hours")
    calibration_year = _field(params, "cet.calibration.year")
    if calibration_year not in years:
        raise CalibrationParamsError("cet.calibration.year deve estar em cet.years")
    if not set(_hours(params, "cet.calibration.hours")) <= set(hours):
        raise CalibrationParamsError("cet.calibration.hours deve estar contido em cet.hours")
    if _field(params, "cet.synthetic.reference_year") not in years:
        raise CalibrationParamsError("cet.synthetic.reference_year deve estar em cet.years")
    factors = _field(params, "cet.synthetic.factors")
    if not _is_list(factors) or len(set(factors)) != len(factors) \
            or not all(_is_number(factor) and 0 < factor <= 9.99 for factor in factors) \
            or len({round(factor * 100) for factor in factors}) != len(factors):
        raise CalibrationParamsError("cet.synthetic.factors deve ser lista de fatores positivos distintos (precisão 0,01)")

    _check_number(_field(params, "network.map_margin_km"), "network.map_margin_km", minimum_exclusive=0)
    ratio = _field(params, "network.max_length_ratio_error")
    if not _is_number(ratio) or not 0 < ratio < 1:
        raise CalibrationParamsError("network.max_length_ratio_error deve ficar entre 0 e 1")

    signal = _field(params, "topologies.signal_coordinated")
    if not _is_list(signal) or not set(signal) <= set(TOPOLOGIES):
        raise CalibrationParamsError(f"topologies.signal_coordinated deve ser lista com itens de {TOPOLOGIES}")

    _check_number(_field(params, "flow.min_veh_h"), "flow.min_veh_h", minimum_exclusive=0)
    _check_number(_field(params, "flow.max_veh_h_per_lane"), "flow.max_veh_h_per_lane", minimum_exclusive=0)
    _check_number(_field(params, "flow.tolerance_kmh"), "flow.tolerance_kmh", minimum_exclusive=0)
    _check_integer(_field(params, "flow.max_iterations"), "flow.max_iterations", minimum=2)

    _check_integer(_field(params, "simulation.seconds"), "simulation.seconds", minimum=1)
    _check_integer(_field(params, "simulation.warmup_s"), "simulation.warmup_s", minimum=0)
    if params["simulation"]["warmup_s"] >= params["simulation"]["seconds"]:
        raise CalibrationParamsError("simulation.warmup_s deve ser menor que simulation.seconds")
    _seeds(params, "simulation.search_seeds", minimum=1)
    _seeds(params, "simulation.report_seeds", minimum=2)

    _check_number(_field(params, "acceptance.max_depart_delay_s"), "acceptance.max_depart_delay_s", minimum_exclusive=0)
    waiting = _field(params, "acceptance.max_waiting_fraction")
    if not _is_number(waiting) or not 0 <= waiting < 1:
        raise CalibrationParamsError("acceptance.max_waiting_fraction deve ficar entre 0 e 1")

    default = _field(params, "krauss.default")
    bounds = _field(params, "krauss.bounds")
    if not isinstance(default, dict) or set(default) != set(KRAUSS_KEYS):
        raise CalibrationParamsError(f"krauss.default deve ter exatamente as chaves {KRAUSS_KEYS}")
    if not isinstance(bounds, dict) or not set(bounds) <= set(KRAUSS_KEYS) or not bounds:
        raise CalibrationParamsError(f"krauss.bounds deve ter chaves de {KRAUSS_KEYS}; as ausentes ficam fixas no default")
    for key in KRAUSS_KEYS:
        _check_number(default[key], f"krauss.default.{key}")
    for key, interval in bounds.items():
        _check_interval(interval, f"krauss.bounds.{key}")
        low, high = interval
        if not low <= default[key] <= high:
            raise CalibrationParamsError(f"krauss.default.{key} = {default[key]} está fora de krauss.bounds")

    n_trials = _field(params, "krauss.n_trials")
    _check_integer(n_trials, "krauss.n_trials", minimum=1)
    _check_integer(_field(params, "krauss.n_startup_trials"), "krauss.n_startup_trials", minimum=0)
    if params["krauss"]["n_startup_trials"] > n_trials:
        raise CalibrationParamsError("krauss.n_startup_trials não pode passar de krauss.n_trials")
    _check_integer(_field(params, "krauss.sampler_seed"), "krauss.sampler_seed", minimum=0)
    _check_integer(_field(params, "krauss.stagnation_patience"), "krauss.stagnation_patience", minimum=1)
    rmse = _field(params, "krauss.target_relative_rmse")
    if not _is_number(rmse) or not 0 < rmse <= 1:
        raise CalibrationParamsError("krauss.target_relative_rmse deve ficar entre 0 e 1")

    _check_integer(_field(params, "execution.max_workers"), "execution.max_workers", minimum=1)


def _field(params, dotted):
    value = params
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            raise CalibrationParamsError(f"Campo ausente em calibration_params.json: {dotted}")
        value = value[part]
    return value


def _hours(params, dotted):
    hours = _field(params, dotted)
    if not _is_list(hours) or not hours or len(set(hours)) != len(hours) \
            or not all(_is_integer(hour) and 0 <= hour <= 23 for hour in hours):
        raise CalibrationParamsError(f"{dotted} deve ser lista não vazia de horas iniciais (0 a 23), sem repetição")
    return hours


def _seeds(params, dotted, minimum):
    seeds = _field(params, dotted)
    if not _is_list(seeds) or len(seeds) < minimum or len(set(seeds)) != len(seeds) \
            or not all(_is_integer(seed) for seed in seeds):
        raise CalibrationParamsError(f"{dotted} deve ser lista de pelo menos {minimum} inteiro(s) sem repetição")
    return seeds


def _is_list(value):
    return isinstance(value, list)


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_number(value, name, minimum_exclusive=None):
    if not _is_number(value):
        raise CalibrationParamsError(f"{name} deve ser número")
    if minimum_exclusive is not None and value <= minimum_exclusive:
        raise CalibrationParamsError(f"{name} deve ser maior que {minimum_exclusive}")


def _check_integer(value, name, minimum):
    if not _is_integer(value) or value < minimum:
        raise CalibrationParamsError(f"{name} deve ser inteiro maior ou igual a {minimum}")


def _check_interval(value, name):
    if not _is_list(value) or len(value) != 2 or not all(_is_number(item) for item in value):
        raise CalibrationParamsError(f"{name} deve ser [mínimo, máximo]")
    if value[0] >= value[1]:
        raise CalibrationParamsError(f"{name}: o mínimo deve ser menor que o máximo")


def main():
    params = load_calibration_params()
    print(f"[OK] {PARAMS_PATH} é válido")
    cet = params["cet"]
    print(f"Anos CET: {cet['years']}; horas: {cet['hours']}")
    print(f"Calibração: {cet['calibration']['year']}, horas {cet['calibration']['hours']}; "
          "validação: todas as demais horas e anos observados")
    print(f"Níveis sintéticos: {cet['synthetic']['factors']} × fluxo médio de {cet['synthetic']['reference_year']}")
    print(f"Krauss: {len(params['krauss']['bounds'])} parâmetros livres de {len(KRAUSS_KEYS)}")


if __name__ == "__main__":
    main()
