"""
Chat API Router Module

Provides chat-related endpoints for AI conversation functionality.
Called by ctrlf-back (Spring backend) to generate AI responses.

Endpoints:
    - POST /ai/chat/messages: Generate AI response for user query

Phase 42 (A안 확정):
- RAGFlow 단일 검색 엔진으로 확정
- RAGFlow 장애 시 503 반환 (fallback 없음)
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from app.services.chat.rag_handler import RagSearchUnavailableError

router = APIRouter(tags=["Chat"])


def get_chat_service() -> ChatService:
    """
    Dependency injection for ChatService.

    Returns a ChatService instance. This pattern allows easy replacement
    with mock services for testing or different implementations.

    Returns:
        ChatService: Chat service instance
    """
    # TODO: In future, this could use a DI container or return
    # a singleton instance with pre-configured clients
    return ChatService()


@router.post(
    "/ai/chat/messages",
    response_model=ChatResponse,
    summary="Generate AI Chat Response",
    description=(
        "Receives user query with conversation history and generates AI response. "
        "Currently returns dummy response. RAG and LLM integration coming soon."
    ),
    responses={
        200: {
            "description": "Successfully generated response",
            "content": {
                "application/json": {
                    "example": {
                        "answer": "This is the AI response...",
                        "sources": [
                            {
                                "doc_id": "HR-001",
                                "title": "Employee Handbook",
                                "page": 15,
                                "score": 0.95,
                            }
                        ],
                        "meta": {
                            "used_model": "gpt-4",
                            "route": "ROUTE_RAG_INTERNAL",
                            "masked": True,
                            "latency_ms": 1500,
                        },
                    }
                }
            },
        },
        422: {"description": "Validation error in request body"},
        503: {"description": "RAG 검색 서비스 사용 불가 (RAGFlow 장애)"},
    },
)
async def create_chat_message(
    req: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    """
    Generate AI response for a chat request.

    This endpoint is called by the backend to generate AI responses
    for user queries. It processes the conversation history and
    returns an answer with relevant source documents.

    **Request Body:**
    - `session_id`: Chat session identifier
    - `user_id`: User identifier (employee ID, etc.)
    - `user_role`: User's role (EMPLOYEE, MANAGER, ADMIN)
    - `department`: User's department (optional)
    - `domain`: Query domain for routing (optional)
    - `channel`: Request channel (WEB, MOBILE)
    - `messages`: Conversation history

    **Response:**
    - `answer`: Generated AI response text
    - `sources`: List of reference documents used
    - `meta`: Response metadata (model, route, etc.)

    **Phase 42 (A안 확정):**
    - RAGFlow 장애 시 503 Service Unavailable 반환
    - fallback 없음 (RAGFlow 단일 검색 엔진)

    Args:
        req: Chat request with user info and conversation history
        service: Injected ChatService instance

    Returns:
        ChatResponse with answer, sources, and metadata

    Raises:
        HTTPException 503: RAGFlow 장애 시
    """
    try:
        return await service.handle_chat(req)
    except RagSearchUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "RAG_SERVICE_UNAVAILABLE",
                "message": e.message,
            },
        ) from e
