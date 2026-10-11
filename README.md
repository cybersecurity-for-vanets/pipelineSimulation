# Simulation structure

This project follows the directory layout described in `structurePathDir.md`:

```text
simulation
├── src
│   ├── fetch_data.py
│   ├── generate_routes.py
│   ├── generate_stage2_configs.py
│   ├── orchestrate_stage3.py
│   └── analyze_stage4_results.py
├── osmFiles
│   ├── highway.osm
│   ├── urbanRoad.osm
│   └── intersection.osm
├── logs
│   ├── highway
│   ├── urbanRoad
│   └── intersection
├── results
│   ├── rawData
│   └── plots
├── conf
│   ├── tomtom.json
│   ├── coordinates_scenaries.json
│   ├── omnetpp.ini
│   ├── scenario.sumocfg
│   ├── vehicle_types.xml
│   └── config.xml
├── trafficXmlFiles
└── README.md
```
