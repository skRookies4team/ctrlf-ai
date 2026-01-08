"""
Quality Gate - Phase 58

L2 Distance 기반 RAG 품질 게이트

설계 원칙:
- SOFT_DEMOTE 이후에도 극단적 저품질(L2 > 1.6) 케이스 방지
- min_l2_distance 기반 3단계 판정: OK, LOW, INSUFFICIENT
- INSUFFICIENT일 경우 LLM 생성 스킵 → 명확화 응답
- 환각(hallucination) 위험 최소화

Decision Table:
┌──────────────────────┬──────────────┬────────────────────────┬────────────────────────┐
│ 조건 (min_L2_distance)│ 등급         │ 동작                   │ 응답 전략              │
├──────────────────────┼──────────────┼────────────────────────┼────────────────────────┤
│ <= 1.4               │ OK           │ PROCEED                │ 정상 RAG 답변          │
│ 1.4 < d <= 1.6       │ LOW          │ PROCEED_WITH_WARNING   │ sources 사용 + 경고    │
│ > 1.6                │ INSUFFICIENT │ REJECT                 │ 근거 부족 → 명확화     │
│ sources == 0         │ INSUFFICIENT │ REJECT                 │ 근거 부족 → 명확화     │
└──────────────────────┴──────────────┴────────────────────────┴────────────────────────┘
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Enums & Data Classes
# =============================================================================

class QualityGrade(str, Enum):
    """품질 등급"""
    OK = "OK"                    # 정상 품질
    LOW = "LOW"                  # 낮은 품질 (경고 필요)
    INSUFFICIENT = "INSUFFICIENT"  # 근거 부족 (LLM 스킵)


class QualityAction(str, Enum):
    """품질 게이트 액션"""
    PROCEED = "PROCEED"                    # 정상 진행
    PROCEED_WITH_WARNING = "PROCEED_WITH_WARNING"  # 경고 포함 진행
    REJECT = "REJECT"                      # LLM 생성 거부


@dataclass
class QualityGateDecision:
    """
    품질 게이트 판정 결과

    Attributes:
        grade: 품질 등급 (OK, LOW, INSUFFICIENT)
        action: 권장 액션 (PROCEED, PROCEED_WITH_WARNING, REJECT)
        min_l2_distance: 최소 L2 거리 (가장 관련성 높은 문서)
        avg_l2_distance: 평균 L2 거리
        sources_count: 검색된 소스 수
        warning_message: 경고 메시지 (LOW 등급 시)
        clarify_message: 명확화 안내 메시지 (INSUFFICIENT 등급 시)
        suggestions: 구체화 질문 예시 리스트
        warn_threshold: 사용된 경고 임계값
        reject_threshold: 사용된 거부 임계값
    """
    grade: QualityGrade
    action: QualityAction
    min_l2_distance: float = 0.0
    avg_l2_distance: float = 0.0
    sources_count: int = 0
    warning_message: Optional[str] = None
    clarify_message: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)
    warn_threshold: float = 1.4
    reject_threshold: float = 1.6


# =============================================================================
# Distance Extraction Utility
# =============================================================================

def extract_distance(source: Any) -> Optional[float]:
    """
    소스 객체에서 L2 거리 값을 추출합니다.

    프로젝트마다 필드명이 다를 수 있으므로 여러 필드를 시도합니다:
    - score (현재 사용)
    - l2_distance
    - distance

    Args:
        source: ChatSource 또는 유사 객체

    Returns:
        L2 거리 값 (없으면 None)
    """
    # 우선순위 순으로 필드 시도
    for field_name in ["score", "l2_distance", "distance"]:
        if hasattr(source, field_name):
            value = getattr(source, field_name)
            if value is not None and isinstance(value, (int, float)):
                return float(value)

    # dict인 경우
    if isinstance(source, dict):
        for field_name in ["score", "l2_distance", "distance"]:
            if field_name in source and source[field_name] is not None:
                return float(source[field_name])

    return None


def calculate_distance_stats(sources: List[Any]) -> tuple[float, float, float]:
    """
    소스 리스트에서 L2 거리 통계를 계산합니다.

    Args:
        sources: 소스 리스트

    Returns:
        (min_distance, avg_distance, max_distance)
        소스가 없거나 거리 값이 없으면 (float('inf'), float('inf'), float('inf'))
    """
    distances = []
    for source in sources:
        dist = extract_distance(source)
        if dist is not None:
            distances.append(dist)

    if not distances:
        return float('inf'), float('inf'), float('inf')

    return min(distances), sum(distances) / len(distances), max(distances)


# =============================================================================
# Quality Gate Evaluation
# =============================================================================

def evaluate_sources_quality(
    sources: List[Any],
    warn_threshold: Optional[float] = None,
    reject_threshold: Optional[float] = None,
) -> QualityGateDecision:
    """
    검색 결과의 품질을 평가하고 게이트 판정을 수행합니다.

    Phase 58: L2 거리 기반 3단계 판정
    - sources == 0 → INSUFFICIENT (무조건)
    - min_l2 <= warn_th → OK/PROCEED
    - warn_th < min_l2 <= reject_th → LOW/PROCEED_WITH_WARNING
    - min_l2 > reject_th → INSUFFICIENT/REJECT

    Args:
        sources: RAG 검색 결과 (ChatSource 리스트)
        warn_threshold: 경고 임계값 (기본: settings.RAG_QUALITY_L2_WARN)
        reject_threshold: 거부 임계값 (기본: settings.RAG_QUALITY_L2_REJECT)

    Returns:
        QualityGateDecision: 판정 결과
    """
    settings = get_settings()

    # 임계값 결정
    warn_th = warn_threshold if warn_threshold is not None else settings.RAG_QUALITY_L2_WARN
    reject_th = reject_threshold if reject_threshold is not None else settings.RAG_QUALITY_L2_REJECT

    # Case 1: 소스 없음 → INSUFFICIENT
    if not sources:
        logger.info(
            "[QualityGate] INSUFFICIENT: sources=0 → REJECT"
        )
        return QualityGateDecision(
            grade=QualityGrade.INSUFFICIENT,
            action=QualityAction.REJECT,
            min_l2_distance=float('inf'),
            avg_l2_distance=float('inf'),
            sources_count=0,
            clarify_message=_build_default_clarify_message(),
            suggestions=_build_generic_suggestions(),
            warn_threshold=warn_th,
            reject_threshold=reject_th,
        )

    # 거리 통계 계산
    min_dist, avg_dist, max_dist = calculate_distance_stats(sources)

    # Case 2: 거리 정보 없음 → OK (레거시 호환)
    if min_dist == float('inf'):
        logger.warning(
            "[QualityGate] No distance info in sources, defaulting to OK"
        )
        return QualityGateDecision(
            grade=QualityGrade.OK,
            action=QualityAction.PROCEED,
            min_l2_distance=0.0,
            avg_l2_distance=0.0,
            sources_count=len(sources),
            warn_threshold=warn_th,
            reject_threshold=reject_th,
        )

    # Case 3: min_l2 > reject_th → INSUFFICIENT
    if min_dist > reject_th:
        logger.info(
            f"[QualityGate] INSUFFICIENT: min_l2={min_dist:.3f} > reject_th={reject_th} → REJECT"
        )
        return QualityGateDecision(
            grade=QualityGrade.INSUFFICIENT,
            action=QualityAction.REJECT,
            min_l2_distance=min_dist,
            avg_l2_distance=avg_dist,
            sources_count=len(sources),
            clarify_message=_build_default_clarify_message(),
            suggestions=_build_generic_suggestions(),
            warn_threshold=warn_th,
            reject_threshold=reject_th,
        )

    # Case 4: warn_th < min_l2 <= reject_th → LOW
    if min_dist > warn_th:
        logger.info(
            f"[QualityGate] LOW: {warn_th} < min_l2={min_dist:.3f} <= {reject_th} → PROCEED_WITH_WARNING"
        )
        return QualityGateDecision(
            grade=QualityGrade.LOW,
            action=QualityAction.PROCEED_WITH_WARNING,
            min_l2_distance=min_dist,
            avg_l2_distance=avg_dist,
            sources_count=len(sources),
            warning_message=_build_warning_message(),
            warn_threshold=warn_th,
            reject_threshold=reject_th,
        )

    # Case 5: min_l2 <= warn_th → OK
    logger.debug(
        f"[QualityGate] OK: min_l2={min_dist:.3f} <= warn_th={warn_th} → PROCEED"
    )
    return QualityGateDecision(
        grade=QualityGrade.OK,
        action=QualityAction.PROCEED,
        min_l2_distance=min_dist,
        avg_l2_distance=avg_dist,
        sources_count=len(sources),
        warn_threshold=warn_th,
        reject_threshold=reject_th,
    )


# =============================================================================
# Clarification & Warning Messages
# =============================================================================

def _build_default_clarify_message() -> str:
    """기본 명확화 메시지"""
    return (
        "죄송합니다. 현재 질문에 대해 충분한 근거를 찾지 못했습니다.\n"
        "다음과 같이 질문을 구체화해 주시면 더 정확한 답변을 드릴 수 있습니다:"
    )


def _build_warning_message() -> str:
    """LOW 등급 경고 메시지"""
    return (
        "※ 참고: 관련 문서가 제한적이어서 답변의 정확도가 낮을 수 있습니다. "
        "더 구체적인 질문을 해주시면 정확한 답변을 드릴 수 있습니다."
    )


def _build_generic_suggestions() -> List[str]:
    """
    범용 구체화 질문 예시

    도메인 구분 없이 사용 가능한 일반적인 구체화 힌트
    """
    return [
        "어떤 절차나 규정에 대해 알고 싶으신가요?",
        "특정 조건이나 예외 사항이 궁금하신가요?",
        "신청 방법이나 기한에 대해 알고 싶으신가요?",
        "구체적인 사례나 상황을 말씀해 주시겠어요?",
    ]


def build_clarification_suggestions(
    query: str,
    domain: str,
) -> List[str]:
    """
    쿼리와 도메인에 맞는 구체화 질문 예시를 생성합니다.

    Phase 58 1차: Rule-based (키워드 매칭)
    향후 2차: LLM 기반 동적 생성 가능

    Args:
        query: 원본 쿼리
        domain: 검색 도메인 (POLICY, EDU 등)

    Returns:
        구체화 질문 예시 리스트 (2~4개)
    """
    suggestions = []
    query_lower = query.lower()

    # Domain-specific suggestions
    if domain == "POLICY" or domain == "HR":
        if "연차" in query_lower or "휴가" in query_lower:
            suggestions = [
                "연차 발생 기준이나 잔여일수가 궁금하신가요?",
                "휴가 신청 방법이나 승인 절차를 알고 싶으신가요?",
                "특정 휴가 종류(병가, 경조사 등)에 대해 알고 싶으신가요?",
            ]
        elif "급여" in query_lower or "월급" in query_lower:
            suggestions = [
                "급여 지급일이나 명세서 조회 방법이 궁금하신가요?",
                "급여 계산 방식이나 공제 항목을 알고 싶으신가요?",
                "상여금이나 인센티브 관련 규정이 궁금하신가요?",
            ]
        elif "출장" in query_lower:
            suggestions = [
                "출장 신청 절차가 궁금하신가요?",
                "출장 비용 정산 방법을 알고 싶으신가요?",
                "해외출장 관련 규정이 궁금하신가요?",
            ]

    elif domain == "SECURITY":
        if "비밀번호" in query_lower or "패스워드" in query_lower:
            suggestions = [
                "비밀번호 변경 방법이 궁금하신가요?",
                "비밀번호 규칙(길이, 복잡도)을 알고 싶으신가요?",
                "비밀번호 초기화 절차가 필요하신가요?",
            ]
        elif "보안" in query_lower:
            suggestions = [
                "보안 사고 신고 절차가 궁금하신가요?",
                "보안 교육 이수 관련 정보가 필요하신가요?",
                "특정 보안 정책(USB 사용, 자료 반출 등)이 궁금하신가요?",
            ]

    elif domain == "EDU" or domain == "EDUCATION":
        suggestions = [
            "특정 교육 과정의 이수 기한이 궁금하신가요?",
            "교육 신청 방법이나 수료증 발급이 필요하신가요?",
            "필수 교육 vs 선택 교육 구분이 궁금하신가요?",
        ]

    # Fallback to generic suggestions if empty
    if not suggestions:
        suggestions = _build_generic_suggestions()

    return suggestions[:4]  # 최대 4개


def build_clarification_response(
    decision: QualityGateDecision,
    query: str,
    domain: str,
) -> str:
    """
    INSUFFICIENT 판정 시 사용자에게 반환할 전체 응답을 생성합니다.

    Args:
        decision: QualityGateDecision (INSUFFICIENT 등급)
        query: 원본 쿼리
        domain: 검색 도메인

    Returns:
        사용자에게 반환할 응답 문자열
    """
    # 도메인 기반 suggestions 생성
    suggestions = build_clarification_suggestions(query, domain)

    # 응답 구성
    response_parts = [
        decision.clarify_message or _build_default_clarify_message(),
        "",  # 빈 줄
    ]

    for i, suggestion in enumerate(suggestions, 1):
        response_parts.append(f"  {i}. {suggestion}")

    return "\n".join(response_parts)


# =============================================================================
# Logging Helper
# =============================================================================

def log_quality_gate_decision(
    decision: QualityGateDecision,
    query: str,
    domain: str,
) -> None:
    """품질 게이트 판정 결과를 로깅합니다."""
    # ASCII-safe query preview
    query_preview = query[:30].encode("unicode_escape").decode("ascii")

    logger.info(
        f"[QualityGate] grade={decision.grade.value} | "
        f"action={decision.action.value} | "
        f"min_l2={decision.min_l2_distance:.3f} | "
        f"sources={decision.sources_count} | "
        f"domain={domain} | "
        f"query='{query_preview}...'"
    )
