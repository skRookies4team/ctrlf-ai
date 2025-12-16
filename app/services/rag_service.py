"""
RAG Service Module

Business logic for RAG (Retrieval-Augmented Generation) document processing.
Handles the document ingestion pipeline by forwarding requests to RAGFlow service.

This service acts as the business logic layer between the API router
and the RAGFlow client, handling:
- Request validation and logging
- RAGFlow client invocation
- Response formatting and error handling
"""

from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.rag import RagProcessRequest, RagProcessResponse
from app.clients.ragflow_client import RagflowClient

logger = get_logger(__name__)


class RagService:
    """
    RAG service handling document processing logic.

    This service is responsible for:
    1. Receiving document processing requests from API router
    2. Forwarding requests to RAGFlow via RagflowClient
    3. Handling errors and returning appropriate responses

    The actual document processing (chunking, embedding, indexing)
    is performed by the ctrlf-ragflow service.

    Attributes:
        _client: RagflowClient instance for RAGFlow communication

    Example:
        service = RagService()
        response = await service.process_document(request)
    """

    def __init__(self, ragflow_client: Optional[RagflowClient] = None) -> None:
        """
        Initialize RagService.

        Args:
            ragflow_client: RagflowClient instance. If None, creates a new instance.
                           Pass custom client for testing or dependency injection.
        """
        self._client = ragflow_client or RagflowClient()

    async def process_document(self, req: RagProcessRequest) -> RagProcessResponse:
        """
        Process a document for RAG indexing via RAGFlow service.

        Forwards the document processing request to RAGFlow and returns the result.
        If in mock mode (AI_ENV=mock), returns a mock success response.
        If RAGFlow is not configured (RAGFLOW_BASE_URL is empty), returns a
        dummy success response for development/testing compatibility.

        Args:
            req: RagProcessRequest containing document info and ACL

        Returns:
            RagProcessResponse with processing result

        Note:
            - If AI_ENV=mock, returns mock success response (no RAGFlow needed)
            - If RAGFlow is not configured, returns dummy success response
            - On RAGFlow error, returns success=False with error details
        """
        logger.info(
            f"Processing RAG document: doc_id={req.doc_id}, "
            f"domain={req.domain}, file_url={req.file_url}"
        )

        # Log ACL info if provided
        if req.acl:
            logger.debug(
                f"Document ACL: roles={req.acl.roles}, "
                f"departments={req.acl.departments}"
            )

        settings = get_settings()

        # Mock 모드: RAGFlow 없이 Mock 응답 반환
        if settings.is_mock_mode:
            logger.info(
                f"[MOCK MODE] Returning mock success for RAG document: doc_id={req.doc_id}"
            )
            return RagProcessResponse(
                doc_id=req.doc_id,
                success=True,
                message=(
                    f"[MOCK] Document '{req.doc_id}' processed successfully. "
                    f"Domain: {req.domain}, File: {req.file_url or 'N/A'}"
                ),
            )

        # Check if RAGFlow is configured
        # Phase 9: ragflow_base_url 프로퍼티 사용 (mock/real 모드 자동 선택)
        if not settings.ragflow_base_url:
            # Return dummy success response for development/testing
            logger.info(
                f"RAGFlow not configured, returning dummy success: doc_id={req.doc_id}"
            )
            return RagProcessResponse(
                doc_id=req.doc_id,
                success=True,
                message=(
                    "RAG document processing dummy response. "
                    "RAGFLOW_BASE_URL is not configured. "
                    "Actual RAGFlow integration will be used when configured."
                ),
            )

        try:
            # Forward request to RAGFlow service
            result = await self._client.process_document_request(req)

            if result.success:
                logger.info(f"RAG document processed successfully: doc_id={req.doc_id}")
            else:
                logger.warning(
                    f"RAG document processing returned failure: "
                    f"doc_id={req.doc_id}, message={result.message}"
                )

            return result

        except Exception as e:
            # Catch any unexpected errors not handled by RagflowClient
            logger.exception(
                f"Unexpected error in RagService.process_document: doc_id={req.doc_id}"
            )
            return RagProcessResponse(
                doc_id=req.doc_id,
                success=False,
                message=f"RAGFlow integration failed: {type(e).__name__}: {str(e)}",
            )
