import json
import os
import platform
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    from .protocols import CRYPTO_TIMINGS_PATH, load_protocols
except ImportError:
    from protocols import CRYPTO_TIMINGS_PATH, load_protocols


BASE_DIR = Path(__file__).resolve().parent.parent
LATENCY_DIR = BASE_DIR / "src" / "latency"
BENCHMARK_DIR = BASE_DIR / "src" / "crypto_benchmark"
BENCHMARK_BINARY = BENCHMARK_DIR / "out" / "ff1_benchmark"
CRYPTO_SOURCES = ["Aes128.cc", "Lea128.cc", "Speck128.cc", "Ff1.cc", "BlockCipher.cc", "CamFieldCipher.cc"]
ITERATIONS = 10000
# Bytes covered by the ECDSA signature: CAM plus security header fields (SHA-256 cost is negligible next to ECDSA).
SIGNED_BYTES = 200


def openssl_flags():
    """Compiler flags for OpenSSL 3 (libcrypto): pkg-config, then OPENSSL_DIR, then the system default."""
    if shutil.which("pkg-config"):
        result = subprocess.run(["pkg-config", "--cflags", "--libs", "libcrypto"], capture_output=True, text=True)
        if result.returncode == 0:
            return shlex.split(result.stdout)
    openssl_dir = os.environ.get("OPENSSL_DIR")
    if openssl_dir:
        return [f"-I{openssl_dir}/include", f"-L{openssl_dir}/lib", "-lcrypto"]
    return ["-lcrypto"]


def build_benchmark():
    BENCHMARK_BINARY.parent.mkdir(parents=True, exist_ok=True)
    compiler = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        raise FileNotFoundError("No C++ compiler found; set CXX")
    cmd = [
        compiler, "-std=c++17", "-O2",
        "-I", str(LATENCY_DIR),
        str(BENCHMARK_DIR / "ff1_benchmark.cc"),
        *(str(LATENCY_DIR / source) for source in CRYPTO_SOURCES),
        *openssl_flags(),
        "-o", str(BENCHMARK_BINARY),
    ]
    subprocess.run(cmd, check=True)


def run_benchmark():
    # The binary validates AES/LEA/SPECK, NIST FF1 and CAM field cipher test vectors before timing.
    result = subprocess.run(
        [str(BENCHMARK_BINARY), str(ITERATIONS), str(SIGNED_BYTES)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Crypto benchmark failed:\n{result.stderr}")
    return json.loads(result.stdout)


def field_cipher_cost(stats, operation):
    """Mean per-CAM FF1 cost: median time per FF1 call x mean FF1 calls per CAM (cycle-walking)."""
    return stats[f"{operation}_call"]["median_us"] * stats[f"{operation}_calls_per_cam"]


def main():
    print("[BUILD] Compilando benchmark criptográfico...")
    build_benchmark()
    print(f"[RUN] {ITERATIONS} operações por algoritmo...")
    measured = run_benchmark()

    machine = platform.machine()
    if platform.system() == "Darwin" or machine in {"x86_64", "AMD64"}:
        print(f"[AVISO] Medindo em {platform.system()}/{machine}: os tempos não representam uma OBU. "
              "Execute este script na placa embarcada e copie conf/crypto_timings.json.")

    ecdsa = measured["ecdsa_p256"]
    sign_us = ecdsa["sign"]["median_us"]
    verify_us = ecdsa["verify"]["median_us"]
    timings = {
        "metadata": {
            "host": platform.node(),
            "system": platform.system(),
            "machine": machine,
            "processor": platform.processor(),
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "iterations": measured["iterations"],
            "ecdsa_signed_bytes": ecdsa["signed_bytes"],
            "statistic": "median per operation; FF1 = median per call x mean calls per CAM",
            "composition": "tx = ECDSA sign + FF1 encrypt; rx = ECDSA verify + FF1 decrypt",
        },
        "ecdsa_p256": ecdsa,
    }
    for name, profile in load_protocols().items():
        cipher = profile["cipher"]
        ff1_encrypt_us = ff1_decrypt_us = 0.0
        entry = {}
        if cipher is not None:
            stats = measured["field_cipher"][cipher]
            ff1_encrypt_us = field_cipher_cost(stats, "encrypt")
            ff1_decrypt_us = field_cipher_cost(stats, "decrypt")
            entry["ff1_stats"] = stats
        timings[name] = {
            "tx_delay_us": sign_us + ff1_encrypt_us,
            "rx_delay_us": verify_us + ff1_decrypt_us,
            "ecdsa_sign_us": sign_us,
            "ecdsa_verify_us": verify_us,
            "ff1_encrypt_us": ff1_encrypt_us,
            "ff1_decrypt_us": ff1_decrypt_us,
            **entry,
        }
        print(f"[OK] {name}: tx={timings[name]['tx_delay_us']:.3f} us (FF1 {ff1_encrypt_us:.3f}) | "
              f"rx={timings[name]['rx_delay_us']:.3f} us (FF1 {ff1_decrypt_us:.3f})")

    CRYPTO_TIMINGS_PATH.write_text(json.dumps(timings, indent=2), encoding="utf-8")
    print(f"[OK] Tempos salvos em {CRYPTO_TIMINGS_PATH}")


if __name__ == "__main__":
    main()
