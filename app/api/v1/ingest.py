"""
문서 인덱싱 API 엔드포인트 (Phase 19)

AI Gateway 문서 인덱싱 API를 제공합니다.
Spring 백엔드에서 문서를 업로드한 후, 이 엔드포인트를 통해 RAGFlow에 인덱싱을 요청합니다.

엔드포인트:
    POST /ingest - 문서 인덱싱 요청

사용 예시:
    curl -X POST http://localhost:8000/ingest \\
      -H "Content-Type: application/json" \\
      -d '{
        "doc_id": "DOC-2025-00123",
        "source_type": "policy",
        "storage_url": "https://files.internal/documents/DOC-2025-00123.pdf",
        "file_name": "정보보안규정_v3.pdf",
        "mime_type": "application/pdf"
      }'
"""

from fastapi import APIRouter, HTTPException, status

from app.core.exceptions import UpstreamServiceError
from app.core.logging import get_logger
from app.models.ingest import IngestRequest, IngestResponse
from app.services.ingest_service import IngestService, SourceTypeNotFoundError

logger = get_logger(__name__)

router = APIRouter()

# 서비스 인스턴스 (lazy initialization)
_ingest_service: IngestService | None = None


def get_ingest_service() -> IngestService:
    """IngestService 인스턴스를 반환합니다 (싱글턴)."""
    global _ingest_service
    if _ingest_service is None:
        _ingest_service = IngestService()
    return _ingest_service


@router.post(
    "/ingest",
    response_model=IngestResponse,
    summary="문서 인덱싱",
    description="RAGFlow를 통해 문서를 인덱싱합니다.",
    responses={
        200: {
            "description": "인덱싱 요청 성공",
            "content": {
                "application/json": {
                    "example": {
                        "task_id": "ingest-2025-000001",
                        "status": "DONE",
                    }
                }
            },
        },
        400: {
            "description": "잘못된 요청 (source_type 없음 등)",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Source type 'unknown' not found. Available: policy, training, incident, education"
                    }
                }
            },
        },
        502: {
            "description": "RAGFlow 서비스 오류 (타임아웃, 5xx 등)",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "RAGFlow service error: timeout after 10s"
                    }
                }
            },
        },
    },
)
async def ingest_document(request: IngestRequest) -> IngestResponse:
    """
    문서를 RAGFlow에 인덱싱합니다.

    Args:
        request: 인덱싱 요청
            - doc_id: 백엔드 문서 ID (필수)
            - source_type: 문서 유형 (필수, 예: "policy")
            - storage_url: 파일 다운로드 URL (필수)
            - file_name: 파일명 (필수)
            - mime_type: MIME 타입 (필수)
            - department: 부서 (선택)
            - acl: 접근 제어 리스트 (선택)
            - tags: 태그 리스트 (선택)
            - version: 버전 (선택, 기본값: 1)

    Returns:
        IngestResponse: 인덱싱 결과
            - task_id: 작업 ID
            - status: 상태 (DONE, QUEUED, PROCESSING, FAILED)

    Raises:
        HTTPException 400: 알 수 없는 source_type
        HTTPException 502: RAGFlow 서비스 오류
    """
    logger.info(
        f"Ingest request: doc_id={request.doc_id}, "
        f"source_type={request.source_type}, file_name={request.file_name}"
    )

    service = get_ingest_service()

    try:
        response = await service.ingest(request)
        logger.info(
            f"Ingest completed: doc_id={request.doc_id}, "
            f"task_id={response.task_id}, status={response.status}"
        )
        return response

    except SourceTypeNotFoundError as e:
        logger.warning(f"Source type not found: {e.source_type}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except UpstreamServiceError as e:
        logger.error(f"RAGFlow service error: {e.message}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"RAGFlow service error: {e.message}",
        )

    except Exception as e:
        logger.exception("Unexpected error in ingest endpoint")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during ingest",
        )
