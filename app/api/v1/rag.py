"""
RAG API Router Module

Provides RAG (Retrieval-Augmented Generation) document processing endpoints.
Called by ctrlf-back (Spring backend) to process documents for RAG indexing.

Endpoints:
    - POST /ai/rag/process: Process document for RAG indexing
"""

from fastapi import APIRouter, Depends

from app.models.rag import RagProcessRequest, RagProcessResponse
from app.services.rag_service import RagService

router = APIRouter(tags=["RAG"])


def get_rag_service() -> RagService:
    """
    Dependency injection for RagService.

    Returns a RagService instance. This pattern allows easy replacement
    with mock services for testing or different implementations.

    Returns:
        RagService: RAG service instance
    """
    # TODO: In future, this could use a DI container or return
    # a singleton instance with pre-configured RAGFlow client
    return RagService()


@router.post(
    "/ai/rag/process",
    response_model=RagProcessResponse,
    summary="Process Document for RAG",
    description=(
        "Receives document information and processes it for RAG indexing. "
        "Currently returns dummy response. RAGFlow integration coming soon."
    ),
    responses={
        200: {
            "description": "Document processing result",
            "content": {
                "application/json": {
                    "example": {
                        "doc_id": "HR-001",
                        "success": True,
                        "message": "Document successfully processed and indexed",
                    }
                }
            },
        },
        422: {"description": "Validation error in request body"},
    },
)
async def process_rag_document(
    req: RagProcessRequest,
    service: RagService = Depends(get_rag_service),
) -> RagProcessResponse:
    """
    Process a document for RAG indexing.

    This endpoint is called by the backend when a new document needs
    to be indexed for RAG retrieval. It handles document download,
    preprocessing, embedding generation, and storage.

    **Request Body:**
    - `doc_id`: Document identifier
    - `file_url`: URL where the document file is located
    - `domain`: Document domain (POLICY, INCIDENT, EDUCATION)
    - `acl`: Access control settings (optional)
        - `roles`: List of roles that can access
        - `departments`: List of departments that can access

    **Response:**
    - `doc_id`: Same document ID from request
    - `success`: Whether processing was successful
    - `message`: Additional information or error message

    **Current Status:**
    Returns dummy response. RAGFlow integration will be
    implemented in the next phase.

    Args:
        req: RAG process request with document info
        service: Injected RagService instance

    Returns:
        RagProcessResponse with processing result
    """
    return await service.process_document(req)
