# app/services/strategy/prometheus_reader.py
import requests

PROM_URL = "http://prometheus:9090"

def _query(q: str) -> float:
    r = requests.get(
        f"{PROM_URL}/api/v1/query",
        params={"query": q},
        timeout=2,
    )
    r.raise_for_status()
    result = r.json()["data"]["result"]
    if not result:
        return 0.0
    return float(result[0]["value"][1])


def query_p95_latency(domain: str) -> float:
    return _query(f'''
        histogram_quantile(
          0.95,
          sum by (le) (
            rate(ctrlf_ai_request_latency_seconds_bucket{{domain="{domain}"}}[1m])
          )
        )
    ''')


def query_rag_ratio(domain: str) -> float:
    return _query(f'''
        sum(rate(ctrlf_ai_requests_total{{domain="{domain}",rag_used="True"}}[1m]))
        /
        sum(rate(ctrlf_ai_requests_total{{domain="{domain}"}}[1m]))
    ''')
