# app/services/strategy/prometheus_client.py
import requests

PROM_URL = "http://prometheus:9090"

def query_prometheus(query: str) -> float:
    r = requests.get(
        f"{PROM_URL}/api/v1/query",
        params={"query": query},
        timeout=2,
    )
    data = r.json()
    return float(data["data"]["result"][0]["value"][1])
