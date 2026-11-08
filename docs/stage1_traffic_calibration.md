# Stage 1: Traffic Calibration with CET-SP Data (MSVP 2016–2019)

Traffic modeling and calibration rely exclusively on the *Mobilidade no Sistema Viário Principal — Volumes e Velocidades* (MSVP) annual reports published by the São Paulo Traffic Engineering Company (CET-SP) from 2016 through 2019.
Previous TomTom data was deprecated and removed on the `cet-calibration` branch.

---

## 1. Data Overview

For each surveyed arterial road and direction during typical weekdays, the MSVP reports provide:

- **Volume:** Classified manual vehicle counts (passenger cars, municipal buses, chartered buses, heavy trucks, motorcycles, and bicycles) in 1-hour intervals during peak periods (07:00–10:00 and 17:00–20:00).
- **Speed:** Space-mean travel speeds collected via GPS test cars traversing each corridor within active traffic flow (corridor distance ÷ travel duration), recorded across matching hourly intervals.

Tabular data is curated in `data/cet_volumes.csv` and `data/cet_speeds.csv`, indexed by survey year (`ano`) and simulation route identifier (`route`).

### 1.1 Annual Coverage

| Year | Highway (Av. 23 de Maio) | Urban Road (R. da Consolação) | Signalized Intersection (Av. Nove de Julho / Av. São Gabriel) |
| :--- | :--- | :--- | :--- |
| **2019** | Volume & Speed | Volume & Speed | Volume (Nove de Julho) & Speed (both avenues) |
| **2018** | Volume only (no speed survey in MSVP 2018) | Volume only | Not surveyed |
| **2017** | Not surveyed | Volume & Speed | Speed only (Nove de Julho) |
| **2016** | Volume & Speed | Not surveyed | Not surveyed |

---

## 2. Experimental Corridors & Topologies

| Topology | Speed Measurement Section | Volume Count Point | Demand Classification |
| :--- | :--- | :--- | :--- |
| **Highway** | Av. 23 de Maio (Vd. Brig. Luís Antônio ↔ Vd. Paraíso, 2,498 m) | Viad. Beneficência Portuguesa | Observed (`cet_volume`) |
| **Urban Road** | R. da Consolação (Av. Paulista ↔ R. Rêgo Freitas, 1,922 m) | R. Caio Prado – R. Marquês de Paranaguá | Observed (`cet_volume`) |
| **Intersection (Major)** | Av. Nove de Julho (Av. Cidade Jardim ↔ Av. São Gabriel, 1,351 m) | R. Manuel Guedes – R. Emanuel Kant | Observed (`cet_volume`) |
| **Intersection (Cross)** | Av. São Gabriel (Av. Nove de Julho ↔ Av. Antônio J. de M. Andrade, ~905 m) | Inferred via speed bisection | Inferred (`speed_bisection`) |

### Corridor Selection Rationale
- **Highway:** Av. 23 de Maio represents a high-flow, signal-free expressway with concurrent volume and speed measurements in both northbound and southbound carriageways.
- **Signalized Intersection:** Av. Nove de Julho × Av. São Gabriel represents a grade-separated at-grade crossing between two primary arterial corridors surveyed by CET-SP.

---

## 3. Network Mapping and Topological Repair

SUMO networks are compiled from OpenStreetMap (OSM) via `netconvert`. Discrepancies between raw OSM geometry and real-world Brazilian driving conditions necessitated algorithmic network repair (`src/traffic/network_repair.py`):

1. **Stub Edge Pruning:** Edges shorter than 20 m resulting from junction clustering (`--junctions.join`) prevent realistic 5-meter vehicle insertion. These stubs are trimmed at route boundaries.
2. **Through-Lane Restoration:** Missing lane turn markings (`turn:lanes`) in OSM cause `netconvert` to force right or left turns at clustered junctions. The repair tool designates right lanes as shared through-lanes along route corridors (7 junctions in `urbanRoad`, 5 in `intersection`).
3. **Signal Green-Wave Guarantee:** Default `netconvert` signal generation equally divides green times across all phases, leaving main arterial roads with inadequate green ratios (15–28%). The linear optimization solver guarantees at least `network.min_main_green_share` (55%) green time per cycle for corridors with empirical CET counts.
4. **Junction Joining:** On Av. Nove de Julho, closely-spaced signal nodes at R. Alemanha and R. Turquia (33 m apart) are unified into a single logical junction to eliminate artificial single-lane bottlenecks.
5. **Highway On/Off-Ramp Flows:** `route_utils.route_ramps` identifies grade-separated ramps along Av. 23 de Maio. Yield-only secondary merge links without priority are filtered out to prevent artificial queue collapse. Ramp flows are assigned proportionally to lane counts, capped by `network.max_ramp_veh_h_per_lane` (1,800 veh/h/lane).

---

## 4. Calibration & Validation Methodology

Following the methodology of **Khaleghian et al. (SMARTCOMP 2023)**:

1. **Observed Demand:** Route demand is set to the hourly CET volume in Passenger Car Equivalents (PCE: passenger car = 1.0; bus/truck = 1.5; motorcycle = 0.4; bicycle = 0).
2. **Inferred Demand:** For corridors without volume counts (Av. São Gabriel), flows are inferred via logarithmic bisection against observed CET speeds using SUMO headless simulations.
3. **Signal Coordination:** Coordinated green-wave offsets are synthesized via SUMO's `tlsCoordinator.py`, weighted by mean afternoon peak demand.
4. **Krauss Model Tuning via TPE:** One parameter set per topology (`accel`, `decel`, `tau`, `sigma`, `minGap`, `speedFactor`, `speedDev`) is tuned using Tree-structured Parzen Estimators (**Optuna**) to minimize velocity Root Mean Square Error (RMSE) across 2019 calibration hours.
5. **Validation Criterion:** A topology passes when its validation relative RMSE (validation RMSE ÷ mean observed validation speed) does not exceed **25%** (`acceptance.max_validation_relative_rmse`), matching the accuracy benchmark in Khaleghian et al. (2023).

---

## 5. HCM Level of Service (LOS) Demand Sweep

To evaluate V2V communications across realistic operational regimes, `src/traffic/map_los.py` simulates the calibrated models across demand multipliers ($0.1\times$ to $1.5\times$ mean 2019 flows, 3 seeds each) and maps each factor to a **Highway Capacity Manual (HCM 6th/7th Ed.) Level of Service (LOS A through F)**:

- **Highway (Basic Freeway Segment):** Passenger-car density (pc/km/lane). Limits: A ($\le 7$), B ($\le 11$), C ($\le 16$), D ($\le 22$), E ($\le 28$), F ($> 28$).
- **Urban Road (Urban Street Segment):** Travel speed ratio (mean travel speed ÷ free-flow speed). Limits: A ($\ge 80\%$), B ($\ge 67\%$), C ($\ge 50\%$), D ($\ge 40\%$), E ($\ge 30\%$), F ($< 30\%$).
- **Signalized Intersection:** Control delay per signal ($[\text{timeLoss} + \text{departDelay}] \div \text{signalCount}$). Limits: A ($\le 10\text{ s}$), B ($\le 20\text{ s}$), C ($\le 35\text{ s}$), D ($\le 55\text{ s}$), E ($\le 80\text{ s}$), F ($> 80\text{ s}$).

### Resulting V2V Experimental Matrix (`flow_levels.json`)

The final experimental matrix consists of **14 representative scenarios**:
- **Highway:** LOS A (`s015`), B (`s030`), C (`s040`), D (`s055`), E (`s075`), F (`s100` - CET peak).
- **Urban Road:** LOS C (`s045`), D (`s095`), E (`s100` - CET peak), F (`s120`).
- **Intersection:** LOS A (`s020`), B (`s050`), C (`s070`), F (`s100` - CET peak).

Combined with **4 cryptographic protocols** and **10 pseudo-random seeds**, this yields **56 configurations and 560 OMNeT++ / Veins simulation runs**.

---

## 6. Calibration Results Summary

| Topology | Calibrated Krauss Profile (`accel, decel, tau, sigma, minGap, speedFactor, speedDev`) | Calibration RMSE | Validation RMSE | Relative RMSE | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Highway** | `2.49, 3.69, 0.57, 0.20, 1.80, 0.72, 0.19` | 8.6 km/h ($n=12$) | 8.2 km/h ($n=12$, 2016) | **22.6%** | **Passed** |
| **Urban Road** | `3.48, 2.66, 0.56, 0.18, 3.29, 0.93, 0.27` | 9.0 km/h ($n=12$) | 10.6 km/h ($n=12$, 2017) | 57.9% | Documented Limitation |
| **Intersection** | `3.13, 3.96, 1.91, 0.09, 2.79, 0.57, 0.20` | 5.7 km/h ($n=8$) | 6.0 km/h ($n=4$, holdout) | 72.4% | Documented Limitation |

> [!NOTE]
> On the urban road and intersection, observed afternoon congestion originates primarily from downstream spillback queues outside the simulated bounding box (e.g. downtown gridlock). A single car-following profile cannot simultaneously reproduce peak morning free-flow and afternoon external queueing with identical vehicle volumes. These conditions are preserved and documented as structural research limitations.
