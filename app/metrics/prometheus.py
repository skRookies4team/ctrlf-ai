from fastapi import APIRouter, Request, Response
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
import time

# 🔹 Router (prefix 없음!)
metrics_router = APIRouter()

# ================================
# Prometheus Metrics 정의
# ================================

REQUEST_COUNT = Counter(
    "ctrlf_ai_requests_total",
    "Total AI requests",
    ["route", "method", "status", "domain", "model_name", "rag_used"]
)

REQUEST_LATENCY = Histogram(
    "ctrlf_ai_request_latency_seconds",
    "AI request latency",
    ["route", "method", "domain", "model_name", "rag_used"]
)

# ================================
# /metrics endpoint
# ================================

@metrics_router.get("/metrics")
def metrics():
    """
    Prometheus scrape endpoint
    """
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )

# ================================
# FastAPI Middleware
# ================================

async def prometheus_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start

    # RequestContextMiddleware에서 이미 세팅돼 있다고 가정
    domain = str(getattr(request.state, "domain", "UNKNOWN") or "UNKNOWN")
    model_name = str(getattr(request.state, "model_name", "UNKNOWN") or "UNKNOWN")
    rag_used = str(bool(getattr(request.state, "rag_used", False)))

    REQUEST_COUNT.labels(
        route=request.url.path,
        method=request.method,
        status=str(response.status_code),
        domain=domain,
        model_name=model_name,
        rag_used=rag_used,
    ).inc()

    REQUEST_LATENCY.labels(
        route=request.url.path,
        method=request.method,
        domain=domain,
        model_name=model_name,
        rag_used=rag_used,
    ).observe(elapsed)

    return response
