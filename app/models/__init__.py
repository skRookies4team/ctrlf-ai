"""
Models package

Pydantic models for request/response schemas.

Modules:
    - chat: Chat-related models (ChatRequest, ChatResponse, etc.)
    - rag: RAG document processing models (RagProcessRequest, RagProcessResponse, etc.)
    - ai_log: AI logging models (AILogEntry, AILogRequest, AILogResponse)
    - intent: Intent/Route/PII domain models
"""

from app.models.ai_log import (
    AILogEntry,
    AILogRequest,
    AILogResponse,
)
from app.models.chat import (
    ChatAnswerMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSource,
)
from app.models.intent import (
    IntentResult,
    IntentType,
    MaskingStage,
    PiiMaskResult,
    PiiTag,
    RouteType,
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
    # AI Log models
    "AILogEntry",
    "AILogRequest",
    "AILogResponse",
    # Intent/Route/PII models
    "IntentType",
    "RouteType",
    "MaskingStage",
    "PiiTag",
    "PiiMaskResult",
    "IntentResult",
]
