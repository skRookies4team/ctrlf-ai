"""
FAQ API 엔드포인트 (Phase 18)

FAQ 초안 생성 API를 제공합니다.
백엔드에서 FAQ 후보 클러스터 정보를 전달하면,
RAG + LLM을 사용하여 FAQ 초안을 생성합니다.

엔드포인트:
    POST /ai/faq/generate - FAQ 초안 생성
"""

from fastapi import APIRouter

from app.core.logging import get_logger
from app.models.faq import (
    FaqDraftGenerateRequest,
    FaqDraftGenerateResponse,
)
from app.services.faq_service import FaqDraftService, FaqGenerationError

logger = get_logger(__name__)

router = APIRouter(prefix="/faq", tags=["FAQ"])

# 서비스 인스턴스 (lazy initialization)
_faq_service: FaqDraftService | None = None


def get_faq_service() -> FaqDraftService:
    """FaqDraftService 인스턴스를 반환합니다 (싱글턴)."""
    global _faq_service
    if _faq_service is None:
        _faq_service = FaqDraftService()
    return _faq_service


@router.post(
    "/generate",
    response_model=FaqDraftGenerateResponse,
    summary="FAQ 초안 생성",
    description="FAQ 후보 클러스터를 기반으로 RAG + LLM을 사용하여 FAQ 초안을 생성합니다.",
    responses={
        200: {
            "description": "FAQ 초안 생성 결과",
            "content": {
                "application/json": {
                    "examples": {
                        "success": {
                            "summary": "성공",
                            "value": {
                                "status": "SUCCESS",
                                "faq_draft": {
                                    "faq_draft_id": "FAQ-cluster-001-a1b2c3d4",
                                    "domain": "SEC_POLICY",
                                    "cluster_id": "cluster-001",
                                    "question": "USB 메모리 반출 시 어떤 절차가 필요한가요?",
                                    "answer_markdown": "**정보보호팀의 사전 승인이 필요합니다.**\n\n- USB 반출 신청서 작성\n- 정보보호팀 승인 요청\n- 승인 후 반출 가능",
                                    "summary": "정보보호팀의 사전 승인이 필요합니다.",
                                    "source_doc_id": "DOC-SEC-001",
                                    "source_article_label": "제3장 제2조",
                                    "answer_source": "AI_RAG",
                                    "ai_confidence": 0.85,
                                    "created_at": "2025-12-12T10:00:00Z",
                                },
                                "error_message": None,
                            },
                        },
                        "failed": {
                            "summary": "실패",
                            "value": {
                                "status": "FAILED",
                                "faq_draft": None,
                                "error_message": "LLM 응답 파싱 실패: JSON 형식 오류",
                            },
                        },
                    }
                }
            },
        },
    },
)
async def generate_faq_draft(
    request: FaqDraftGenerateRequest,
) -> FaqDraftGenerateResponse:
    """
    FAQ 초안을 생성합니다.

    Args:
        request: FAQ 초안 생성 요청
            - domain: 도메인 (예: SEC_POLICY, PII_PRIVACY)
            - cluster_id: FAQ 후보 클러스터 ID
            - canonical_question: 대표 질문
            - sample_questions: 실제 직원 질문 예시들 (선택)
            - top_docs: RAG에서 뽑아온 후보 문서들 (선택)

    Returns:
        FaqDraftGenerateResponse: 생성 결과
            - status: SUCCESS 또는 FAILED
            - faq_draft: 생성된 FAQ 초안 (성공 시)
            - error_message: 에러 메시지 (실패 시)
    """
    logger.info(
        f"FAQ generate request: domain={request.domain}, "
        f"cluster_id={request.cluster_id}"
    )

    service = get_faq_service()

    try:
        draft = await service.generate_faq_draft(request)

        logger.info(f"FAQ draft generated successfully: id={draft.faq_draft_id}")

        return FaqDraftGenerateResponse(
            status="SUCCESS",
            faq_draft=draft,
            error_message=None,
        )

    except FaqGenerationError as e:
        logger.warning(f"FAQ generation failed: {e}")
        return FaqDraftGenerateResponse(
            status="FAILED",
            faq_draft=None,
            error_message=str(e),
        )

    except Exception as e:
        logger.exception(f"Unexpected error in FAQ generation: {e}")
        return FaqDraftGenerateResponse(
            status="FAILED",
            faq_draft=None,
            error_message=f"예기치 않은 오류: {type(e).__name__}: {str(e)}",
        )
