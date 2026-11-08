# Stage 4: Experimental Results & Comparative Analysis

Analysis of the full simulation campaign (560 runs) evaluating the performance impact of **FF1 Format-Preserving Encryption** (AES-128, LEA-128, SPECK-128) against the **baseline** (unencrypted CAM) across 14 calibrated traffic scenarios.

---

## 1. Simulation Verification & Integrity

All 56 configurations (14 traffic scenarios $\times$ 4 protocols) completed with 10 pseudo-random seeds:
- `ff1DecryptFailures = 0` across all runs (100% cryptographic round-trip fidelity).
- CAM packet lengths are invariant across protocols ($\pm 0.05\text{ B}$ variance due to pseudonym certificates), verifying 0-byte format-preserving encryption overhead.
- No simulation crashes or memory faults were encountered.

---

## 2. Traffic Regimes & Physical Channel Load

| Metric | Highway (LOS A $\to$ F) | Urban Road (LOS C $\to$ F) | Intersection (LOS A $\to$ F) |
| :--- | :--- | :--- | :--- |
| **Space-Mean Speed** | 11.2 $\to$ 9.9 m/s (40 $\to$ 36 km/h) | 7.3 $\to$ 6.1 m/s | 6.4 $\to$ 4.5 m/s |
| **Active Concurrent Vehicles** | 308 $\to$ 2,008 | 281 $\to$ 746 | 104 $\to$ 485 |
| **Channel Busy Ratio (CBR)** | 2.9% $\to$ 20.4% | 4.7% $\to$ 13.1% | 1.4% $\to$ 6.9% |

- CBR remains below the 30% decentralized congestion control (DCC) threshold across all topologies, indicating that packet drops are governed by distance attenuation and interference rather than catastrophic channel saturation.

---

## 3. Packet Delivery Ratio (PDR)

| Topology | Baseline PDR (LOS A $\to$ F) | Loss Mechanism |
| :--- | :--- | :--- |
| **Highway** | 98.3% $\to$ 85.5% | Dominated by SNIR degradation and hidden-terminal collisions at $>150\text{ m}$. |
| **Urban Road** | 97.9% $\to$ 91.9% | Interrupted building shadowing and dense signal queues. |
| **Intersection**| 99.5% $\to$ 96.4% | Highly localized transmission with minimal obstruction. |

### Distance-Binned PDR on Highway LOS F
- $0 - 50\text{ m}$: $96.6\%$
- $100 - 150\text{ m}$: $86.9\%$
- $250 - 300\text{ m}$: $76.0\%$

---

## 4. Cryptographic Latency Breakdown

| Protocol | TX Delay (Sign + Encrypt) | RX Delay (Verify + Decrypt) | Total Overhead vs. Baseline |
| :--- | :--- | :--- | :--- |
| **Baseline (ECDSA only)** | 234 µs | 621 µs | Reference (0 µs) |
| **FF1_AES** | 335 µs | 722 µs | +202 µs (+23.6%) |
| **FF1_LEA** | 321 µs | 708 µs | +174 µs (+20.3%) |
| **FF1_SPECK** | 319 µs | 707 µs | +171 µs (+20.0%) |

### Key Conclusions:
1. **Computational Cost Ordering:** $\text{AES} > \text{LEA} \approx \text{SPECK}$. Lightweight block ciphers (LEA and SPECK) achieve a ~15% reduction in crypto processing delay compared to AES-128.
2. **End-to-End Latency Impact:** Across all 560 simulations, end-to-end latency increases by approximately **0.17 to 0.24 ms** (+15% relative to the 1.45 ms baseline), remaining well below active safety deadlines (10 ms platooning / 100 ms general CAM).
3. **Equivalence Tests (TOST):** Two One-Sided Tests (TOST) with Holm adjustment confirm equivalence to baseline for CAM payload size, transmission rate, and CBR in 100% of tested cases.
