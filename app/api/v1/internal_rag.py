"""
Internal RAG API Endpoints (Phase 25)

백엔드에서 AI 서버로 직접 RAG 인덱싱/삭제 요청을 보내기 위한 내부용 API입니다.
RAGFlow를 우회하고 AI 서버가 Milvus에 직접 upsert/delete/search를 수행합니다.

Endpoints:
- POST /internal/rag/index: 문서 인덱싱 요청
- POST /internal/rag/delete: 문서 삭제 요청
- GET /internal/jobs/{job_id}: 작업 상태 조회
"""

from fastapi import APIRouter, HTTPException, status

from app.core.logging import get_logger
from app.models.internal_rag import (
    InternalRagIndexRequest,
    InternalRagIndexResponse,
    InternalRagDeleteRequest,
    InternalRagDeleteResponse,
    JobStatusResponse,
)
from app.services.indexing_service import get_indexing_service
from app.services.job_service import get_job_service

logger = get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["Internal RAG"])


# =============================================================================
# POST /internal/rag/index
# =============================================================================


@router.post(
    "/rag/index",
    response_model=InternalRagIndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="문서 인덱싱 요청",
    description="""
    문서를 다운로드하여 텍스트 추출, 청킹, 임베딩 생성 후 Milvus에 저장합니다.

    처리 흐름:
    1. fileUrl에서 파일 다운로드
    2. 텍스트 추출 및 청킹
    3. 임베딩 생성
    4. Milvus에 upsert
    5. 새 버전 성공 시 이전 버전 삭제

    작업은 비동기로 처리되며, GET /internal/jobs/{jobId}로 상태를 폴링할 수 있습니다.
    """,
    responses={
        202: {"description": "인덱싱 작업이 큐에 등록됨"},
        400: {"description": "잘못된 요청"},
        500: {"description": "서버 오류"},
    },
)
async def index_document(
    request: InternalRagIndexRequest,
) -> InternalRagIndexResponse:
    """
    문서 인덱싱을 요청합니다.

    Args:
        request: 인덱싱 요청 (documentId, versionNo, domain, fileUrl, jobId 등)

    Returns:
        InternalRagIndexResponse: 작업 ID와 초기 상태
    """
    logger.info(
        f"Received index request: job_id={request.job_id}, "
        f"document_id={request.document_id}, version_no={request.version_no}"
    )

    try:
        indexing_service = get_indexing_service()
        response = await indexing_service.index_document(request)

        logger.info(f"Index request accepted: job_id={request.job_id}")
        return response

    except Exception as e:
        logger.exception(f"Failed to process index request: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process index request: {str(e)}",
        )


# =============================================================================
# POST /internal/rag/delete
# =============================================================================


@router.post(
    "/rag/delete",
    response_model=InternalRagDeleteResponse,
    summary="문서 삭제 요청",
    description="""
    Milvus에서 문서 청크를 삭제합니다.

    - versionNo가 주어지면 해당 버전만 삭제
    - versionNo가 없으면 해당 문서의 모든 버전 삭제

    삭제는 동기적으로 처리되며 즉시 결과를 반환합니다.
    """,
    responses={
        200: {"description": "삭제 완료"},
        400: {"description": "잘못된 요청"},
        500: {"description": "서버 오류"},
    },
)
async def delete_document(
    request: InternalRagDeleteRequest,
) -> InternalRagDeleteResponse:
    """
    문서를 Milvus에서 삭제합니다.

    Args:
        request: 삭제 요청 (documentId, versionNo 선택)

    Returns:
        InternalRagDeleteResponse: 삭제 결과
    """
    logger.info(
        f"Received delete request: document_id={request.document_id}, "
        f"version_no={request.version_no}"
    )

    try:
        indexing_service = get_indexing_service()
        response = await indexing_service.delete_document(request)

        logger.info(
            f"Delete completed: document_id={request.document_id}, "
            f"deleted_count={response.deleted_count}"
        )
        return response

    except Exception as e:
        logger.exception(f"Failed to process delete request: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process delete request: {str(e)}",
        )


# =============================================================================
# GET /internal/jobs/{job_id}
# =============================================================================


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="작업 상태 조회",
    description="""
    인덱싱/삭제 작업의 상태를 조회합니다.

    작업 상태:
    - queued: 대기 중
    - running: 실행 중
    - completed: 완료
    - failed: 실패 (errorMessage 포함)

    백엔드에서 작업 완료를 확인할 때까지 폴링할 수 있습니다.
    """,
    responses={
        200: {"description": "작업 상태"},
        404: {"description": "작업을 찾을 수 없음"},
    },
)
async def get_job_status(
    job_id: str,
) -> JobStatusResponse:
    """
    작업 상태를 조회합니다.

    Args:
        job_id: 작업 ID

    Returns:
        JobStatusResponse: 작업 상태
    """
    job_service = get_job_service()
    status_response = await job_service.get_job_status(job_id)

    if status_response is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    return status_response
