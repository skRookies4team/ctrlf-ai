"""
Chat API Router Module

Provides chat-related endpoints for AI conversation functionality.
Called by ctrlf-back (Spring backend) to generate AI responses.

Endpoints:
    - POST /ai/chat/messages: Generate AI response for user query
"""

from fastapi import APIRouter, Depends

from app.models.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

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

    **Current Status:**
    Returns dummy response. RAG and LLM integration will be
    implemented in the next phase.

    Args:
        req: Chat request with user info and conversation history
        service: Injected ChatService instance

    Returns:
        ChatResponse with answer, sources, and metadata
    """
    return await service.handle_chat(req)
