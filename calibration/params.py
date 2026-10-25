"""Único ponto de leitura e validação de conf/calibration_params.json.

Os demais scripts de calibração importam daqui; nenhum lê o JSON diretamente.
Executado direto, só valida o arquivo e mostra um resumo.
"""
import json
from datetime import date
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
PARAMS_PATH = PROJECT_DIR / "conf" / "calibration_params.json"
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
KRAUSS_KEYS = ("accel", "decel", "tau", "sigma", "minGap")
AGGREGATIONS = ("arithmetic", "harmonic", "median")
SENSITIVITY_METHODS = ("morris",)
DISPERSION_METRICS = ("travel_time_cv", "travel_time_p95_median")


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

    days = _field(params, "tomtom.days_of_week")
    if not _is_list(days) or not days or len(set(days)) != len(days) or any(day not in WEEKDAYS for day in days):
        raise CalibrationParamsError(f"tomtom.days_of_week deve ser lista sem repetição de {WEEKDAYS}")
    excluded = _field(params, "tomtom.exclude_dates")
    if not _is_list(excluded) or len(set(excluded)) != len(excluded):
        raise CalibrationParamsError("tomtom.exclude_dates deve ser lista de datas AAAA-MM-DD sem repetição")
    for value in excluded:
        _parse_date(value, "tomtom.exclude_dates")

    signal = _field(params, "topologies.signal_coordinated")
    speed = _field(params, "topologies.speed_factor")
    if not _is_list(signal) or not _is_list(speed) or not (signal or speed):
        raise CalibrationParamsError("topologies.signal_coordinated e speed_factor devem ser listas")
    if set(signal) & set(speed):
        raise CalibrationParamsError("Uma topologia não pode estar em signal_coordinated e speed_factor")

    _check_interval(_field(params, "speed_factor.interval"), "speed_factor.interval", minimum_exclusive=0)

    _check_number(_field(params, "flow.min_veh_h"), "flow.min_veh_h", minimum_exclusive=0)
    _check_number(_field(params, "flow.max_veh_h_per_lane"), "flow.max_veh_h_per_lane", minimum_exclusive=0)
    _check_number(_field(params, "flow.tolerance_kmh"), "flow.tolerance_kmh", minimum_exclusive=0)
    _check_integer(_field(params, "flow.max_iterations"), "flow.max_iterations", minimum=1)
    seeds = _field(params, "flow.stability_seeds")
    if not _is_list(seeds) or not seeds or not all(_is_integer(seed) for seed in seeds):
        raise CalibrationParamsError("flow.stability_seeds deve ser lista não vazia de inteiros")

    replica_seeds = _field(params, "replicas.seeds")
    if not _is_list(replica_seeds) or len(replica_seeds) < 2 or len(set(replica_seeds)) != len(replica_seeds) \
            or not all(_is_integer(seed) for seed in replica_seeds):
        raise CalibrationParamsError("replicas.seeds deve ser lista de pelo menos 2 inteiros sem repetição")
    _check_number(_field(params, "acceptance.max_depart_delay_s"), "acceptance.max_depart_delay_s", minimum_exclusive=0)
    waiting = _field(params, "acceptance.max_waiting_fraction")
    if not _is_number(waiting) or not 0 <= waiting < 1:
        raise CalibrationParamsError("acceptance.max_waiting_fraction deve ficar entre 0 e 1")

    if _field(params, "sensitivity.method") not in SENSITIVITY_METHODS:
        raise CalibrationParamsError(f"sensitivity.method deve ser um de {SENSITIVITY_METHODS}")
    _check_integer(_field(params, "sensitivity.trajectories"), "sensitivity.trajectories", minimum=2)
    levels = _field(params, "sensitivity.levels")
    if not _is_integer(levels) or levels < 2 or levels % 2:
        raise CalibrationParamsError("sensitivity.levels deve ser inteiro par maior ou igual a 2")
    sensitivity_seeds = _field(params, "sensitivity.seeds")
    if not _is_list(sensitivity_seeds) or not sensitivity_seeds or len(set(sensitivity_seeds)) != len(sensitivity_seeds) \
            or not all(_is_integer(seed) for seed in sensitivity_seeds):
        raise CalibrationParamsError("sensitivity.seeds deve ser lista não vazia de inteiros sem repetição")
    _check_integer(_field(params, "sensitivity.sampler_seed"), "sensitivity.sampler_seed", minimum=0)
    _check_interval(_field(params, "sensitivity.flow_relative_range"), "sensitivity.flow_relative_range",
                    minimum_exclusive=0)
    _check_number(_field(params, "sensitivity.min_effect_kmh"), "sensitivity.min_effect_kmh", minimum_exclusive=0)
    _check_number(_field(params, "sensitivity.min_effect_cv"), "sensitivity.min_effect_cv", minimum_exclusive=0)
    _check_number(_field(params, "sensitivity.noise_multiplier"), "sensitivity.noise_multiplier", minimum_exclusive=0)

    speed_dev = _field(params, "joint.speed_dev")
    if not isinstance(speed_dev, dict) or set(speed_dev) != {"default", "bounds"}:
        raise CalibrationParamsError("joint.speed_dev deve ter as chaves default e bounds")
    _check_interval(speed_dev["bounds"], "joint.speed_dev.bounds", minimum_exclusive=None)
    _check_number(speed_dev["default"], "joint.speed_dev.default")
    if not speed_dev["bounds"][0] <= speed_dev["default"] <= speed_dev["bounds"][1] or speed_dev["bounds"][0] < 0:
        raise CalibrationParamsError("joint.speed_dev.default deve ficar dentro de joint.speed_dev.bounds, que não pode ser negativo")
    joint_seeds = _field(params, "joint.seeds")
    if not _is_list(joint_seeds) or len(joint_seeds) < 2 or len(set(joint_seeds)) != len(joint_seeds) \
            or not all(_is_integer(seed) for seed in joint_seeds):
        raise CalibrationParamsError("joint.seeds deve ser lista de pelo menos 2 inteiros sem repetição")
    bracket = _field(params, "joint.flow_bracket")
    _check_interval(bracket, "joint.flow_bracket", minimum_exclusive=0)
    if not bracket[0] < 1 < bracket[1]:
        raise CalibrationParamsError("joint.flow_bracket deve conter 1 (o fluxo da T2)")
    _check_number(_field(params, "joint.speed_factor_bracket"), "joint.speed_factor_bracket", minimum_exclusive=0)
    _check_integer(_field(params, "joint.sampler_seed"), "joint.sampler_seed", minimum=0)
    joint_trials = _field(params, "joint.n_trials")
    _check_integer(joint_trials, "joint.n_trials", minimum=1)
    _check_integer(_field(params, "joint.n_startup_trials"), "joint.n_startup_trials", minimum=0)
    if params["joint"]["n_startup_trials"] > joint_trials:
        raise CalibrationParamsError("joint.n_startup_trials não pode passar de joint.n_trials")
    _check_integer(_field(params, "joint.stagnation_patience"), "joint.stagnation_patience", minimum=1)
    _check_number(_field(params, "joint.target_max_cv_error"), "joint.target_max_cv_error", minimum_exclusive=0)
    _check_number(_field(params, "joint.infeasible_penalty"), "joint.infeasible_penalty", minimum_exclusive=0)

    _check_integer(_field(params, "simulation.seconds"), "simulation.seconds", minimum=1)
    _check_integer(_field(params, "simulation.warmup_s"), "simulation.warmup_s", minimum=0)
    if params["simulation"]["warmup_s"] >= params["simulation"]["seconds"]:
        raise CalibrationParamsError("simulation.warmup_s deve ser menor que simulation.seconds")
    error = _field(params, "checks.max_length_ratio_error")
    if not _is_number(error) or not 0 < error < 1:
        raise CalibrationParamsError("checks.max_length_ratio_error deve ficar entre 0 e 1")
    _check_integer(_field(params, "checks.window_s"), "checks.window_s", minimum=1)
    cutoffs = _field(params, "checks.cutoffs_s")
    if not _is_list(cutoffs) or not cutoffs or len(set(cutoffs)) != len(cutoffs) \
            or not all(_is_integer(cut) and 0 <= cut < params["simulation"]["seconds"] for cut in cutoffs):
        raise CalibrationParamsError("checks.cutoffs_s deve ser lista sem repetição de inteiros entre 0 e simulation.seconds")

    segment_sample = _field(params, "segments.min_normalized_sample_size")
    if not _is_number(segment_sample) or not 0 <= segment_sample <= 1:
        raise CalibrationParamsError("segments.min_normalized_sample_size deve ficar entre 0 e 1")
    _check_integer(_field(params, "segments.min_valid_cells"), "segments.min_valid_cells", minimum=1)
    segment_seeds = _field(params, "segments.seeds")
    if not _is_list(segment_seeds) or not segment_seeds or len(set(segment_seeds)) != len(segment_seeds) \
            or not all(_is_integer(seed) for seed in segment_seeds):
        raise CalibrationParamsError("segments.seeds deve ser lista não vazia de inteiros sem repetição")
    _check_number(_field(params, "segments.fcd_period_s"), "segments.fcd_period_s", minimum_exclusive=0)
    _check_number(_field(params, "segments.max_projection_distance_m"), "segments.max_projection_distance_m",
                  minimum_exclusive=0)
    if _field(params, "dispersion.metric") not in DISPERSION_METRICS:
        raise CalibrationParamsError(f"dispersion.metric deve ser um de {DISPERSION_METRICS}")

    regimes = _field(params, "target.regimes")
    if not isinstance(regimes, dict) or not regimes:
        raise CalibrationParamsError("target.regimes deve ser objeto com pelo menos um regime")
    used_hours = []
    for name, regime in regimes.items():
        hours = regime.get("hours") if isinstance(regime, dict) else None
        if not _is_list(hours) or not hours or len(set(hours)) != len(hours) \
                or not all(_is_integer(hour) and 0 <= hour <= 23 for hour in hours):
            raise CalibrationParamsError(f"target.regimes.{name}.hours deve ser lista de horas iniciais (0 a 23), sem repetição")
        used_hours.extend(hours)
    if len(set(used_hours)) != len(used_hours):
        raise CalibrationParamsError("Dois regimes em target.regimes não podem usar a mesma hora")
    aggregation = _field(params, "target.aggregation")
    if aggregation not in AGGREGATIONS:
        raise CalibrationParamsError(f"target.aggregation deve ser um de {AGGREGATIONS}")
    min_sample = _field(params, "target.min_normalized_sample_size")
    if not _is_number(min_sample) or not 0 <= min_sample <= 1:
        raise CalibrationParamsError("target.min_normalized_sample_size deve ficar entre 0 e 1")

    default = _field(params, "krauss.default")
    bounds = _field(params, "krauss.bounds")
    if not isinstance(default, dict) or set(default) != set(KRAUSS_KEYS):
        raise CalibrationParamsError(f"krauss.default deve ter exatamente as chaves {KRAUSS_KEYS}")
    if not isinstance(bounds, dict) or set(bounds) != set(KRAUSS_KEYS):
        raise CalibrationParamsError(f"krauss.bounds deve ter exatamente as chaves {KRAUSS_KEYS}")
    for key in KRAUSS_KEYS:
        _check_interval(bounds[key], f"krauss.bounds.{key}", minimum_exclusive=None)
        _check_number(default[key], f"krauss.default.{key}")
        low, high = bounds[key]
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
    _check_number(rmse, "krauss.target_relative_rmse", minimum_exclusive=0)
    if rmse > 1:
        raise CalibrationParamsError("krauss.target_relative_rmse deve ficar entre 0 e 1")


def _field(params, dotted):
    value = params
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            raise CalibrationParamsError(f"Campo ausente em calibration_params.json: {dotted}")
        value = value[part]
    return value


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


def _check_interval(value, name, minimum_exclusive=None):
    if not _is_list(value) or len(value) != 2 or not all(_is_number(item) for item in value):
        raise CalibrationParamsError(f"{name} deve ser [mínimo, máximo]")
    low, high = value
    if low >= high:
        raise CalibrationParamsError(f"{name}: o mínimo deve ser menor que o máximo")
    if minimum_exclusive is not None and low <= minimum_exclusive:
        raise CalibrationParamsError(f"{name}: o mínimo deve ser maior que {minimum_exclusive}")


def _parse_date(value, name):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise CalibrationParamsError(f"{name} contém data inválida: {value!r}") from error


def main():
    params = load_calibration_params()
    print(f"[OK] {PARAMS_PATH} é válido")
    print(f"Seções: {', '.join(params)}")
    print(f"Dias úteis do alvo: {', '.join(params['tomtom']['days_of_week'])}")
    print(f"Krauss: {len(params['krauss']['bounds'])} parâmetros com limites")


if __name__ == "__main__":
    main()
