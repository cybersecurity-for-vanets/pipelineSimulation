import os
import json
import xml.etree.ElementTree as ET
import subprocess
import shutil
import pandas as pd
import numpy as np
import optuna

# ==============================================================================
# 1. CONFIGURACIÓN DE ENTORNO Y SELECCIÓN DE ESCENARIO HCM
# ==============================================================================
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_DIR = os.path.join(PROJECT_DIR, "conf")
RAW_DATA_DIR = os.path.join(PROJECT_DIR, "results", "rawData")
SUMO_BINARY = os.environ.get("SUMO_BINARY") or shutil.which("sumo") or "/usr/local/bin/sumo"
SUMO_CONFIG = os.path.join(CONF_DIR, "scenario.sumocfg")
VTYPE_FILE = os.path.join(CONF_DIR, "vehicle_types.xml")
OUTPUT_DUMP = os.path.join(RAW_DATA_DIR, "edge_data_output.xml")
EMPIRICAL_JSON = os.path.join(CONF_DIR, "tomtom_calibrated_rates.json")

# Escenario a calibrar en esta ejecución: 'high_flow', 'medium_flow', o 'low_flow'
TARGET_SCENARIO = "high_flow"  
ROUTE_FILE = os.path.join(PROJECT_DIR, "trafficXmlFiles", f"routes_{TARGET_SCENARIO}.rou.xml")
N_TRIALS = 50  # Iteraciones de búsqueda con Optuna

# ==============================================================================
# 2. CARGA DINÁMICA DE DATOS EMPÍRICOS (DESDE ETAPA PRELIMINAR)
# ==============================================================================
def load_empirical_speeds(json_path, scenario_name):
    """
    Carga del archivo JSON generado por fetch_data.py
    las velocidades medias empíricas (m/s) para el escenario seleccionado.
    """
    if not os.path.exists(json_path):
        raise FileNotFoundError(
            f"No se encontró el archivo '{json_path}'. Ejecuta primero fetch_data.py"
        )
        
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    scenario_dict = data["scenarios"].get(scenario_name, {})
    empirical_speeds = {}
    
    for edge_id, metrics in scenario_dict.items():
        mean_spd = metrics.get("mean_speed")
        if mean_spd is not None:
            empirical_speeds[edge_id] = mean_spd
            
    if not empirical_speeds:
        raise ValueError(f"No hay datos de velocidad válidos para el escenario '{scenario_name}' en {json_path}")
        
    print(f"[LOAD] Datos empíricos cargados para escenario [{scenario_name.upper()}]:")
    for edge, spd in empirical_speeds.items():
        print(f"  - Edge '{edge}': {spd} m/s ({spd*3.6:.1f} km/h)")
        
    return empirical_speeds

# Carga inicial de datos para la sesión de calibración
TOMTOM_EMPIRICAL_SPEEDS = load_empirical_speeds(EMPIRICAL_JSON, TARGET_SCENARIO)

# ==============================================================================
# 3. MANIPULACIÓN DE ARCHIVOS XML Y EJECUCIÓN DE SUMO
# ==============================================================================
def update_vtype_xml(accel, decel, tau, sigma, min_gap, output_filename=VTYPE_FILE):
    """
    Inyecta los parámetros microscópicos Krauss en el XML de vehículos.
    """
    tree = ET.parse(VTYPE_FILE)
    root = tree.getroot()
    
    for vtype in root.findall('vType'):
        if vtype.attrib.get('id') == 'car_krauss':
            vtype.set('carFollowModel', 'Krauss')
            vtype.set('accel', f"{accel:.2f}")
            vtype.set('decel', f"{decel:.2f}")
            vtype.set('tau', f"{tau:.2f}")
            vtype.set('sigma', f"{sigma:.2f}")
            vtype.set('minGap', f"{min_gap:.2f}")
            
    tree.write(output_filename, encoding='utf-8', xml_declaration=True)

def parse_sumo_output():
    """
    Extrae la velocidad promedio por tramo (m/s) reportada por SUMO.
    """
    simulated_speeds = {}
    if not os.path.exists(OUTPUT_DUMP):
        return simulated_speeds

    tree = ET.parse(OUTPUT_DUMP)
    root = tree.getroot()

    for interval in root.findall('interval'):
        for edge in interval.findall('edge'):
            edge_id = edge.attrib.get('id')
            if edge_id in TOMTOM_EMPIRICAL_SPEEDS:
                speed = float(edge.attrib.get('speed', 0.0))
                simulated_speeds[edge_id] = speed

    return simulated_speeds

def run_sumo_simulation():
    """
    Ejecuta SUMO en modo CLI Headless.
    """
    cmd = [
        SUMO_BINARY,
        "-c", SUMO_CONFIG,
        "--route-files", ROUTE_FILE,
        "--edgedata-output", OUTPUT_DUMP,
        "--no-warnings", "true",
        "--duration-log.disable", "true"
    ]
    os.makedirs(RAW_DATA_DIR, exist_ok=True)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_DIR)
    if result.returncode != 0:
        raise RuntimeError(f"Error en SUMO: {result.stderr}")

# ==============================================================================
# 4. FUNCIÓN OBJETIVO Y EVALUACIÓN DE ERROR (GEH + RMSE)
# ==============================================================================
def calculate_geh(empirical, simulated):
    """
    Cálculo de la estadística GEH.
    """
    if empirical + simulated == 0:
        return 0
    return np.sqrt((2 * (simulated - empirical)**2) / (empirical + simulated))

def objective(trial):
    # Rango de búsqueda de parámetros microscópicos Krauss
    accel = trial.suggest_float("accel", 1.0, 3.5)
    decel = trial.suggest_float("decel", 1.5, 4.5)
    tau = trial.suggest_float("tau", 0.5, 2.0)
    sigma = trial.suggest_float("sigma", 0.0, 1.0)
    min_gap = trial.suggest_float("minGap", 1.0, 3.5)

    # 1. Modificar XML
    update_vtype_xml(accel, decel, tau, sigma, min_gap)

    # 2. Ejecutar simulación
    try:
        run_sumo_simulation()
    except Exception:
        return float('inf')

    # 3. Leer velocidades simuladas
    sim_speeds = parse_sumo_output()
    if not sim_speeds:
        return float('inf')

    # 4. Comparar contra datos empíricos de TomTom
    errors = []
    geh_list = []
    for edge_id, emp_speed in TOMTOM_EMPIRICAL_SPEEDS.items():
        sim_speed = sim_speeds.get(edge_id, 0.0)
        errors.append((emp_speed - sim_speed)**2)
        geh_list.append(calculate_geh(emp_speed, sim_speed))

    rmse = np.sqrt(np.mean(errors))
    mean_geh = np.mean(geh_list)

    trial.set_user_attr("rmse", rmse)
    trial.set_user_attr("mean_geh", mean_geh)

    return rmse

# ==============================================================================
# 5. EJECUCIÓN DE OPTIMIZACIÓN Y EXPORTACIÓN
# ==============================================================================
if __name__ == "__main__":
    print(f"\n=== Calibrando Escenario: [{TARGET_SCENARIO.upper()}] ===")
    
    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=N_TRIALS, n_jobs=1)

    # Obtener el trial con el menor RMSE
    best_trial = study.best_trial
    best_rmse = best_trial.value
    best_geh = best_trial.user_attrs.get("mean_geh", 0.0)

    print("\n================ RESULTADOS DE CALIBRACIÓN ================")
    print(f"Escenario: {TARGET_SCENARIO}")
    print(f"Mejor RMSE: {best_rmse:.4f} m/s")
    print(f"GEH Promedio: {best_geh:.4f} (Objetivo: < 5.0)")
    print("Parámetros Microscópicos Óptimos:")
    for param, val in study.best_params.items():
        print(f"  - {param}: {val:.4f}")

    # Guardar un archivo XML específico para este escenario HCM
    calibrated_vtype_out = os.path.join(CONF_DIR, f"vehicle_types_{TARGET_SCENARIO}.xml")
    bp = study.best_params
    update_vtype_xml(bp['accel'], bp['decel'], bp['tau'], bp['sigma'], bp['minGap'], calibrated_vtype_out)
    
    print(f"\n[OK] Archivo de calibración guardado como: '{calibrated_vtype_out}'")