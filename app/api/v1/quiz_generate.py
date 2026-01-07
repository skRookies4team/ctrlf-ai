"""
Quiz Generate API (Phase 16)

교육/사규 문서의 QUIZ_CANDIDATE 블록들을 입력받아
LLM을 통해 객관식 퀴즈를 자동 생성하는 API.

엔드포인트:
- POST /ai/quiz/generate
"""

from fastapi import APIRouter, HTTPException, status

from app.core.logging import get_logger
from app.models.quiz_generate import (
    QuizGenerateRequest,
    QuizGenerateResponse,
)
from app.services.quiz_generate_service import QuizGenerateService

logger = get_logger(__name__)

# ✅ 백엔드 명세에 맞게 prefix 수정
router = APIRouter(prefix="/quiz", tags=["Quiz Generate"])


# =============================================================================
# Quiz Generate Service (Singleton)
# =============================================================================

_quiz_generate_service: QuizGenerateService | None = None


def get_quiz_generate_service() -> QuizGenerateService:
    """
    QuizGenerateService 싱글톤 인스턴스 반환
    """
    global _quiz_generate_service
    if _quiz_generate_service is None:
        _quiz_generate_service = QuizGenerateService()
    return _quiz_generate_service


# =============================================================================
# API Endpoint
# =============================================================================

@router.post(
    "/generate",
    response_model=QuizGenerateResponse,
    summary="퀴즈 자동 생성",
    description="""
교육/사규 문서의 QUIZ_CANDIDATE 블록들을 입력받아
객관식 퀴즈를 자동 생성합니다.

이 API는 내부 백엔드(ctrlf-back) 전용 API입니다.

### 주요 특징
- 1차 응시: 신규 문항 생성
- 2차 응시: excludePreviousQuestions 기반 중복 방지
- 난이도 분배:
  - EASY 50%
  - NORMAL 30%
  - HARD 20%
""",
    responses={
        200: {"description": "퀴즈 생성 성공"},
        400: {"description": "잘못된 요청"},
        422: {"description": "유효성 검증 실패"},
        500: {"description": "서버 내부 오류"},
    },
)
async def generate_quiz(
    request: QuizGenerateRequest,
) -> QuizGenerateResponse:
    """
    퀴즈 자동 생성 API

    Args:
        request (QuizGenerateRequest):
            - language: "ko"
            - numQuestions: 생성 문항 수
            - maxOptions: 보기 수
            - quizCandidateBlocks: 퀴즈 후보 블록 목록
            - excludePreviousQuestions: 이전 문항 제외 목록

    Returns:
        QuizGenerateResponse:
            - generatedCount
            - questions[]
    """

    logger.info(
        "Quiz generate request received | "
        f"num_questions={request.num_questions}, "
        f"blocks={len(request.quiz_candidate_blocks)}"
    )

    try:
        service = get_quiz_generate_service()
        response = await service.generate_quiz(request)

        logger.info(
            "Quiz generate completed | "
            f"generated_count={response.generated_count}"
        )

        return response

    except ValueError as e:
        logger.warning(f"Invalid quiz generate request: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except Exception as e:
        logger.exception("Quiz generate failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate quiz: {str(e)}",
        )
