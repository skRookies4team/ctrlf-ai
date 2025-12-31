"""
Gap Suggestions API (Phase 15)

RAG Gap 질문들을 분석하여 사규/교육 보완 제안을 생성하는 관리자용 API.

엔드포인트:
- POST /ai/gap/policy-edu/suggestions: RAG Gap 보완 제안 생성
"""

from fastapi import APIRouter, HTTPException, status

from app.core.logging import get_logger
from app.models.gap_suggestion import (
    GapSuggestionRequest,
    GapSuggestionResponse,
)
from app.services.gap_suggestion_service import GapSuggestionService

logger = get_logger(__name__)

router = APIRouter(prefix="/gap", tags=["Gap Suggestions"])


# =============================================================================
# Gap Suggestion Service 인스턴스
# =============================================================================

_gap_suggestion_service: GapSuggestionService | None = None


def get_gap_suggestion_service() -> GapSuggestionService:
    """GapSuggestionService 싱글톤 인스턴스를 반환합니다."""
    global _gap_suggestion_service
    if _gap_suggestion_service is None:
        _gap_suggestion_service = GapSuggestionService()
    return _gap_suggestion_service


# =============================================================================
# API 엔드포인트
# =============================================================================


@router.post(
    "/policy-edu/suggestions",
    response_model=GapSuggestionResponse,
    summary="RAG Gap 보완 제안 생성",
    description="""
    RAG Gap으로 식별된 질문 목록을 분석하여 사규/교육 보완 제안을 생성합니다.

    이 API는 내부 관리자/백엔드 전용입니다.
    백엔드(ctrlf-back)에서 수집한 RAG Gap 질문들을 보내면,
    AI가 어떤 사규/교육 항목을 추가/보완하면 좋을지 제안합니다.

    ## 사용 예시

    ```json
    {
      "domain": "POLICY",
      "questions": [
        {
          "questionId": "log-123",
          "text": "재택근무할 때 VPN 안 쓰면 어떻게 되나요?",
          "userRole": "EMPLOYEE",
          "intent": "POLICY_QA",
          "domain": "POLICY",
          "askedCount": 5
        }
      ]
    }
    ```

    ## 응답 예시

    ```json
    {
      "summary": "재택근무 시 보안 규정에 대한 문서가 부족합니다.",
      "suggestions": [
        {
          "id": "SUG-001",
          "title": "재택근무 시 정보보호 수칙 상세 예시 추가",
          "description": "VPN 사용 의무 등을 포함한 조문을 신설하세요.",
          "relatedQuestionIds": ["log-123"],
          "priority": "HIGH"
        }
      ]
    }
    ```

    ## TODO: 인증/권한
    - 이 엔드포인트는 내부 관리자/백엔드 전용입니다.
    - IP 제한 또는 헤더 토큰 기반 인증을 추가할 예정입니다.
    """,
    responses={
        200: {
            "description": "보완 제안 생성 성공",
            "model": GapSuggestionResponse,
        },
        400: {
            "description": "잘못된 요청",
        },
        500: {
            "description": "서버 내부 오류",
        },
    },
)
async def generate_gap_suggestions(
    request: GapSuggestionRequest,
) -> GapSuggestionResponse:
    """
    RAG Gap 질문들을 분석하여 사규/교육 보완 제안을 생성합니다.

    Args:
        request: RAG Gap 보완 제안 요청
            - time_range: 분석 대상 기간 (선택)
            - domain: 대상 도메인 (POLICY, EDU 등)
            - grouping_key: 그룹핑 기준 (intent, keyword, role 등)
            - questions: RAG Gap 질문 목록

    Returns:
        GapSuggestionResponse: 분석 요약 및 보완 제안 목록
            - summary: 전체 분석 요약
            - suggestions: 보완 제안 항목들
    """
    logger.info(
        f"Gap suggestion request: domain={request.domain}, "
        f"questions_count={len(request.questions)}"
    )

    try:
        service = get_gap_suggestion_service()
        response = await service.generate_suggestions(request)

        logger.info(
            f"Gap suggestion response: suggestions_count={len(response.suggestions)}"
        )

        return response

    except Exception as e:
        logger.exception(f"Failed to generate gap suggestions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate suggestions: {str(e)}",
        )
