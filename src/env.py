"""Environment configuration and path resolution for the VANET simulation pipeline."""

import os
from pathlib import Path

# Project root directory
ROOT_DIR = Path(__file__).resolve().parent.parent

# Core subdirectories
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
SIM_DIR = ROOT_DIR / "sim"
OSM_DIR = ROOT_DIR / "osmFiles"
TRAFFIC_DIR = ROOT_DIR / "trafficXmlFiles"

# Simulation data storage directory (supports external drive override via SIM_DATA_DIR)
# Defaults to project root if external SSD is not mounted.
DEFAULT_EXTERNAL_DIR = Path("/Volumes/PSSD T7")
ENV_DATA_DIR = os.environ.get("SIM_DATA_DIR")
if ENV_DATA_DIR:
    DATA_STORAGE_DIR = Path(ENV_DATA_DIR)
elif DEFAULT_EXTERNAL_DIR.is_dir():
    DATA_STORAGE_DIR = DEFAULT_EXTERNAL_DIR
else:
    DATA_STORAGE_DIR = ROOT_DIR

RESULTS_DIR = DATA_STORAGE_DIR / "results"
RAW_DATA_DIR = RESULTS_DIR / "rawData"
PLOTS_DIR = RESULTS_DIR / "plots"
LOGS_DIR = DATA_STORAGE_DIR / "logs"

# External Tool Paths (OMNeT++, Veins, SUMO)
# Configurable via environment variables with standard fallbacks.
OMNETPP_HOME = Path(os.environ.get("OMNETPP_HOME", "/Users/fabiotorres/omnetpp-6.2.0"))
VEINS_HOME = Path(os.environ.get("VEINS_HOME", "/Users/fabiotorres/veins-veins-5.3.1"))
SUMO_HOME = Path(os.environ.get("SUMO_HOME", "/usr/local/share/sumo"))

OPP_RUN = Path(os.environ.get("OPP_RUN", OMNETPP_HOME / "bin" / "opp_run"))
OPP_MAKEMAKE = Path(os.environ.get("OPP_MAKEMAKE", OMNETPP_HOME / "bin" / "opp_makemake"))
OPP_SCAVETOOL = Path(os.environ.get("OPP_SCAVETOOL", OMNETPP_HOME / "bin" / "opp_scavetool"))
VEINS_LAUNCHD = Path(os.environ.get("VEINS_LAUNCHD", VEINS_HOME / "bin" / "veins_launchd"))
VEINS_SRC = Path(os.environ.get("VEINS_SRC", VEINS_HOME / "src"))
VEINS_LIB = Path(os.environ.get("VEINS_LIB", VEINS_SRC / "veins"))

SUMO_BINARY = os.environ.get("SUMO_BINARY", "sumo")
NETCONVERT_BINARY = os.environ.get("NETCONVERT_BINARY", "netconvert")

# Configure environment variables for OMNeT++ / Veins runtime execution
OMNETPP_BIN = OMNETPP_HOME / "bin"
OMNETPP_CONFIGFILE = OMNETPP_HOME / "Makefile.inc"

path_parts = [str(OMNETPP_BIN)]
tools_dir = OMNETPP_HOME / "tools" / "macos.aarch64" / "bin"
if tools_dir.is_dir():
    path_parts.append(str(tools_dir))

current_path = os.environ.get("PATH", "")
for p in reversed(path_parts):
    if p not in current_path:
        current_path = f"{p}:{current_path}"
os.environ["PATH"] = current_path

if OMNETPP_CONFIGFILE.is_file():
    os.environ["OMNETPP_CONFIGFILE"] = str(OMNETPP_CONFIGFILE)
    os.environ["__omnetpp_root_dir"] = str(OMNETPP_HOME)

# Update dynamic library search path for OMNeT++ and Veins libraries
dyld_paths = [str(OMNETPP_HOME / "lib"), str(VEINS_LIB), str(VEINS_SRC)]
current_dyld = os.environ.get("DYLD_LIBRARY_PATH", "")
for p in reversed(dyld_paths):
    if p not in current_dyld:
        current_dyld = f"{p}:{current_dyld}" if current_dyld else p
os.environ["DYLD_LIBRARY_PATH"] = current_dyld

# Ensure required runtime output directories exist
for path in (RESULTS_DIR, RAW_DATA_DIR, PLOTS_DIR, LOGS_DIR, OSM_DIR, TRAFFIC_DIR, CONFIG_DIR):
    path.mkdir(parents=True, exist_ok=True)
