"""
문서 인덱싱 서비스 모듈 (Phase 19)

AI Gateway 문서 인덱싱 API의 비즈니스 로직을 처리합니다.
RAGFlow의 /v1/chunk/ingest 엔드포인트와 연동하여 문서를 인덱싱합니다.

주요 기능:
- source_type 유효성 검증
- RAGFlow 인덱싱 API 호출
- 응답 정규화 및 에러 처리

사용 예시:
    from app.services.ingest_service import IngestService

    service = IngestService()
    response = await service.ingest(request)
"""

from typing import List, Optional

from app.clients.ragflow_client import RagflowClient
from app.core.logging import get_logger
from app.models.ingest import IngestRequest, IngestResponse

logger = get_logger(__name__)


class SourceTypeNotFoundError(Exception):
    """source_type이 매핑에 없을 때 발생하는 예외."""

    def __init__(self, source_type: str, available_types: List[str]):
        self.source_type = source_type
        self.available_types = available_types
        super().__init__(
            f"Source type '{source_type}' not found. "
            f"Available: {', '.join(available_types)}"
        )


class IngestService:
    """
    문서 인덱싱 서비스

    AI Gateway 인덱싱 API를 처리하고, RAGFlow와 연동합니다.

    Attributes:
        _ragflow: RagflowClient 인스턴스

    Example:
        service = IngestService()
        response = await service.ingest(IngestRequest(
            doc_id="DOC-001",
            source_type="policy",
            storage_url="https://files.internal/doc.pdf",
            file_name="규정.pdf",
            mime_type="application/pdf",
        ))
    """

    # 지원하는 source_type 목록
    SUPPORTED_SOURCE_TYPES: List[str] = ["policy", "training", "incident", "education"]

    def __init__(
        self,
        ragflow_client: Optional[RagflowClient] = None,
    ) -> None:
        """
        IngestService 초기화

        Args:
            ragflow_client: RagflowClient 인스턴스. None이면 새로 생성.
        """
        self._ragflow = ragflow_client or RagflowClient()

    def validate_source_type(self, source_type: str) -> None:
        """
        source_type 유효성을 검증합니다.

        Args:
            source_type: 문서 유형 (예: "policy", "training")

        Raises:
            SourceTypeNotFoundError: 유효하지 않은 source_type인 경우
        """
        source_type_lower = source_type.lower().strip()

        # RagflowClient의 매핑 테이블 확인
        if not self._ragflow.is_valid_source_type(source_type):
            raise SourceTypeNotFoundError(
                source_type=source_type,
                available_types=self.SUPPORTED_SOURCE_TYPES,
            )

    async def ingest(self, request: IngestRequest) -> IngestResponse:
        """
        문서를 RAGFlow에 인덱싱합니다.

        Args:
            request: 인덱싱 요청 DTO

        Returns:
            IngestResponse: 인덱싱 결과
                - task_id: 작업 ID
                - status: 상태 (DONE, QUEUED, PROCESSING, FAILED)

        Raises:
            SourceTypeNotFoundError: 유효하지 않은 source_type인 경우
            UpstreamServiceError: RAGFlow 서비스 오류 시 (타임아웃, HTTP 에러 등)
        """
        logger.info(
            f"Ingest request: doc_id={request.doc_id}, "
            f"source_type={request.source_type}, file_name={request.file_name}"
        )

        # 1. source_type 유효성 검증
        self.validate_source_type(request.source_type)

        # 2. RAGFlow 인덱싱 API 호출
        response = await self._ragflow.ingest_document(request)

        logger.info(
            f"Ingest completed: doc_id={request.doc_id}, "
            f"task_id={response.task_id}, status={response.status}"
        )

        return response
