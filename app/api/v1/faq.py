"""
FAQ API 엔드포인트 (Phase 18 + Phase 20)

FAQ 초안 생성 API를 제공합니다.
백엔드에서 FAQ 후보 클러스터 정보를 전달하면,
RAG + LLM을 사용하여 FAQ 초안을 생성합니다.

엔드포인트:
    POST /ai/faq/generate - FAQ 초안 생성 (단건)
    POST /ai/faq/generate/batch - FAQ 초안 배치 생성 (Phase 20-AI-2)
"""

import asyncio
from typing import List

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.faq import (
    FaqDraftGenerateBatchRequest,
    FaqDraftGenerateBatchResponse,
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
            - avg_intent_confidence: 평균 의도 신뢰도 (선택, 최소 0.7 필요)

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

    # 의도 신뢰도 검증 (설정에 따라 선택적 검증)
    settings = get_settings()
    if request.avg_intent_confidence is not None:
        threshold = settings.FAQ_INTENT_CONFIDENCE_THRESHOLD
        if request.avg_intent_confidence < threshold:
            if settings.FAQ_INTENT_CONFIDENCE_REQUIRED:
                # 검증 필수 모드: 차단
                error_msg = (
                    f"의도 신뢰도가 부족합니다. "
                    f"(현재: {request.avg_intent_confidence}, 최소 요구: {threshold})"
                )
                logger.warning(
                    f"FAQ generation blocked: {error_msg}, "
                    f"cluster_id={request.cluster_id}"
                )
                return FaqDraftGenerateResponse(
                    status="FAILED",
                    faq_draft=None,
                    error_message=error_msg,
                )
            else:
                # 경고만 출력하고 계속 진행
                logger.warning(
                    f"Low intent confidence detected: {request.avg_intent_confidence} < {threshold}, "
                    f"but continuing (cluster_id={request.cluster_id})"
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


# =============================================================================
# Phase 20-AI-2: 배치 FAQ 생성 엔드포인트
# =============================================================================


async def _process_single_item(
    service: FaqDraftService,
    request: FaqDraftGenerateRequest,
    semaphore: asyncio.Semaphore,
) -> FaqDraftGenerateResponse:
    """
    단일 FAQ 요청을 처리합니다 (배치 내부용).

    세마포어로 동시성을 제한하고, 예외를 개별적으로 처리합니다.
    """
    async with semaphore:
        # 의도 신뢰도 검증 (설정에 따라 선택적 검증)
        settings = get_settings()
        if request.avg_intent_confidence is not None:
            threshold = settings.FAQ_INTENT_CONFIDENCE_THRESHOLD
            if request.avg_intent_confidence < threshold:
                if settings.FAQ_INTENT_CONFIDENCE_REQUIRED:
                    # 검증 필수 모드: 차단
                    error_msg = (
                        f"의도 신뢰도가 부족합니다. "
                        f"(현재: {request.avg_intent_confidence}, 최소 요구: {threshold})"
                    )
                    logger.warning(
                        f"FAQ batch item blocked: {error_msg}, "
                        f"cluster_id={request.cluster_id}"
                    )
                    return FaqDraftGenerateResponse(
                        status="FAILED",
                        faq_draft=None,
                        error_message=error_msg,
                    )
                else:
                    # 경고만 출력하고 계속 진행
                    logger.debug(
                        f"Low intent confidence detected: {request.avg_intent_confidence} < {threshold}, "
                        f"but continuing (cluster_id={request.cluster_id})"
                    )

        try:
            draft = await service.generate_faq_draft(request)
            return FaqDraftGenerateResponse(
                status="SUCCESS",
                faq_draft=draft,
                error_message=None,
            )
        except FaqGenerationError as e:
            logger.warning(
                f"FAQ batch item failed: cluster_id={request.cluster_id}, error={e}"
            )
            return FaqDraftGenerateResponse(
                status="FAILED",
                faq_draft=None,
                error_message=str(e),
            )
        except Exception as e:
            logger.exception(
                f"FAQ batch item unexpected error: cluster_id={request.cluster_id}"
            )
            return FaqDraftGenerateResponse(
                status="FAILED",
                faq_draft=None,
                error_message=f"예기치 않은 오류: {type(e).__name__}: {str(e)}",
            )


@router.post(
    "/generate/batch",
    response_model=FaqDraftGenerateBatchResponse,
    summary="FAQ 초안 배치 생성 (Phase 20)",
    description="다수의 FAQ 클러스터를 한 번에 생성합니다. 각 항목은 독립적으로 처리됩니다.",
    responses={
        200: {
            "description": "배치 FAQ 초안 생성 결과",
            "content": {
                "application/json": {
                    "examples": {
                        "mixed_results": {
                            "summary": "일부 성공/일부 실패",
                            "value": {
                                "items": [
                                    {
                                        "status": "SUCCESS",
                                        "faq_draft": {
                                            "faq_draft_id": "FAQ-cluster-001-a1b2c3d4",
                                            "domain": "SEC_POLICY",
                                            "cluster_id": "cluster-001",
                                            "question": "USB 반출 절차는?",
                                            "answer_markdown": "정보보호팀 승인 필요",
                                            "answer_source": "RAGFLOW",
                                            "ai_confidence": 0.85,
                                            "created_at": "2025-12-16T10:00:00Z",
                                        },
                                        "error_message": None,
                                    },
                                    {
                                        "status": "FAILED",
                                        "faq_draft": None,
                                        "error_message": "PII_DETECTED",
                                    },
                                ],
                                "total_count": 2,
                                "success_count": 1,
                                "failed_count": 1,
                            },
                        },
                    }
                }
            },
        },
    },
)
async def generate_faq_draft_batch(
    request: FaqDraftGenerateBatchRequest,
) -> FaqDraftGenerateBatchResponse:
    """
    배치로 FAQ 초안을 생성합니다 (Phase 20-AI-2).

    Args:
        request: 배치 FAQ 초안 생성 요청
            - items: FAQ 초안 생성 요청 리스트
            - concurrency: 동시 처리 수 (선택)

    Returns:
        FaqDraftGenerateBatchResponse: 배치 생성 결과
            - items: 요청 순서대로 응답 리스트
            - total_count: 전체 요청 수
            - success_count: 성공한 요청 수
            - failed_count: 실패한 요청 수
    """
    settings = get_settings()
    concurrency = request.concurrency or settings.FAQ_BATCH_CONCURRENCY

    logger.info(
        f"FAQ batch generate request: item_count={len(request.items)}, "
        f"concurrency={concurrency}"
    )

    service = get_faq_service()
    semaphore = asyncio.Semaphore(concurrency)

    # 모든 항목을 병렬로 처리 (순서 유지)
    tasks = [
        _process_single_item(service, item, semaphore)
        for item in request.items
    ]
    results: List[FaqDraftGenerateResponse] = await asyncio.gather(*tasks)

    # 통계 계산
    success_count = sum(1 for r in results if r.status == "SUCCESS")
    failed_count = len(results) - success_count

    logger.info(
        f"FAQ batch generate completed: total={len(results)}, "
        f"success={success_count}, failed={failed_count}"
    )

    return FaqDraftGenerateBatchResponse(
        items=results,
        total_count=len(results),
        success_count=success_count,
        failed_count=failed_count,
    )
