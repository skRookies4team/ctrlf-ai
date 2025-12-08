"""
Models package

Pydantic models for request/response schemas.

Modules:
    - chat: Chat-related models (ChatRequest, ChatResponse, etc.)
    - rag: RAG document processing models (RagProcessRequest, RagProcessResponse, etc.)
"""

from app.models.chat import (
    ChatAnswerMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSource,
)
from app.models.rag import (
    RagAcl,
    RagProcessRequest,
    RagProcessResponse,
)

__all__ = [
    # Chat models
    "ChatMessage",
    "ChatRequest",
    "ChatSource",
    "ChatAnswerMeta",
    "ChatResponse",
    # RAG models
    "RagAcl",
    "RagProcessRequest",
    "RagProcessResponse",
]
