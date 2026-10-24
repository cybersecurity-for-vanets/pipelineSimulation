import json
import statistics
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = BASE_DIR / "conf" / "crypto_runs"
OUTPUT_PATH = BASE_DIR / "conf" / "crypto_timings.json"
FIELDS = ("tx_delay_us", "rx_delay_us")
NON_PROTOCOL_KEYS = {"metadata", "ecdsa_p256"}


def load_environment(runs_dir):
    """Lê o environment.txt (chave=valor) gravado por run_crypto_benchmark.sh, se existir."""
    path = runs_dir / "environment.txt"
    if not path.is_file():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)
    return {key.strip(): value.strip() for key, value in pairs}


def run_score(run, protocols):
    """Custo total TX+RX da execução em todos os protocolos; ordena as execuções por velocidade."""
    return sum(run[name][field] for name in protocols for field in FIELDS)


def spread_pct(values):
    return (max(values) - min(values)) / min(values) * 100 if min(values) > 0 else 0.0


def main():
    runs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else RUNS_DIR
    files = sorted(runs_dir.glob("run_*.json"))
    if not files:
        raise FileNotFoundError(f"Nenhum run_*.json em {runs_dir}")
    runs = [json.loads(file.read_text(encoding="utf-8")) for file in files]
    protocols = [key for key in runs[0] if key not in NON_PROTOCOL_KEYS]

    scores = [run_score(run, protocols) for run in runs]
    order = sorted(range(len(runs)), key=lambda index: scores[index])
    selected = order[len(order) // 2]  # execução mediana (a mais lenta das duas centrais se N for par)

    print(f"[INFO] {len(runs)} execuções em {runs_dir}")
    stats = {}
    for name in protocols:
        stats[name] = {}
        for field in FIELDS:
            values = [run[name][field] for run in runs]
            stats[name][field] = {
                "values_us": values,
                "median_us": statistics.median(values),
                "mean_us": statistics.fmean(values),
                "stdev_us": statistics.stdev(values) if len(values) > 1 else 0.0,
                "spread_pct": spread_pct(values),
            }
            print(f"[OK] {name:10} {field}: " + " / ".join(f"{v:.2f}" for v in values) +
                  f"  mediana={stats[name][field]['median_us']:.2f}  variação={stats[name][field]['spread_pct']:.2f}%")
    ecdsa_spread = {
        op: spread_pct([run["ecdsa_p256"][op]["median_us"] for run in runs]) for op in ("sign", "verify")
    }
    print(f"[OK] ECDSA variação: sign={ecdsa_spread['sign']:.2f}% verify={ecdsa_spread['verify']:.2f}%")

    result = json.loads(json.dumps(runs[selected]))
    result["metadata"].update({
        "aggregation": "execução mediana (por custo total TX+RX) entre as repetições",
        "runs": len(runs),
        "selected_run": files[selected].name,
        "source_runs": [file.name for file in files],
        "run_scores_us": scores,
        "ecdsa_spread_pct": ecdsa_spread,
        "environment": load_environment(runs_dir),
    })
    result["metadata"]["run_statistics"] = stats

    OUTPUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[OK] Execução selecionada: {files[selected].name}")
    print(f"[OK] Tempos salvos em {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
