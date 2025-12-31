"""
Telemetry Metrics - Latency & RAG Metrics Collection

요청 처리 중 수집되는 메트릭을 저장하는 contextvars 기반 컨테이너입니다.
emit_chat_turn_once에서 이 값들을 참조하여 이벤트에 포함합니다.

사용법:
    from app.telemetry.metrics import (
        set_latency_metrics,
        get_latency_metrics,
        set_rag_metrics,
        get_rag_metrics,
    )

    # RAG 검색 후
    set_rag_metrics(
        retriever="milvus",
        top_k=5,
        scores=[0.8, 0.75, 0.7],
        sources=[{"docId": "DOC-1", "chunkId": 1, "score": 0.8}],
    )

    # 처리 완료 후
    metrics = get_latency_metrics()
    rag_metrics = get_rag_metrics()
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Latency Metrics
# =============================================================================


@dataclass
class LatencyMetrics:
    """지연 시간 메트릭 컨테이너.

    Attributes:
        total_ms: 총 응답 시간 (ms)
        llm_ms: LLM 호출 시간 (ms)
        retrieval_ms: 검색/재랭크 시간 (ms)
    """

    total_ms: Optional[int] = None
    llm_ms: Optional[int] = None
    retrieval_ms: Optional[int] = None


# Latency 메트릭 컨텍스트 변수
_latency_metrics: ContextVar[Optional[LatencyMetrics]] = ContextVar(
    "latency_metrics",
    default=None,
)


def set_latency_metrics(
    total_ms: Optional[int] = None,
    llm_ms: Optional[int] = None,
    retrieval_ms: Optional[int] = None,
) -> None:
    """지연 시간 메트릭을 설정합니다.

    기존 값이 있으면 병합합니다 (None이 아닌 값만 업데이트).

    Args:
        total_ms: 총 응답 시간 (ms)
        llm_ms: LLM 호출 시간 (ms)
        retrieval_ms: 검색/재랭크 시간 (ms)
    """
    current = _latency_metrics.get()
    if current is None:
        current = LatencyMetrics()

    if total_ms is not None:
        current.total_ms = total_ms
    if llm_ms is not None:
        current.llm_ms = llm_ms
    if retrieval_ms is not None:
        current.retrieval_ms = retrieval_ms

    _latency_metrics.set(current)


def get_latency_metrics() -> Optional[LatencyMetrics]:
    """현재 요청의 지연 시간 메트릭을 반환합니다.

    Returns:
        LatencyMetrics 또는 None (미설정 시)
    """
    return _latency_metrics.get()


def reset_latency_metrics() -> None:
    """지연 시간 메트릭을 초기화합니다."""
    _latency_metrics.set(None)


# =============================================================================
# RAG Metrics
# =============================================================================


@dataclass
class RagMetrics:
    """RAG 검색 메트릭 컨테이너.

    Attributes:
        retriever: 사용된 검색 엔진 (예: milvus, ragflow)
        top_k: 검색 TopK 설정
        scores: 검색 결과 점수 목록
        sources: 검색 결과 소스 목록 (docId, chunkId, score)
        min_score: 최소 점수
        max_score: 최대 점수
        avg_score: 평균 점수
    """

    retriever: str = "unknown"
    top_k: int = 5
    scores: List[float] = field(default_factory=list)
    sources: List[Dict] = field(default_factory=list)
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    avg_score: Optional[float] = None


# RAG 메트릭 컨텍스트 변수
_rag_metrics: ContextVar[Optional[RagMetrics]] = ContextVar(
    "rag_metrics",
    default=None,
)


def set_rag_metrics(
    retriever: str,
    top_k: int,
    scores: Optional[List[float]] = None,
    sources: Optional[List[Dict]] = None,
) -> None:
    """RAG 검색 메트릭을 설정합니다.

    scores가 제공되면 min/max/avg를 자동 계산합니다.

    Args:
        retriever: 사용된 검색 엔진 (예: milvus, ragflow)
        top_k: 검색 TopK 설정
        scores: 검색 결과 점수 목록
        sources: 검색 결과 소스 목록 [{docId, chunkId, score}, ...]
    """
    scores = scores or []
    sources = sources or []

    # 점수 통계 계산
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    avg_score: Optional[float] = None

    if scores:
        min_score = min(scores)
        max_score = max(scores)
        avg_score = sum(scores) / len(scores)

    metrics = RagMetrics(
        retriever=retriever,
        top_k=top_k,
        scores=scores,
        sources=sources,
        min_score=min_score,
        max_score=max_score,
        avg_score=avg_score,
    )

    _rag_metrics.set(metrics)


def get_rag_metrics() -> Optional[RagMetrics]:
    """현재 요청의 RAG 검색 메트릭을 반환합니다.

    Returns:
        RagMetrics 또는 None (미설정 시)
    """
    return _rag_metrics.get()


def reset_rag_metrics() -> None:
    """RAG 검색 메트릭을 초기화합니다."""
    _rag_metrics.set(None)


# =============================================================================
# Helper: RagMetrics → RagInfo 변환
# =============================================================================


def rag_metrics_to_rag_info() -> Optional["RagInfo"]:
    """현재 RagMetrics를 RagInfo 모델로 변환합니다.

    emit_chat_turn_once에서 사용하기 위한 헬퍼 함수입니다.

    Returns:
        RagInfo 또는 None (메트릭 없음 또는 소스 없음)
    """
    from app.telemetry.models import RagInfo, RagSource

    metrics = get_rag_metrics()
    if metrics is None:
        return None

    # 소스가 없으면 RagInfo를 생성하지 않음
    if not metrics.sources:
        return None

    # scores가 비어있을 수 있으므로 기본값 처리
    min_score = metrics.min_score if metrics.min_score is not None else 0.0
    max_score = metrics.max_score if metrics.max_score is not None else 0.0
    avg_score = metrics.avg_score if metrics.avg_score is not None else 0.0

    # sources 변환
    rag_sources = []
    for src in metrics.sources:
        try:
            rag_sources.append(
                RagSource(
                    doc_id=str(src.get("docId", src.get("doc_id", "unknown"))),
                    chunk_id=int(src.get("chunkId", src.get("chunk_id", 0))),
                    score=float(src.get("score", 0.0)),
                )
            )
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to convert source: {src}, error: {e}")
            continue

    if not rag_sources:
        return None

    return RagInfo(
        retriever=metrics.retriever,
        top_k=metrics.top_k,
        min_score=min_score,
        max_score=max_score,
        avg_score=avg_score,
        sources=rag_sources,
        context_excerpt=None,  # 기본 OFF (향후 플래그로 활성화)
    )


# =============================================================================
# Reset All Metrics
# =============================================================================


def reset_all_metrics() -> None:
    """모든 메트릭을 초기화합니다.

    테스트 또는 새 요청 시작 시 호출합니다.
    """
    reset_latency_metrics()
    reset_rag_metrics()
