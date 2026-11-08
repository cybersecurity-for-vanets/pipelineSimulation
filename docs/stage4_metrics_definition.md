# Stage 4: Formal Calculation of PDR, CBR, and E2E Latency

This document details the mathematical formulation and aggregation rules for the three core vehicular performance metrics recorded by the C++ simulation module (`LatencyTraCIDemo11p.cc`) and extracted in Stage 4:
- **Packet Delivery Ratio (PDR)** [%]
- **Channel Busy Ratio (CBR)** [%]
- **End-to-End Latency ($T_{\text{E2E}}$)** [ms]

---

## 1. Mathematical Formulation

### 1.1 Packet Delivery Ratio (PDR)
In broadcast ad-hoc networks, packet delivery cannot be calculated per-node because each transmitted packet is expected by multiple surrounding vehicles within radio range.

For each CAM $k$ transmitted by vehicle $i$ at time $t_k$, let $\mathcal{N}_i(t_k)$ be the set of active surrounding vehicles within the maximum communication interference distance $R_c = 300\text{ m}$:

$$\mathcal{N}_i(t_k) = \{ j \neq i \mid \|\mathbf{p}_i(t_k) - \mathbf{p}_j(t_k)\|_2 \le R_c \}$$

The total expected receptions across a simulation run is:
$$E = \sum_{i} \sum_{k \in \text{CAMs}(i)} |\mathcal{N}_i(t_k)|$$

Let $R$ denote the total count of successfully received, verified, and decoded CAMs by intended recipients:
$$R = \sum_{j} \sum_{k} \mathbb{1}[\text{CAM } k \text{ received by } j \text{ and } j \in \mathcal{N}_i(t_k)]$$

The run-level Packet Delivery Ratio is defined as:
$$\text{PDR}_{\text{run}} = \frac{R}{E} \times 100\%$$

Since every counted reception corresponds to an expected receiver tagged at broadcast time, $R \le E$ is guaranteed, preventing mathematical overflows ($PDR \le 100\%$).

### 1.2 Channel Busy Ratio (CBR)
The physical radio channel state is tracked via IEEE 802.11p clear channel assessment (CCA). In `LatencyTraCIDemo11p.cc`, each node records the fraction of time the physical medium is sensed busy (energy $> -89\text{ dBm}$):

$$\text{CBR}_i = \frac{1}{T_{\text{meas}}} \int_{t_{\text{start}}}^{t_{\text{end}}} \mathbb{1}[\text{Medium is Busy}(t)] \, dt$$

The simulation-wide CBR represents the spatial average across all active nodes:
$$\text{CBR}_{\text{run}} = \frac{1}{N} \sum_{i=1}^{N} \text{CBR}_i \times 100\%$$

### 1.3 End-to-End Latency ($T_{\text{E2E}}$)
For each received packet $k$ at node $j$, the packet latency is the elapsed duration between kinematic state generation at sender $i$ and completion of cryptographic verification and processing at receiver $j$:

$$T_{\text{E2E}, k} = t_{\text{rx\_finish}, k} - t_{\text{tx\_gen}, k}$$

The mean end-to-end latency for a run is computed across all successfully delivered samples:
$$\bar{T}_{\text{E2E}} = \frac{\sum_{k} T_{\text{E2E}, k}}{R}$$

---

## 2. Statistical Aggregation Across Seeds

For each experimental configuration $c = (\text{Topology}, \text{LOS}, \text{Protocol})$ evaluated across $S = 10$ independent random seeds, the reported mean and $95\%$ Student-$t$ Confidence Interval are:

$$\bar{X}_c = \frac{1}{S} \sum_{s=1}^{S} X_{c, s}$$

$$\text{CI}_{95\%} = t_{0.975, S-1} \cdot \frac{\sigma_{c}}{\sqrt{S}}$$

Where $\sigma_c$ is the sample standard deviation with $S - 1$ degrees of freedom.
