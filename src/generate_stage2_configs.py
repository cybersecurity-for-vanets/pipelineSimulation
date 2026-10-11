import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS_CONF_PATH = os.path.join(BASE_DIR, "conf", "coordinates_scenaries.json")
TOMTOM_CONF_PATH = os.path.join(BASE_DIR, "conf", "tomtom.json")
INI_OUTPUT_PATH = os.path.join(BASE_DIR, "conf", "omnetpp.ini")

PROTOCOLS = {
    "ASCON_128": 928,          # +16B payload overhead
    "Chaskey": 928,            # +16B payload overhead
    "Proposed_V2X": 896,       # +12B payload overhead
    "Ed25519": 1312            # +64B payload overhead
}

def generate_omnetpp_ini():
    with open(SCENARIOS_CONF_PATH, "r", encoding="utf-8") as f:
        scenarios = list(json.load(f).keys())
    with open(TOMTOM_CONF_PATH, "r", encoding="utf-8") as f:
        flows = list(json.load(f)["hcm_flow_rates_veh_per_hour_per_lane"].keys())

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

# ------------------------------------------------------------------------------
# Administrador TraCI / SUMO
# ------------------------------------------------------------------------------
*.manager.updateInterval = 1s
*.manager.host = "localhost"
*.manager.port = 9999
*.manager.autoShutdown = true
*.manager.launchConfig = xmldoc("scenario.launchd.xml")
*.manager.moduleType = "org.car2x.veins.nodes.Car"
*.manager.moduleName = "node"

# ------------------------------------------------------------------------------
# ConnectionManager (Canal de Radio V2V)
# ------------------------------------------------------------------------------
*.connectionManager.maxInterfDist = 300m
*.connectionManager.sendDirect = false
*.connectionManager.drawMaxIntfDist = false

# ------------------------------------------------------------------------------
# RSU (Roadside Unit)
# ------------------------------------------------------------------------------
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

# ------------------------------------------------------------------------------
# Veículos e Mobilidade
# ------------------------------------------------------------------------------
*.node[*].applType = "TraCIDemo11p"
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

# ------------------------------------------------------------------------------
# Camada MAC IEEE 1609.4 / 802.11p
# ------------------------------------------------------------------------------
**.mac1609_4.useServiceChannel = false
**.mac1609_4.txPower = 20mW
**.mac1609_4.bitrate = 6Mbps

# ------------------------------------------------------------------------------
# Camada Física IEEE 802.11p (Nativos Completos Veins 5.3.1)
# ------------------------------------------------------------------------------
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
        for proto_name, payload_bits in PROTOCOLS.items():
            for flow in flows:
                count += 1
                config_name = f"Config_{top}_{proto_name}_{flow}"
                sumocfg_rel_path = f"../trafficXmlFiles/{top}_{flow}.sumocfg"
                
                ini_content += f"""
# ------------------------------------------------------------------------------
# [{count}/36] Topologia: {top} | Protocolo: {proto_name} | Fluxo HCM: {flow}
# ------------------------------------------------------------------------------
[Config {config_name}]
description = "{top} - {proto_name} - {flow}"
*.manager.configFile = "{sumocfg_rel_path}"
*.node[*].appl.dataLength = {payload_bits}bit
"""

    with open(INI_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(ini_content)

    print(f"[OK] {INI_OUTPUT_PATH} gerado com sucesso contendo {count} configurações.")

if __name__ == "__main__":
    generate_omnetpp_ini()