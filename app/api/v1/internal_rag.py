"""
Internal RAG API Endpoints (Phase 42 - A안 확정으로 Deprecated)

Phase 42 (A안 확정):
- Direct Milvus 인덱싱 파이프라인 완전 제거
- 문서 인덱싱/삭제는 SourceSet Orchestrator → RAGFlow 경로만 사용
- 이 라우터의 모든 엔드포인트는 Deprecated (410 Gone)

Deprecated Endpoints:
- POST /internal/rag/index: 제거됨
- POST /internal/rag/delete: 제거됨
- GET /internal/jobs/{job_id}: 제거됨

대안:
- POST /internal/ai/source-sets/{sourceSetId}/start
"""

from fastapi import APIRouter, HTTPException, status

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["Internal RAG (Deprecated)"])


# =============================================================================
# 모든 엔드포인트 Deprecated - 410 Gone
# =============================================================================

DEPRECATED_MESSAGE = {
    "error": "ENDPOINT_REMOVED",
    "message": "Phase 42 (A안 확정)에 따라 Direct Milvus 인덱싱 파이프라인이 제거되었습니다. "
               "문서 인덱싱은 SourceSet Orchestrator → RAGFlow 경로를 사용하세요.",
    "alternative": "/internal/ai/source-sets/{sourceSetId}/start",
}


@router.post(
    "/rag/index",
    status_code=status.HTTP_410_GONE,
    summary="[REMOVED] 문서 인덱싱 요청",
    responses={410: {"description": "엔드포인트 제거됨"}},
)
async def index_document():
    """[REMOVED] Direct Milvus 인덱싱 제거됨."""
    logger.warning("Removed endpoint called: /internal/rag/index")
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=DEPRECATED_MESSAGE)


@router.post(
    "/rag/delete",
    status_code=status.HTTP_410_GONE,
    summary="[REMOVED] 문서 삭제 요청",
    responses={410: {"description": "엔드포인트 제거됨"}},
)
async def delete_document():
    """[REMOVED] Direct Milvus 삭제 제거됨."""
    logger.warning("Removed endpoint called: /internal/rag/delete")
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=DEPRECATED_MESSAGE)


@router.get(
    "/jobs/{job_id}",
    status_code=status.HTTP_410_GONE,
    summary="[REMOVED] 작업 상태 조회",
    responses={410: {"description": "엔드포인트 제거됨"}},
)
async def get_job_status(job_id: str):
    """[REMOVED] Direct 인덱싱 작업 상태 조회 제거됨."""
    logger.warning(f"Removed endpoint called: /internal/jobs/{job_id}")
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=DEPRECATED_MESSAGE)
