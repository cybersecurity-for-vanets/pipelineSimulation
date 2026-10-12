import os
import json
import math
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS_CONF_PATH = os.path.join(BASE_DIR, "conf", "coordinates_scenaries.json")
TOMTOM_CONF_PATH = os.path.join(BASE_DIR, "conf", "tomtom.json")
TRAFFIC_DIR = os.path.join(BASE_DIR, "trafficXmlFiles")
INI_OUTPUT_PATH = os.path.join(BASE_DIR, "conf", "omnetpp.ini")

PROTOCOLS = {
    "ASCON_128": 928,          # +16B payload overhead
    "Chaskey": 928,            # +16B payload overhead
    # "Proposed_V2X": 896,       # +12B payload overhead
    "Ed25519": 1312            # +64B payload overhead
}

PLAYGROUND_MARGIN_M = 500

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

    ini_content = """# ==============================================================================
# CONFIGURAÇÃO AUTOMÁTICA DO OMNET++ / VEINS 5.3.1 (ETAPA 2)
# ==============================================================================

[General]
cmdenv-express-mode = true
cmdenv-autoflush = true
cmdenv-status-frequency = 10s
sim-time-limit = 600s
repeat = 30

network = RSUExampleScenario
*.playgroundSizeX = 3000m
*.playgroundSizeY = 3000m
*.playgroundSizeZ = 50m

*.annotations.draw = false
*.obstacles.debug = false

# Administrador TraCI / SUMO
*.manager.updateInterval = 1s
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
*.rsu[*].appl.dataLength = 800bit
*.rsu[*].appl.sendBeacons = true
*.rsu[*].appl.dataOnSch = false
*.rsu[*].appl.beaconInterval = 1s
*.rsu[*].appl.beaconUserPriority = 7
*.rsu[*].appl.dataUserPriority = 5

# Veículos e Mobilidade
*.node[*].applType = "pipelineSimulation.LatencyTraCIDemo11p"
*.node[*].appl.headerLength = 256bit
*.node[*].appl.sendBeacons = true
*.node[*].appl.dataOnSch = false
*.node[*].appl.beaconInterval = 1s
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

        for proto_name, payload_bits in PROTOCOLS.items():
            for flow in flows:
                count += 1
                config_name = f"Config_{top}_{proto_name}_{flow}"
                launchd_filename = launchd_files[(top, flow)]
                
                ini_content += f"""
# ------------------------------------------------------------------------------
# [{count}/36] Topologia: {top} | Protocolo: {proto_name} | Fluxo HCM: {flow}
# ------------------------------------------------------------------------------
[Config {config_name}]
description = "{top} - {proto_name} - {flow}"
*.manager.launchConfig = xmldoc("{launchd_filename}")
*.playgroundSizeX = {playground_x}m
*.playgroundSizeY = {playground_y}m
*.rsu[0].mobility.x = {rsu_x:.2f}
*.rsu[0].mobility.y = {rsu_y:.2f}
*.node[*].appl.dataLength = {payload_bits}bit
"""

    with open(INI_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(ini_content)

    print(f"[OK] {INI_OUTPUT_PATH} gerado com sucesso contendo {count} configurações.")

if __name__ == "__main__":
    generate_omnetpp_ini()