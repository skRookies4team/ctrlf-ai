"""
RAG Quality Log - Phase 57/58

RAG 파이프라인 품질 지표 구조화 로깅

설계 원칙:
- 단일 스키마로 Query Expansion, RRF, 최종 결과 추적
- Elasticsearch/Kibana 분석 가능한 구조
- phase 태그로 버전별 비교 가능

Phase 58: Quality Gate
- L2 Distance 기반 응답 품질 제어
- REJECT/PROCEED_WITH_WARNING/PROCEED 3단계 판정
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RAGQualityLog:
    """RAG 품질 로그 구조체"""

    # 공통 필드
    phase: int = 57
    request_id: str = ""
    domain: str = ""
    intent: str = ""

    # Query 필드
    query_original: str = ""
    query_normalized: str = ""
    query_length: int = 0

    # Expansion 필드 (Phase 57)
    expansion_enabled: bool = True
    expansion_used: bool = False
    expansion_reason: str = ""
    expansion_query: str = ""
    expansion_method: str = "rule_based"

    # Search Original 필드
    search_original_count: int = 0
    search_original_min_distance: float = 0.0
    search_original_avg_distance: float = 0.0
    search_original_max_distance: float = 0.0
    search_original_top5_doc_ids: List[str] = field(default_factory=list)

    # Search Expanded 필드 (Phase 57)
    search_expanded_count: int = 0
    search_expanded_min_distance: float = 0.0
    search_expanded_avg_distance: float = 0.0
    search_expanded_max_distance: float = 0.0
    search_expanded_top5_doc_ids: List[str] = field(default_factory=list)

    # RRF 필드 (Phase 57)
    rrf_enabled: bool = True
    rrf_applied: bool = False
    rrf_k_parameter: int = 60
    rrf_output_count: int = 0
    rrf_common_doc_count: int = 0

    # Final Result 필드
    result_sources_count: int = 0
    result_min_distance: float = 0.0
    result_avg_distance: float = 0.0
    result_retriever_used: str = ""
    result_gate_action: str = ""
    result_top5_doc_ids: List[str] = field(default_factory=list)

    # Quality Metrics 필드
    quality_distance_improvement: float = 0.0
    quality_expansion_benefit: bool = False
    quality_rrf_benefit: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Elasticsearch 인덱싱용 dict 변환 (nested 구조)"""
        return {
            "@timestamp": datetime.utcnow().isoformat(),
            "phase": self.phase,
            "request_id": self.request_id,
            "domain": self.domain,
            "intent": self.intent,
            "query": {
                "original": self.query_original,
                "normalized": self.query_normalized,
                "length": self.query_length,
            },
            "expansion": {
                "enabled": self.expansion_enabled,
                "used": self.expansion_used,
                "reason": self.expansion_reason,
                "query": self.expansion_query,
                "method": self.expansion_method,
            },
            "search": {
                "original": {
                    "count": self.search_original_count,
                    "min_distance": self.search_original_min_distance,
                    "avg_distance": self.search_original_avg_distance,
                    "max_distance": self.search_original_max_distance,
                    "top5_doc_ids": self.search_original_top5_doc_ids,
                },
                "expanded": {
                    "count": self.search_expanded_count,
                    "min_distance": self.search_expanded_min_distance,
                    "avg_distance": self.search_expanded_avg_distance,
                    "max_distance": self.search_expanded_max_distance,
                    "top5_doc_ids": self.search_expanded_top5_doc_ids,
                },
            },
            "rrf": {
                "enabled": self.rrf_enabled,
                "applied": self.rrf_applied,
                "k_parameter": self.rrf_k_parameter,
                "output_count": self.rrf_output_count,
                "common_doc_count": self.rrf_common_doc_count,
            },
            "result": {
                "sources_count": self.result_sources_count,
                "min_distance": self.result_min_distance,
                "avg_distance": self.result_avg_distance,
                "retriever_used": self.result_retriever_used,
                "gate_action": self.result_gate_action,
                "top5_doc_ids": self.result_top5_doc_ids,
            },
            "quality": {
                "distance_improvement": self.quality_distance_improvement,
                "expansion_benefit": self.quality_expansion_benefit,
                "rrf_benefit": self.quality_rrf_benefit,
            },
        }

    def to_flat_dict(self) -> Dict[str, Any]:
        """플랫 구조 dict (로깅 호환용)"""
        return {
            "phase": self.phase,
            "request_id": self.request_id,
            "domain": self.domain,
            "query_original": self.query_original[:50],
            "expansion_used": self.expansion_used,
            "expansion_reason": self.expansion_reason,
            "search_original_count": self.search_original_count,
            "search_original_min_dist": round(self.search_original_min_distance, 3),
            "search_expanded_count": self.search_expanded_count,
            "search_expanded_min_dist": round(self.search_expanded_min_distance, 3),
            "rrf_applied": self.rrf_applied,
            "result_sources_count": self.result_sources_count,
            "result_min_dist": round(self.result_min_distance, 3),
            "quality_improvement": round(self.quality_distance_improvement, 1),
        }


def calculate_distance_improvement(
    original_min: float,
    expanded_min: float,
) -> float:
    """
    거리 개선율 계산 (%)

    음수 = 개선됨 (거리 감소)
    양수 = 악화됨 (거리 증가)
    """
    if original_min <= 0:
        return 0.0

    return ((expanded_min - original_min) / original_min) * 100


def calculate_common_doc_count(
    original_doc_ids: List[str],
    expanded_doc_ids: List[str],
) -> int:
    """양쪽 검색 결과에 공통으로 있는 문서 수"""
    return len(set(original_doc_ids) & set(expanded_doc_ids))


def log_rag_quality(log: RAGQualityLog) -> None:
    """
    RAG 품질 로그 출력

    INFO 레벨로 핵심 지표만 출력 (운영용)
    """
    # 핵심 지표만 간결하게
    logger.info(
        f"[RAGQuality] phase={log.phase} | "
        f"expansion={log.expansion_used} | "
        f"rrf={log.rrf_applied} | "
        f"sources={log.result_sources_count} | "
        f"min_dist={log.result_min_distance:.3f} | "
        f"improvement={log.quality_distance_improvement:+.1f}%"
    )


def log_rag_quality_debug(log: RAGQualityLog) -> None:
    """
    RAG 품질 로그 상세 출력

    DEBUG 레벨로 전체 지표 출력 (개발용)
    """
    flat = log.to_flat_dict()
    logger.debug(f"[RAGQuality:DEBUG] {flat}")


def build_rag_quality_log(
    request_id: str,
    domain: str,
    query: str,
    normalized_query: str,
    original_sources: List,
    expanded_sources: Optional[List] = None,
    rewrite_result: Optional[Any] = None,
    rrf_result: Optional[Any] = None,
    final_sources: Optional[List] = None,
    retriever_used: str = "MILVUS",
    settings: Optional[Any] = None,
) -> RAGQualityLog:
    """
    RAG 파이프라인 결과로부터 품질 로그 빌드

    Args:
        request_id: 요청 ID
        domain: 검색 도메인
        query: 원본 쿼리
        normalized_query: 정규화된 쿼리
        original_sources: 원문 검색 결과
        expanded_sources: 확장 검색 결과 (Optional)
        rewrite_result: Query Expansion 결과 (Optional)
        rrf_result: RRF Fusion 결과 (Optional)
        final_sources: 최종 소스 (Optional, 없으면 original 사용)
        retriever_used: 사용된 검색기
        settings: 설정 객체

    Returns:
        RAGQualityLog: 빌드된 로그 객체
    """
    log = RAGQualityLog(
        request_id=request_id,
        domain=domain,
        query_original=query,
        query_normalized=normalized_query,
        query_length=len(query),
    )

    # 설정 반영
    if settings:
        log.expansion_enabled = getattr(settings, 'QUERY_EXPANSION_ENABLED', True)
        log.rrf_enabled = getattr(settings, 'RAG_FUSION_ENABLED', True)
        log.rrf_k_parameter = getattr(settings, 'RRF_K_PARAMETER', 60)

    # Original Search 지표
    if original_sources:
        log.search_original_count = len(original_sources)
        distances = [s.score for s in original_sources if hasattr(s, 'score') and s.score]
        if distances:
            log.search_original_min_distance = min(distances)
            log.search_original_avg_distance = sum(distances) / len(distances)
            log.search_original_max_distance = max(distances)
        log.search_original_top5_doc_ids = [
            s.doc_id for s in original_sources[:5] if hasattr(s, 'doc_id')
        ]

    # Expansion 지표
    if rewrite_result:
        log.expansion_used = rewrite_result.used
        log.expansion_reason = rewrite_result.reason
        log.expansion_query = rewrite_result.rewritten if rewrite_result.used else ""

    # Expanded Search 지표
    if expanded_sources:
        log.search_expanded_count = len(expanded_sources)
        distances = [s.score for s in expanded_sources if hasattr(s, 'score') and s.score]
        if distances:
            log.search_expanded_min_distance = min(distances)
            log.search_expanded_avg_distance = sum(distances) / len(distances)
            log.search_expanded_max_distance = max(distances)
        log.search_expanded_top5_doc_ids = [
            s.doc_id for s in expanded_sources[:5] if hasattr(s, 'doc_id')
        ]

        # 공통 문서 수
        log.rrf_common_doc_count = calculate_common_doc_count(
            log.search_original_top5_doc_ids,
            log.search_expanded_top5_doc_ids,
        )

    # RRF 지표
    if rrf_result:
        log.rrf_applied = rrf_result.fusion_applied
        log.rrf_output_count = len(rrf_result.results) if rrf_result.results else 0

    # Final Result 지표
    sources = final_sources or original_sources
    if sources:
        log.result_sources_count = len(sources)
        distances = [s.score for s in sources if hasattr(s, 'score') and s.score]
        if distances:
            log.result_min_distance = min(distances)
            log.result_avg_distance = sum(distances) / len(distances)
        log.result_top5_doc_ids = [
            s.doc_id for s in sources[:5] if hasattr(s, 'doc_id')
        ]

    log.result_retriever_used = retriever_used

    # Quality Metrics 계산
    if log.expansion_used and log.search_expanded_min_distance > 0:
        log.quality_distance_improvement = calculate_distance_improvement(
            log.search_original_min_distance,
            log.search_expanded_min_distance,
        )
        # 개선 여부 (음수면 개선됨)
        log.quality_expansion_benefit = log.quality_distance_improvement < 0

    if log.rrf_applied:
        log.quality_rrf_benefit = log.rrf_common_doc_count > 0

    return log


# =============================================================================
# Phase 58: Quality Gate - L2 Distance 기반 응답 품질 제어
# =============================================================================

class QualityGrade(str, Enum):
    """품질 등급"""
    OK = "OK"                     # 정상 품질
    LOW = "LOW"                   # 저품질 (경고)
    INSUFFICIENT = "INSUFFICIENT" # 불충분 (거부)


class QualityAction(str, Enum):
    """품질 판정 액션"""
    PROCEED = "PROCEED"                         # 정상 진행
    PROCEED_WITH_WARNING = "PROCEED_WITH_WARNING"  # 경고 포함 진행
    REJECT = "REJECT"                           # 거부 (LLM 생성 스킵)


@dataclass
class QualityGateDecision:
    """Quality Gate 판정 결과"""
    grade: QualityGrade
    action: QualityAction
    min_l2_distance: float
    avg_l2_distance: float = 0.0
    source_count: int = 0
    warning_message: Optional[str] = None
    reject_reason: Optional[str] = None


def evaluate_sources_quality(
    sources: List,
    warn_threshold: float = 1.0,
    reject_threshold: float = 1.5,
) -> QualityGateDecision:
    """
    검색 결과의 품질을 평가합니다.

    Args:
        sources: ChatSource 리스트
        warn_threshold: 경고 임계값 (min_l2 > warn_threshold → LOW)
        reject_threshold: 거부 임계값 (min_l2 > reject_threshold → INSUFFICIENT)

    Returns:
        QualityGateDecision: 품질 판정 결과
    """
    if not sources:
        return QualityGateDecision(
            grade=QualityGrade.INSUFFICIENT,
            action=QualityAction.REJECT,
            min_l2_distance=float('inf'),
            source_count=0,
            reject_reason="검색 결과가 없습니다.",
        )

    # L2 거리 계산
    distances = [s.score for s in sources if hasattr(s, 'score') and s.score is not None]
    if not distances:
        return QualityGateDecision(
            grade=QualityGrade.OK,
            action=QualityAction.PROCEED,
            min_l2_distance=0.0,
            source_count=len(sources),
        )

    min_distance = min(distances)
    avg_distance = sum(distances) / len(distances)

    # 거부 임계값 초과
    if min_distance > reject_threshold:
        return QualityGateDecision(
            grade=QualityGrade.INSUFFICIENT,
            action=QualityAction.REJECT,
            min_l2_distance=min_distance,
            avg_l2_distance=avg_distance,
            source_count=len(sources),
            reject_reason=f"검색 결과의 관련도가 낮습니다 (min_l2={min_distance:.3f} > {reject_threshold})",
        )

    # 경고 임계값 초과
    if min_distance > warn_threshold:
        return QualityGateDecision(
            grade=QualityGrade.LOW,
            action=QualityAction.PROCEED_WITH_WARNING,
            min_l2_distance=min_distance,
            avg_l2_distance=avg_distance,
            source_count=len(sources),
            warning_message=f"검색 결과의 관련도가 다소 낮을 수 있습니다.",
        )

    # 정상
    return QualityGateDecision(
        grade=QualityGrade.OK,
        action=QualityAction.PROCEED,
        min_l2_distance=min_distance,
        avg_l2_distance=avg_distance,
        source_count=len(sources),
    )


def log_quality_gate_decision(
    decision: QualityGateDecision,
    query: str,
    domain: str,
) -> None:
    """Quality Gate 판정 결과를 로깅합니다."""
    query_preview = query[:50] if query else ""

    if decision.action == QualityAction.REJECT:
        logger.warning(
            f"[QualityGate] REJECT | "
            f"grade={decision.grade.value} | "
            f"min_l2={decision.min_l2_distance:.3f} | "
            f"sources={decision.source_count} | "
            f"domain={domain} | "
            f"query={query_preview}..."
        )
    elif decision.action == QualityAction.PROCEED_WITH_WARNING:
        logger.info(
            f"[QualityGate] WARN | "
            f"grade={decision.grade.value} | "
            f"min_l2={decision.min_l2_distance:.3f} | "
            f"sources={decision.source_count} | "
            f"domain={domain}"
        )
    else:
        logger.debug(
            f"[QualityGate] OK | "
            f"min_l2={decision.min_l2_distance:.3f} | "
            f"sources={decision.source_count}"
        )


def build_clarification_response(
    decision: QualityGateDecision,
    query: str,
    domain: str,
) -> str:
    """
    REJECT 판정 시 사용자에게 보여줄 명확화 응답을 생성합니다.

    Args:
        decision: Quality Gate 판정 결과
        query: 원본 쿼리
        domain: 도메인

    Returns:
        str: 명확화 응답 메시지
    """
    return (
        "죄송합니다. 질문과 관련된 정보를 충분히 찾지 못했습니다.\n\n"
        "더 정확한 답변을 드리기 위해 질문을 조금 더 구체적으로 해주시겠어요?\n"
        "예를 들어, 특정 제도명이나 상황을 포함해 주시면 도움이 됩니다."
    )
