import argparse
import os
import xml.etree.ElementTree as ET

from src.env import OSM_DIR


def inspect_edges(net_file=None):
    if net_file is None:
        net_file = OSM_DIR / "highway.net.xml"
    root = tree.getroot()
    
    edges = []
    for edge in root.findall('edge'):
        # Filtrar edges internos de intersecciones (los que empiezan con ':')
        edge_id = edge.attrib.get('id', '')
        if edge_id and not edge_id.startswith(':'):
            edges.append(edge_id)

    print(f"=== Tramos encontrados en network.net.xml ({len(edges)} en total) ===")
    for e in edges[:15]:  # Muestra los primeros 15 edges
        print(f"  - {e}")
        
    if len(edges) > 15:
        print(f"  ... y {len(edges) - 15} tramos más.")

if __name__ == "__main__":
    inspect_edges()