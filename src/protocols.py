import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
PROTOCOLS_PATH = BASE_DIR / "conf" / "protocols.json"
CRYPTO_TIMINGS_PATH = BASE_DIR / "conf" / "crypto_timings.json"
CAM_MODEL_PATH = BASE_DIR / "conf" / "cam_model.json"


def load_protocols(path=PROTOCOLS_PATH):
    """Protocol profiles keyed by name; key order is the plotting order (baseline first)."""
    with Path(path).open("r", encoding="utf-8") as source:
        return json.load(source)


def load_crypto_timings(path=CRYPTO_TIMINGS_PATH):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"FF1 timings not found: {path}; run benchmark_crypto.py first")
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def load_cam_model(path=CAM_MODEL_PATH):
    """Structural CAM size model (bytes and inclusion intervals) with its sources in 'notes'."""
    with Path(path).open("r", encoding="utf-8") as source:
        return json.load(source)
