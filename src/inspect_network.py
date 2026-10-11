import os
import xml.etree.ElementTree as ET

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_FILE = os.path.join(PROJECT_DIR, "conf", "network.net.xml")

def inspect_edges():
    if not os.path.exists(NET_FILE):
        print(f"[ERROR] No se encontró {NET_FILE}")
        return

    tree = ET.parse(NET_FILE)
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