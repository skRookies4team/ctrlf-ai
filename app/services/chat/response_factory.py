"""
응답 생성 팩토리 (Response Factory)

ChatService에서 사용하는 응답 생성 함수들과 관련 상수를 제공합니다.

Phase 2 리팩토링:
- Fallback 응답 메시지 상수
- Router 응답 상수 (SYSTEM_HELP_RESPONSE, UNKNOWN_ROUTE_RESPONSE)
- 응답 생성 함수들 (create_fallback_response, create_router_response, etc.)
"""

import time
from typing import Optional

from app.models.chat import ChatAnswerMeta, ChatResponse
from app.models.intent import RouteType
from app.models.router_types import RouterRouteType, Tier0Intent
from app.services.router_orchestrator import OrchestrationResult


# =============================================================================
# Phase 12: Fallback 응답 메시지
# =============================================================================

# LLM 서비스 장애 시
LLM_FALLBACK_MESSAGE = (
    "죄송합니다. 현재 AI 서비스에 일시적인 문제가 발생했습니다. "
    "잠시 후 다시 시도해 주세요."
)

# Backend 데이터 조회 실패 시 (BACKEND_API 라우트)
BACKEND_FALLBACK_MESSAGE = (
    "현재 시스템에서 정보를 가져오는 데 문제가 발생했습니다. "
    "관리자 시스템에서 직접 확인하시거나, 잠시 후 다시 시도해 주세요."
)

# RAG 검색 실패 시 추가 안내
RAG_FAIL_NOTICE = (
    "\n\n※ 참고: 관련 문서 검색에 문제가 있어 일반적인 원칙만 안내드립니다. "
    "정확한 정책 정보는 담당 부서에 확인해 주세요."
)

# MIXED_BACKEND_RAG에서 Backend만 실패 시
MIXED_BACKEND_FAIL_NOTICE = (
    "\n\n※ 참고: 실제 현황 데이터를 조회하지 못했습니다. "
    "통계/현황 정보는 관리자 시스템에서 직접 확인해 주세요."
)


# =============================================================================
# Phase 22: Router Integration Constants
# =============================================================================

# 시스템 도움말 응답
SYSTEM_HELP_RESPONSE = """안녕하세요! CTRL+F AI 어시스턴트입니다.

제가 도와드릴 수 있는 것들:
- **사규/정책 질문**: 회사 규정, 보안 정책 등을 안내해 드립니다.
- **교육 관련**: 4대교육, 직무교육 내용을 설명해 드립니다.
- **HR 정보 조회**: 연차, 근태, 복지 등 개인 정보를 확인해 드립니다.
- **퀴즈**: 교육 퀴즈를 진행하거나 결과를 확인할 수 있습니다.

궁금한 점이 있으시면 편하게 질문해 주세요!"""

# UNKNOWN 라우트 응답
UNKNOWN_ROUTE_RESPONSE = (
    "죄송합니다. 질문을 이해하지 못했습니다. "
    "사규, 교육, HR 정보, 퀴즈 관련 질문을 해주시면 도움을 드릴 수 있습니다."
)


# =============================================================================
# 응답 생성 함수
# =============================================================================


def create_fallback_response(
    message: str,
    start_time: float,
    has_pii: bool = False,
) -> ChatResponse:
    """
    Create a fallback response for error cases.

    Args:
        message: Error or fallback message
        start_time: Request start time for latency calculation
        has_pii: Whether PII was detected

    Returns:
        ChatResponse with fallback answer
    """
    latency_ms = int((time.perf_counter() - start_time) * 1000)

    return ChatResponse(
        answer=message,
        sources=[],
        meta=ChatAnswerMeta(
            used_model=None,
            route=RouteType.FALLBACK.value,
            masked=has_pii,
            latency_ms=latency_ms,
            rag_used=False,
            rag_source_count=0,
        ),
    )


def create_router_response(
    orchestration_result: OrchestrationResult,
    start_time: float,
    has_pii: bool,
) -> ChatResponse:
    """
    RouterOrchestrator의 되묻기/확인 응답을 ChatResponse로 변환합니다.

    Args:
        orchestration_result: 오케스트레이션 결과
        start_time: 요청 시작 시간
        has_pii: PII 검출 여부

    Returns:
        ChatResponse: 되묻기/확인 응답
    """
    latency_ms = int((time.perf_counter() - start_time) * 1000)
    router_result = orchestration_result.router_result

    # Route 결정
    if router_result.needs_clarify:
        route_value = "CLARIFY"
    elif router_result.requires_confirmation:
        route_value = "CONFIRMATION"
    else:
        route_value = router_result.route_type.value

    return ChatResponse(
        answer=orchestration_result.response_message,
        sources=[],
        meta=ChatAnswerMeta(
            used_model=None,
            route=route_value,
            intent=router_result.tier0_intent.value,
            domain=router_result.domain.value if router_result.domain else "GENERAL",
            masked=has_pii,
            latency_ms=latency_ms,
            rag_used=False,
            rag_source_count=0,
        ),
    )


def create_system_help_response(
    start_time: float,
    has_pii: bool,
) -> ChatResponse:
    """
    시스템 도움말 응답을 생성합니다.

    Args:
        start_time: 요청 시작 시간
        has_pii: PII 검출 여부

    Returns:
        ChatResponse: 시스템 도움말 응답
    """
    latency_ms = int((time.perf_counter() - start_time) * 1000)

    return ChatResponse(
        answer=SYSTEM_HELP_RESPONSE,
        sources=[],
        meta=ChatAnswerMeta(
            used_model=None,
            route=RouterRouteType.ROUTE_SYSTEM_HELP.value,
            intent=Tier0Intent.SYSTEM_HELP.value,
            domain="GENERAL",
            masked=has_pii,
            latency_ms=latency_ms,
            rag_used=False,
            rag_source_count=0,
        ),
    )


def create_unknown_route_response(
    start_time: float,
    has_pii: bool,
) -> ChatResponse:
    """
    UNKNOWN 라우트 응답을 생성합니다.

    Args:
        start_time: 요청 시작 시간
        has_pii: PII 검출 여부

    Returns:
        ChatResponse: UNKNOWN 응답
    """
    latency_ms = int((time.perf_counter() - start_time) * 1000)

    return ChatResponse(
        answer=UNKNOWN_ROUTE_RESPONSE,
        sources=[],
        meta=ChatAnswerMeta(
            used_model=None,
            route=RouterRouteType.ROUTE_UNKNOWN.value,
            intent=Tier0Intent.UNKNOWN.value,
            domain="GENERAL",
            masked=has_pii,
            latency_ms=latency_ms,
            rag_used=False,
            rag_source_count=0,
        ),
    )
