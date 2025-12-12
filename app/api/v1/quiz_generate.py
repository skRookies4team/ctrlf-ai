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

    1. **1차 응시**: 새로운 퀴즈 세트 생성 (attempt_no=1)
    2. **2차 응시**: 기존 문항 중복 방지 (attempt_no=2, exclude_previous_questions 사용)

    ## 요청 예시

    ```json
    {
      "educationId": "EDU-SEC-2025-001",
      "docId": "DOC-SEC-001",
      "docVersion": "v1",
      "attemptNo": 1,
      "language": "ko",
      "numQuestions": 10,
      "difficultyDistribution": {
        "easy": 5,
        "normal": 3,
        "hard": 2
      },
      "questionType": "MCQ_SINGLE",
      "maxOptions": 4,
      "quizCandidateBlocks": [
        {
          "blockId": "BLOCK-001",
          "chapterId": "CH1",
          "learningObjectiveId": "LO-1",
          "text": "USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인을 받아야 한다.",
          "tags": ["USB", "반출", "승인"]
        }
      ],
      "excludePreviousQuestions": []
    }
    ```

    ## 응답 예시

    ```json
    {
      "educationId": "EDU-SEC-2025-001",
      "docId": "DOC-SEC-001",
      "docVersion": "v1",
      "attemptNo": 1,
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
          "explanation": "USB 반출 시에는 반드시 정보보호팀의 사전 승인을 받아야 합니다."
        }
      ]
    }
    ```

    ## TODO: 인증/권한
    - 이 엔드포인트는 내부 백엔드/관리자 전용입니다.
    - IP 제한 또는 헤더 토큰 기반 인증을 추가할 예정입니다.

    ## TODO: Phase 17 예정
    - LLM Self-check 기반 고급 QC 파이프라인
    - RAG 재검증을 통한 정답 검증
    - 문장 유사도(embedding) 기반 중복 제거
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
            - education_id: 교육/코스 식별자
            - doc_id, doc_version: 사규/교육 문서 ID 및 버전
            - attempt_no: 응시 차수 (1=1차 응시, 2=2차 응시 등)
            - num_questions: 생성할 문항 수
            - difficulty_distribution: 난이도별 문항 수 분배
            - quiz_candidate_blocks: 퀴즈 생성에 사용할 텍스트 블록 목록
            - exclude_previous_questions: 2차 응시 시 제외할 기존 문항 목록

    Returns:
        QuizGenerateResponse: 생성된 퀴즈 문항들
            - generated_count: 생성된 문항 수
            - questions: 생성된 퀴즈 문항 목록
    """
    logger.info(
        f"Quiz generate request: education_id={request.education_id}, "
        f"doc_id={request.doc_id}, num_questions={request.num_questions}, "
        f"attempt_no={request.attempt_no}, blocks_count={len(request.quiz_candidate_blocks)}"
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
