# Scientific Plotting Conventions & Visual Guidelines

This document outlines the visual formatting rules and data conventions for all figures generated in this project. Adhering to these conventions ensures visual consistency across publications, thesis chapters, and presentation figures.

---

## 1. Data Sources & Separation of Concerns

Plotting scripts in `src/analysis/` only consume pre-aggregated CSVs located in `results/rawData/`. They do not parse multi-gigabyte raw simulation trace files (`.sca`/`.vec`):

| File | Structure | Intended Visualization |
| :--- | :--- | :--- |
| `flow_kpis_per_seed.csv` | One row per simulation seed run | Scatter plots, boxplots, seed dispersion |
| `flow_kpis_by_topology.csv` | Mean & 95% Confidence Intervals per configuration | Multi-curve line plots, bar charts with error bars |
| `flow_kpis_by_distance.csv` | PDR mean & 95% CI binned by distance (50 m bins) | PDR vs. Communication Distance curves |
| `kpi_heatmap_values.csv` | Tabular performance values across matrix | Matrix heatmaps (Topologies $\times$ Protocols) |

- **Metrics available:** `PDR (%)`, `CBR (%)`, `Latency E2E (ms)`, `Active vehicles`, and processing metrics (`processingLatencyMs`, `cpuBusyTimeShare`).
- **Data immutability:** Plotting routines must never modify or overwrite source CSV files.

---

## 2. Visual Style Standards (IEEE / Springer Academic Style)

### 2.1 Colors, Markers, and Linestyles

| Design Element | Encoding Parameter | Visual Value |
| :--- | :--- | :--- |
| **Topology** | Line / Scatter Color | `highway`: `#1f77b4` (Blue)<br>`urbanRoad`: `#ff7f0e` (Orange)<br>`intersection`: `#2ca02c` (Green) |
| **Protocol** | Marker Shape | `baseline`: Circle (`o`)<br>`FF1_AES`: Square (`s`)<br>`FF1_LEA`: Triangle Up (`^`)<br>`FF1_SPECK`: Diamond (`D`) |
| **Sample Mean** | Horizontal Dash | `_` marker (`s=400`, `linewidths=2`, `zorder=3`) |
| **Demand Origin** | Marker Fill & Line Alpha | **CET Validated Peak (`s100`):** Solid filled marker, opaque line (`alpha=1.0`).<br>**Extrapolated LOS:** Hollow white marker (`facecolors="white"`), faded line segment (`alpha=0.35`). |
| **HCM LOS Annotation** | Text Label (`annotate_los()`) | Letter placed above panel at corresponding flow $x$-coordinate; bold on CET peak. |
| **PDR vs. Distance Curves** | Linestyle by Operational Role | Lightest LOS: Dotted (`:`)<br>Intermediate LOS: Dash-dot (`-.`)<br>CET Peak: Dashed (`--`)<br>Saturated Oversaturated: Solid (`-`) |

### 2.2 Typography & Axes
- **Font Family:** Serif (`DejaVu Serif`, `Times New Roman`, or `Computer Modern`).
- **Font Sizes:** Title: 12 pt, Axis labels: 11 pt, Tick marks / Legend: 10 pt.
- **X-Axis:** Numerical flow per lane (`Flow per lane (veh/h/lane)`). In multi-topology shared axes, logarithmic scale is used.
- **Export Formats:** Every plot is exported simultaneously as raster PNG (300 DPI, `dpi=300`) and vector PDF (`.pdf`) for publication inclusion.
