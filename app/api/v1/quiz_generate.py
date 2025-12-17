"""
Quiz Generate API (Phase 16)

교육/사규 문서의 QUIZ_CANDIDATE 블록들을 입력받아
LLM을 통해 객관식 퀴즈를 자동 생성하는 API.

엔드포인트:
- POST /ai/quiz/generate: 퀴즈 자동 생성
"""

from fastapi import APIRouter, HTTPException, status

from app.core.logging import get_logger
from app.models.quiz_generate import (
    QuizGenerateRequest,
    QuizGenerateResponse,
)
from app.services.quiz_generate_service import QuizGenerateService

logger = get_logger(__name__)

router = APIRouter(prefix="/quiz", tags=["Quiz Generate"])


# =============================================================================
# Quiz Generate Service 인스턴스
# =============================================================================

_quiz_generate_service: QuizGenerateService | None = None


def get_quiz_generate_service() -> QuizGenerateService:
    """QuizGenerateService 싱글톤 인스턴스를 반환합니다."""
    global _quiz_generate_service
    if _quiz_generate_service is None:
        _quiz_generate_service = QuizGenerateService()
    return _quiz_generate_service


# =============================================================================
# API 엔드포인트
# =============================================================================


@router.post(
    "/generate",
    response_model=QuizGenerateResponse,
    summary="퀴즈 자동 생성",
    description="""
    교육/사규 문서의 QUIZ_CANDIDATE 블록들을 입력받아 객관식 퀴즈를 자동 생성합니다.

    이 API는 내부 백엔드/관리자용입니다.
    백엔드(ctrlf-back)에서 교육 문서의 퀴즈 후보 블록들을 보내면,
    AI가 객관식 문제/보기/정답/해설을 생성하여 반환합니다.

    ## 주요 기능

    1. **1차 응시**: 새로운 퀴즈 세트 생성
    2. **2차 응시**: 기존 문항 중복 방지 (excludePreviousQuestions 사용)

    ## 난이도 분배

    AI 서버가 고정 비율로 자동 결정합니다:
    - 쉬움(EASY): 50%
    - 보통(NORMAL): 30%
    - 어려움(HARD): 20%

    ## 요청 예시

    ```json
    {
      "language": "ko",
      "numQuestions": 10,
      "maxOptions": 4,
      "quizCandidateBlocks": [
        {
          "blockId": "BLOCK-001",
          "docId": "DOC-SEC-001",
          "docVersion": "v1",
          "chapterId": "CH1",
          "learningObjectiveId": "LO-1",
          "text": "USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인을 받아야 한다.",
          "tags": ["USB", "반출", "승인"],
          "articlePath": "제3장 > 제2조 > 제1항"
        }
      ],
      "excludePreviousQuestions": []
    }
    ```

    ## 응답 예시

    ```json
    {
      "generatedCount": 10,
      "questions": [
        {
          "questionId": "Q-20251212-ABCD1234",
          "status": "DRAFT_AI_GENERATED",
          "questionType": "MCQ_SINGLE",
          "stem": "USB 메모리를 사외로 반출할 때 필요한 조치는?",
          "options": [
            {"optionId": "OPT-1", "text": "정보보호팀의 사전 승인", "isCorrect": true},
            {"optionId": "OPT-2", "text": "팀장에게 구두 보고", "isCorrect": false},
            {"optionId": "OPT-3", "text": "자유롭게 반출", "isCorrect": false},
            {"optionId": "OPT-4", "text": "사후 보고", "isCorrect": false}
          ],
          "difficulty": "EASY",
          "sourceDocId": "DOC-SEC-001",
          "sourceDocVersion": "v1",
          "sourceArticlePath": "제3장 > 제2조 > 제1항",
          "explanation": "USB 반출 시에는 반드시 정보보호팀의 사전 승인을 받아야 합니다."
        }
      ]
    }
    ```

    ## TODO: 인증/권한
    - 이 엔드포인트는 내부 백엔드/관리자 전용입니다.
    - IP 제한 또는 헤더 토큰 기반 인증을 추가할 예정입니다.
    """,
    responses={
        200: {
            "description": "퀴즈 생성 성공",
            "model": QuizGenerateResponse,
        },
        400: {
            "description": "잘못된 요청 (빈 블록 목록 등)",
        },
        422: {
            "description": "유효성 검증 실패",
        },
        500: {
            "description": "서버 내부 오류",
        },
    },
)
async def generate_quiz(
    request: QuizGenerateRequest,
) -> QuizGenerateResponse:
    """
    교육/사규 문서에서 객관식 퀴즈를 자동 생성합니다.

    Args:
        request: 퀴즈 생성 요청
            - num_questions: 생성할 문항 수
            - language: 퀴즈 언어 (기본: ko)
            - max_options: 보기 개수 (기본: 4)
            - quiz_candidate_blocks: 퀴즈 생성에 사용할 텍스트 블록 목록
            - exclude_previous_questions: 2차 응시 시 제외할 기존 문항 목록

    Returns:
        QuizGenerateResponse: 생성된 퀴즈 문항들
            - generated_count: 생성된 문항 수
            - questions: 생성된 퀴즈 문항 목록
    """
    logger.info(
        f"Quiz generate request: num_questions={request.num_questions}, "
        f"blocks_count={len(request.quiz_candidate_blocks)}"
    )

    try:
        service = get_quiz_generate_service()
        response = await service.generate_quiz(request)

        logger.info(
            f"Quiz generate response: generated_count={response.generated_count}"
        )

        return response

    except ValueError as e:
        logger.warning(f"Invalid request: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.exception(f"Failed to generate quiz: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate quiz: {str(e)}",
        )
