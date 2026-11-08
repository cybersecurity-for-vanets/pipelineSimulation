#!/usr/bin/env bash
# Executa src/benchmark_crypto.py N vezes em série e agrega o resultado em conf/crypto_timings.json.
# Uso: src/run_crypto_benchmark.sh [N]   (padrão: 3)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNS="${1:-3}"
RUNS_DIR="$ROOT/config/crypto_runs"
TIMINGS="$ROOT/config/crypto_timings.json"

if ! [[ "$RUNS" =~ ^[1-9][0-9]*$ ]]; then
    echo "[ERRO] N deve ser um inteiro positivo (recebido: $RUNS)" >&2
    exit 1
fi

mkdir -p "$RUNS_DIR"
rm -f "$RUNS_DIR"/run_*.json

# Registra as condições da medição (vai para o metadata do JSON final).
{
    echo "uname=$(uname -srm)"
    echo "bits=$(getconf LONG_BIT)"
    echo "openssl=$(openssl version 2>/dev/null || echo desconhecido)"
    echo "model=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo desconhecido)"
    echo "governor=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo desconhecido)"
    echo "cpu_mhz=$(( $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || echo 0) / 1000 ))"
    echo "runs=$RUNS"
} > "$RUNS_DIR/environment.txt"

if command -v vcgencmd >/dev/null 2>&1; then
    echo "[INFO] get_throttled antes: $(vcgencmd get_throttled) | $(vcgencmd measure_temp)"
fi

for i in $(seq 1 "$RUNS"); do
    echo "[RUN] Execução $i/$RUNS"
    python3 -m src.crypto.benchmark
    cp "$TIMINGS" "$RUNS_DIR/run_$(printf '%02d' "$i").json"
done

if command -v vcgencmd >/dev/null 2>&1; then
    throttled="$(vcgencmd get_throttled)"
    echo "[INFO] get_throttled depois: $throttled | $(vcgencmd measure_temp)"
    echo "throttled_after=${throttled#*=}" >> "$RUNS_DIR/environment.txt"
    if [[ "${throttled#*=}" != "0x0" ]]; then
        echo "[AVISO] Throttling/subtensão detectado durante as medições; os tempos podem estar inflados." >&2
    fi
fi

python3 -m src.crypto.aggregate "$RUNS_DIR"
echo "[OK] Próximo passo: python -m src.simulation.generate_configs"
