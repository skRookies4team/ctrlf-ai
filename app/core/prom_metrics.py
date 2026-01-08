# app/core/prom_metrics.py
from prometheus_client import Counter, Histogram

# =========================
# 1) Chat Pipeline Metrics
# =========================
#커밋용 주석 추가
# 라우팅 결정 카운트 (왜 HR로 가냐 같은 거 추적)
CHAT_ROUTE_DECISION_TOTAL = Counter(
    "ctrlf_ai_route_decision_total",
    "Total routing decisions after intent classification",
    ["route", "intent", "domain"],
)

# Fallback 카운트 (운영에서 제일 중요)
CHAT_FALLBACK_TOTAL = Counter(
    "ctrlf_ai_fallback_total",
    "Total fallback events in chat pipeline",
    ["reason", "route", "intent", "domain"],
)

# Upstream 에러 카운트 (LLM/RAG/Backend 구분)
CHAT_UPSTREAM_ERRORS_TOTAL = Counter(
    "ctrlf_ai_upstream_errors_total",
    "Total upstream errors by service and error type",
    ["service", "error_type"],
)

# 내부 단계 latency (seconds)
CHAT_RAG_LATENCY = Histogram(
    "ctrlf_ai_rag_latency_seconds",
    "RAG search latency in seconds",
    ["retriever", "domain"],
)

CHAT_LLM_LATENCY = Histogram(
    "ctrlf_ai_llm_latency_seconds",
    "LLM latency in seconds",
    ["provider", "model", "route"],
)

CHAT_BACKEND_LATENCY = Histogram(
    "ctrlf_ai_backend_latency_seconds",
    "Backend API latency in seconds",
    ["endpoint", "domain"],
)

# 가드레일/차단 이벤트들 (있으면 진짜 운영 편해짐)
CHAT_PRIVACY_BLOCK_TOTAL = Counter(
    "ctrlf_ai_privacy_block_total",
    "Total privacy gate blocks",
    ["decision"],
)

CHAT_CITATION_BLOCK_TOTAL = Counter(
    "ctrlf_ai_citation_block_total",
    "Total citation hallucination blocks",
    ["domain"],
)

CHAT_NO_SOURCE_TEMPLATE_TOTAL = Counter(
    "ctrlf_ai_no_source_template_total",
    "Total times template_only response was used due to no sources",
    ["domain", "intent"],
)

CHAT_RAG_GAP_CANDIDATE_TOTAL = Counter(
    "ctrlf_ai_rag_gap_candidate_total",
    "Total times query was marked as rag_gap_candidate",
    ["domain", "intent"],
)
