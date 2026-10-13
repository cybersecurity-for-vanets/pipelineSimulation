import requests

def fetch_tomtom_data(api_key, bbox, default_speed):
    """
    Consulta a velocidade atual na API TomTom Traffic Flow.
    """
    min_lat, min_lon, _, _ = bbox
    url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?key={api_key}&point={min_lat},{min_lon}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            print(data)  # Imprime a resposta completa para depuração
            return data.get("flowSegmentData", {}).get("currentSpeed", default_speed)
    except Exception:
        pass
    return default_speed

# r = fetch_tomtom_data("99kD3MT4jtVw2LPxJJr2BCRd9fwhUvEX",
#                   [-23.4900000, -46.5100000, -23.4800000, -46.4900000],
#                   100)
# print(r)

"""
{
    "flowSegmentData": {
        "frc": "FRC1",
        "currentSpeed": 97,
        "freeFlowSpeed": 97,
        "currentTravelTime": 253,
        "freeFlowTravelTime": 253,
        "confidence": 1,
        "roadClosure": false,
        "coordinates": {
            "coordinate": [
                {
                    "latitude": -23.507300901937967,
                    "longitude": -46.548629753884995
                }]
        },
        "@version": "4"
    }
}
"""