import os
import json
import math
import xml.etree.ElementTree as ET

try:
    from .protocols import load_cam_model, load_crypto_timings, load_protocols
    from .generate_routes import SUMO_STEP_LENGTH
except ImportError:
    from protocols import load_cam_model, load_crypto_timings, load_protocols
    from generate_routes import SUMO_STEP_LENGTH

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS_CONF_PATH = os.path.join(BASE_DIR, "conf", "coordinates_scenaries.json")
TOMTOM_CONF_PATH = os.path.join(BASE_DIR, "conf", "tomtom.json")
TRAFFIC_DIR = os.path.join(BASE_DIR, "trafficXmlFiles")
INI_OUTPUT_PATH = os.path.join(BASE_DIR, "conf", "omnetpp.ini")

PLAYGROUND_MARGIN_M = 500

# Vetores por mensagem para analyze_message_metrics.py: grava 1 a cada MESSAGE_VECTOR_STRIDE amostras por vetor.
RECORD_MESSAGE_VECTORS = True
MESSAGE_VECTOR_STRIDE = 100

# Tempo por execução: aquecimento (estado transitório) + janela de medição (regime permanente).
# A demanda das rotas é constante (vehsPerHour), então 3600 s não agregam informação.
WARMUP_S = 300
MEASUREMENT_S = 200
REPEATS = 10

def load_protocol_profiles():
    """Junta conf/protocols.json com os atrasos medidos em conf/crypto_timings.json (ECDSA + FF1)."""
    timings = load_crypto_timings()
    machine = timings.get("metadata", {})
    if machine.get("system") == "Darwin":
        print(f"[AVISO] crypto_timings.json foi medido em {machine.get('host')} ({machine.get('machine')}), não numa OBU.")
    profiles = {}
    for name, protocol in load_protocols().items():
        if name not in timings:
            raise KeyError(f"No timing for '{name}' in crypto_timings.json; rerun benchmark_crypto.py")
        profiles[name] = {
            "cipher": protocol["cipher"] or "none",
            "overhead_bytes": protocol["overhead_bytes"],
            "tx_delay_us": timings[name]["tx_delay_us"],
            "rx_delay_us": timings[name]["rx_delay_us"],
        }
    return profiles

def get_topology_bounds(topology):
    net_path = os.path.join(BASE_DIR, "osmFiles", f"{topology}.net.xml")
    if not os.path.isfile(net_path):
        raise FileNotFoundError(f"SUMO network not found: {net_path}")

    location = ET.parse(net_path).getroot().find("location")
    if location is None or not location.get("convBoundary"):
        raise ValueError(f"SUMO network has no converted boundary: {net_path}")

    min_x, min_y, max_x, max_y = map(float, location.get("convBoundary").split(","))
    return min_x, min_y, max_x, max_y

def generate_omnetpp_ini():
    with open(SCENARIOS_CONF_PATH, "r", encoding="utf-8") as f:
        scenarios = list(json.load(f).keys())
    with open(TOMTOM_CONF_PATH, "r", encoding="utf-8") as f:
        flows = list(json.load(f)["hcm_flow_rates_veh_per_hour_per_lane"].keys())
    protocols = load_protocol_profiles()
    cam_model = load_cam_model()
    total_configs = len(scenarios) * len(protocols) * len(flows)

    launchd_files = {}
    for top in scenarios:
        for flow in flows:
            sumocfg_filename = f"{top}_{flow}.sumocfg"
            sumocfg_path = os.path.join(TRAFFIC_DIR, sumocfg_filename)
            if not os.path.isfile(sumocfg_path):
                raise FileNotFoundError(f"SUMO config not found: {sumocfg_path}")

            launchd_filename = f"scenario_{top}_{flow}.launchd.xml"
            launchd_path = os.path.join(BASE_DIR, "conf", launchd_filename)
            launchd_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<launch>
    <basedir path="{TRAFFIC_DIR}" />
    <copy file="{sumocfg_filename}" type="config" />
</launch>
'''
            with open(launchd_path, "w", encoding="utf-8") as f:
                f.write(launchd_content)
            launchd_files[(top, flow)] = launchd_filename

    default_launchd = os.path.join(BASE_DIR, "conf", "scenario.launchd.xml")
    with open(default_launchd, "w", encoding="utf-8") as f:
        f.write(launchd_content)

    ini_content = f"""# ==============================================================================
# CONFIGURAÇÃO AUTOMÁTICA DO OMNET++ / VEINS 5.3.1 (ETAPA 2)
# ==============================================================================

[General]
cmdenv-express-mode = true
cmdenv-autoflush = true
cmdenv-status-frequency = 10s
sim-time-limit = {WARMUP_S + MEASUREMENT_S}s
repeat = {REPEATS}
seed-set = ${{repetition}}

network = RSUExampleScenario
*.playgroundSizeX = 3000m
*.playgroundSizeY = 3000m
*.playgroundSizeZ = 50m

*.annotations.draw = false
*.obstacles.debug = false

# Administrador TraCI / SUMO
*.manager.updateInterval = {SUMO_STEP_LENGTH}s
*.manager.host = "localhost"
*.manager.port = 9999
*.manager.autoShutdown = true
*.manager.launchConfig = xmldoc("scenario.launchd.xml")
*.manager.moduleType = "org.car2x.veins.nodes.Car"
*.manager.moduleName = "node"

# ConnectionManager
*.connectionManager.maxInterfDist = 300m
*.connectionManager.sendDirect = false
*.connectionManager.drawMaxIntfDist = false

# RSU (Roadside Unit)
*.rsu[0].mobility.x = 1500
*.rsu[0].mobility.y = 1500
*.rsu[0].mobility.z = 3
*.rsu[*].applType = "TraCIDemoRSU11p"
*.rsu[*].appl.headerLength = 256bit
*.rsu[*].appl.dataLengthBits = 800bit
*.rsu[*].appl.sendBeacons = true
*.rsu[*].appl.dataOnSch = false
*.rsu[*].appl.beaconInterval = 1s
*.rsu[*].appl.beaconUserPriority = 7
*.rsu[*].appl.dataUserPriority = 5

# Veículos e Mobilidade
*.node[*].applType = "pipelineSimulation.LatencyTraCIDemo11p"
*.node[*].appl.headerLength = {cam_model['gn_btp_header_bytes'] * 8}bit
*.node[*].appl.dataLengthBits = 800bit
*.node[*].appl.sendBeacons = false
*.node[*].appl.dataOnSch = false
*.node[*].appl.beaconInterval = 1s
*.node[*].appl.camMinInterval = 0.1s
*.node[*].appl.camMaxInterval = 1s
*.node[*].appl.cbrWindow = 1s
*.node[*].appl.camPositionThreshold = 4m
*.node[*].appl.camHeadingThresholdDeg = 4
*.node[*].appl.camSpeedThreshold = 0.5mps
*.node[*].appl.cbrLowThreshold = 0.3
*.node[*].appl.cbrHighThreshold = 0.6
*.node[*].appl.cryptoCipher = "none"
# Modelo estrutural de tamanho do CAM assinado (conf/cam_model.json)
*.node[*].appl.camBaseBytes = {cam_model['cam_base_bytes']}
*.node[*].appl.camLowFrequencyExtraBytes = {cam_model['low_frequency_extra_bytes']}
*.node[*].appl.camCertificateExtraBytes = {cam_model['certificate_extra_bytes']}
*.node[*].appl.camLowFrequencyInterval = {cam_model['low_frequency_interval_s']}s
*.node[*].appl.camCertificateInterval = {cam_model['certificate_interval_s']}s
*.node[*].appl.cryptoOverheadBits = 0bit
*.node[*].appl.cryptoTxDelay = 0us
*.node[*].appl.cryptoRxDelay = 0us
# Estatísticas (escalares e vetores por mensagem) começam após o aquecimento
*.node[*].appl.warmupTime = {WARMUP_S}s
*.node[*].appl.recordMessageVectors = {str(RECORD_MESSAGE_VECTORS).lower()}
*.node[*].appl.messageVectorStride = {MESSAGE_VECTOR_STRIDE}
*.node[*].appl.beaconUserPriority = 7
*.node[*].appl.dataUserPriority = 5

*.node[*].veinsmobility.x = 0
*.node[*].veinsmobility.y = 0
*.node[*].veinsmobility.z = 0
*.node[*].veinsmobility.setHostSpeed = false

# Camada MAC IEEE 1609.4 / 802.11p
**.mac1609_4.useServiceChannel = false
**.mac1609_4.txPower = 20mW
**.mac1609_4.bitrate = 6Mbps

# Camada Física IEEE 802.11p
**.phy80211p.analogueModels = xmldoc("config.xml")
**.phy80211p.decider = xmldoc("config.xml")
**.phy80211p.sensitivity = -89dBm
**.phy80211p.maxTXPower = 20mW
**.phy80211p.minPowerLevel = -110dBm
**.phy80211p.useThermalNoise = true
**.phy80211p.thermalNoise = -110dBm
**.phy80211p.useNoiseFloor = true
**.phy80211p.noiseFloor = -98dBm
**.phy80211p.usePropagationDelay = true
**.phy80211p.antenna = xmldoc("antenna.xml", "/root/Antenna[@id='monopole']")
**.phy80211p.antennaPosition = "0 0 0"
**.phy80211p.collectCollisionStatistics = false
**.phy80211p.allowDirectAntennaDirection = false
"""

    count = 0
    for top in scenarios:
        min_x, min_y, max_x, max_y = get_topology_bounds(top)
        playground_x = math.ceil(max_x + PLAYGROUND_MARGIN_M)
        playground_y = math.ceil(max_y + PLAYGROUND_MARGIN_M)
        rsu_x = (min_x + max_x) / 2
        rsu_y = (min_y + max_y) / 2

        for proto_name, crypto_profile in protocols.items():
            for flow in flows:
                count += 1
                config_name = f"Config_{top}_{proto_name}_{flow}"
                launchd_filename = launchd_files[(top, flow)]
                overhead_bits = crypto_profile["overhead_bytes"] * 8
                
                ini_content += f"""
# ------------------------------------------------------------------------------
# [{count}/{total_configs}] Topologia: {top} | Protocolo: {proto_name} | Fluxo HCM: {flow}
# ------------------------------------------------------------------------------
[Config {config_name}]
description = "{top} - {proto_name} - {flow}"
*.manager.launchConfig = xmldoc("{launchd_filename}")
*.playgroundSizeX = {playground_x}m
*.playgroundSizeY = {playground_y}m
*.rsu[0].mobility.x = {rsu_x:.2f}
*.rsu[0].mobility.y = {rsu_y:.2f}
*.node[*].appl.cryptoCipher = "{crypto_profile['cipher']}"
*.node[*].appl.cryptoOverheadBits = {overhead_bits}bit
*.node[*].appl.cryptoTxDelay = {crypto_profile['tx_delay_us']:.3f}us
*.node[*].appl.cryptoRxDelay = {crypto_profile['rx_delay_us']:.3f}us
"""

    with open(INI_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(ini_content)

    print(f"[OK] {INI_OUTPUT_PATH} gerado com sucesso contendo {count} configurações.")

if __name__ == "__main__":
    generate_omnetpp_ini()