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
from fastapi import BackgroundTasks
from fastapi import Request

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
        "Uses RAG for context retrieval and LLM for response generation."
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
    request: Request,
    background_tasks: BackgroundTasks,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    try:
        response = await service.handle_chat(
            req=req,
            background_tasks=background_tasks,
        )

        # metrics / state용 정보 세팅
        if response.meta:
            request.state.domain = response.meta.domain
            request.state.model_name = response.meta.used_model
            request.state.rag_used = bool(response.meta.rag_used)

        return response

    except RagSearchUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "RAG_SERVICE_UNAVAILABLE",
                "message": e.message,
            },
        ) from e
