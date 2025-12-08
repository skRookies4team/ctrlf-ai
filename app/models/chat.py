"""
Chat Models Module

Pydantic models for chat-related request/response schemas.
These models define the contract between ctrlf-back (Spring backend)
and ctrlf-ai-gateway for AI chat functionality.

Usage:
    - ChatRequest: Backend sends user query with conversation history
    - ChatResponse: AI Gateway returns answer with sources and metadata
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """
    Single message in conversation history.

    Represents one turn in the conversation, sent from backend to AI gateway.

    Attributes:
        role: Message sender type (user/assistant/system)
        content: Actual message text
    """

    role: Literal["user", "assistant", "system"] = Field(
        description="Message sender type (user/assistant/system)"
    )
    content: str = Field(description="Actual message text")


class ChatRequest(BaseModel):
    """
    Chat request from backend to AI gateway.

    Contains user information, session context, and conversation history
    for generating AI responses.

    Attributes:
        session_id: Chat session ID managed by backend
        user_id: User identifier (employee ID, etc.)
        user_role: User's role (EMPLOYEE, MANAGER, ADMIN, etc.)
        department: User's department (optional)
        domain: Query domain for routing (POLICY, INCIDENT, EDUCATION, etc.)
        channel: Request channel (WEB, MOBILE, etc.)
        messages: Conversation history (last element is the latest message)
    """

    session_id: str = Field(description="Chat session ID managed by backend")
    user_id: str = Field(description="User identifier (employee ID, etc.)")
    user_role: str = Field(
        description="User's role (e.g., EMPLOYEE, MANAGER, ADMIN)"
    )
    department: Optional[str] = Field(
        default=None, description="User's department (optional)"
    )
    domain: Optional[str] = Field(
        default=None,
        description="Query domain (e.g., POLICY, INCIDENT, EDUCATION). If not provided, AI will determine.",
    )
    channel: str = Field(
        default="WEB", description="Request channel (WEB, MOBILE, etc.)"
    )
    messages: List[ChatMessage] = Field(
        description="Conversation history (last element is the latest message)"
    )


class ChatSource(BaseModel):
    """
    Source document information retrieved by RAG.

    Represents a reference document used to generate the answer.

    Attributes:
        doc_id: Document ID managed by backend/RAGFlow
        title: Document title
        page: Page number in document (if applicable)
        score: Search relevance score (optional)
        snippet: Text excerpt from document for LLM prompt context (optional)
    """

    doc_id: str = Field(description="Document ID managed by backend/RAGFlow")
    title: str = Field(description="Document title")
    page: Optional[int] = Field(
        default=None, description="Page number in document (if applicable)"
    )
    score: Optional[float] = Field(
        default=None, description="Search relevance score (optional)"
    )
    snippet: Optional[str] = Field(
        default=None,
        description="Text excerpt from document for LLM prompt context (optional)",
    )


class ChatAnswerMeta(BaseModel):
    """
    Metadata about the AI response.

    Contains information about how the response was generated.

    Attributes:
        used_model: LLM model name used for generation
        route: Routing path (e.g., ROUTE_RAG_INTERNAL, ROUTE_DIRECT_LLM)
        masked: Whether PII masking was applied
        latency_ms: Response generation time in milliseconds
    """

    used_model: Optional[str] = Field(
        default=None, description="LLM model name used for generation"
    )
    route: Optional[str] = Field(
        default=None,
        description="Routing path (e.g., ROUTE_RAG_INTERNAL, ROUTE_DIRECT_LLM)",
    )
    masked: Optional[bool] = Field(
        default=None, description="Whether PII masking was applied"
    )
    latency_ms: Optional[int] = Field(
        default=None, description="Response generation time in milliseconds"
    )


class ChatResponse(BaseModel):
    """
    Chat response from AI gateway to backend.

    Contains the generated answer, source documents, and metadata.

    Attributes:
        answer: Final answer text
        sources: List of reference documents used
        meta: Response metadata (model, route, etc.)
    """

    answer: str = Field(description="Final answer text")
    sources: List[ChatSource] = Field(
        default_factory=list, description="List of reference documents used"
    )
    meta: ChatAnswerMeta = Field(
        default_factory=ChatAnswerMeta,
        description="Response metadata (model, route, etc.)",
    )
