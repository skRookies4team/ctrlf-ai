# app/services/strategy/scoring.py

def latency_score(p95_latency: float) -> float:
    if p95_latency < 2:
        return 0
    if p95_latency < 5:
        return 1
    return 3


def cost_score(rag_ratio: float) -> float:
    if rag_ratio < 0.3:
        return 0
    if rag_ratio < 0.6:
        return 1
    return 2


def quality_score(rag_ratio: float) -> float:
    # RAG 너무 적으면 품질 위험
    if rag_ratio < 0.1:
        return 2
    return 0


def total_score(p95_latency: float, rag_ratio: float) -> int:
    return (
        latency_score(p95_latency)
        + cost_score(rag_ratio)
        + quality_score(rag_ratio)
    )
