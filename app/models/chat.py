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

    Phase 13 업데이트:
    - article_label, article_path 필드 추가 (조항/섹션 정보)
    - 사용자에게 "어떤 문서의 몇 조/몇 항인지" 정보 제공

    Phase 29 업데이트:
    - source_type 필드 추가 (POLICY, TRAINING_SCRIPT 등 소스 유형 구분)
    - 교육 스크립트 기반 RAG 결과와 정책 문서 결과 구분

    Attributes:
        doc_id: Document ID managed by backend/RAGFlow
        title: Document title
        page: Page number in document (if applicable)
        score: Search relevance score (optional)
        snippet: Text excerpt from document for LLM prompt context (optional)
        article_label: Human-readable article label (e.g., "제10조 (정보보호 의무) 제2항")
        article_path: Hierarchical path to the article (e.g., "제3장 > 제10조 > 제2항")
        source_type: Source type (POLICY, TRAINING_SCRIPT, etc.)
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

    # Phase 13: 조항/섹션 메타데이터 필드
    article_label: Optional[str] = Field(
        default=None,
        description="Human-readable article label (e.g., '제10조 (정보보호 의무) 제2항')",
    )
    article_path: Optional[str] = Field(
        default=None,
        description="Hierarchical path to the article (e.g., '제3장 정보보호 > 제10조 > 제2항')",
    )

    # Phase 29: 소스 유형 구분
    source_type: Optional[str] = Field(
        default=None,
        description="Source type: POLICY (정책문서), TRAINING_SCRIPT (교육스크립트), etc.",
    )


class ChatAnswerMeta(BaseModel):
    """
    Metadata about the AI response.

    Contains information about how the response was generated,
    including intent classification, routing, and PII detection details.

    Phase 10 업데이트:
    - user_role 필드 추가 (UserRole Enum 값)

    Phase 12 업데이트:
    - error_type, error_message 필드 추가 (에러 표준화)
    - fallback_reason 필드 추가 (fallback 발생 시 원인)
    - rag_latency_ms, llm_latency_ms, backend_latency_ms 필드 추가 (개별 지연 측정)

    Attributes:
        user_role: User's role (EMPLOYEE, ADMIN, INCIDENT_MANAGER)
        used_model: LLM model name used for generation
        route: Routing path (e.g., RAG_INTERNAL, LLM_ONLY, BACKEND_API)
        intent: Classified intent type (e.g., POLICY_QA, INCIDENT_REPORT)
        domain: Resolved domain (e.g., POLICY, INCIDENT, EDU)
        masked: Whether any PII masking was applied (input or output)
        has_pii_input: Whether PII was detected in user input
        has_pii_output: Whether PII was detected in LLM output
        rag_used: Whether RAG search was performed
        rag_source_count: Number of RAG sources retrieved
        latency_ms: Response generation time in milliseconds
        error_type: Error type code (Phase 12)
        error_message: Summarized error message (Phase 12)
        fallback_reason: Reason for fallback if applicable (Phase 12)
        rag_latency_ms: RAG search latency in ms (Phase 12)
        llm_latency_ms: LLM generation latency in ms (Phase 12)
        backend_latency_ms: Backend API latency in ms (Phase 12)
    """

    user_role: Optional[str] = Field(
        default=None,
        description="User's role (EMPLOYEE, ADMIN, INCIDENT_MANAGER)",
    )
    used_model: Optional[str] = Field(
        default=None, description="LLM model name used for generation"
    )
    route: Optional[str] = Field(
        default=None,
        description="Routing path (e.g., RAG_INTERNAL, LLM_ONLY, BACKEND_API)",
    )
    intent: Optional[str] = Field(
        default=None,
        description="Classified intent type (e.g., POLICY_QA, INCIDENT_REPORT)",
    )
    domain: Optional[str] = Field(
        default=None,
        description="Resolved domain (e.g., POLICY, INCIDENT, EDU)",
    )
    masked: Optional[bool] = Field(
        default=None, description="Whether any PII masking was applied"
    )
    has_pii_input: Optional[bool] = Field(
        default=None, description="Whether PII was detected in user input"
    )
    has_pii_output: Optional[bool] = Field(
        default=None, description="Whether PII was detected in LLM output"
    )
    rag_used: Optional[bool] = Field(
        default=None, description="Whether RAG search was performed"
    )
    rag_source_count: Optional[int] = Field(
        default=None, description="Number of RAG sources retrieved"
    )
    # Option 3: 실제 사용된 검색 엔진 (운영 디버깅용)
    retriever_used: Optional[str] = Field(
        default=None,
        description="Retriever backend used (MILVUS, RAGFLOW, RAGFLOW_FALLBACK)",
    )
    latency_ms: Optional[int] = Field(
        default=None, description="Response generation time in milliseconds"
    )
    # Phase 12: 에러 정보 필드
    error_type: Optional[str] = Field(
        default=None,
        description="Error type code (UPSTREAM_TIMEOUT, UPSTREAM_ERROR, BAD_REQUEST, INTERNAL_ERROR)",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Summarized error message (external details not exposed)",
    )
    fallback_reason: Optional[str] = Field(
        default=None,
        description="Reason for fallback if applicable (e.g., RAG_FAIL, LLM_FAIL, BACKEND_FAIL)",
    )
    # Phase 12: 개별 서비스 지연 시간
    rag_latency_ms: Optional[int] = Field(
        default=None, description="RAG search latency in milliseconds"
    )
    llm_latency_ms: Optional[int] = Field(
        default=None, description="LLM generation latency in milliseconds"
    )
    backend_latency_ms: Optional[int] = Field(
        default=None, description="Backend API latency in milliseconds"
    )
    # Phase 14: RAG Gap 후보 플래그
    rag_gap_candidate: bool = Field(
        default=False,
        description="Whether this is a RAG gap candidate (POLICY/EDU domain with no/low-score RAG results)",
    )
    # Personalization: 개인화 Q ID
    personalization_q: Optional[str] = Field(
        default=None,
        description="Personalization sub-intent ID (Q1-Q20) if this is a personalization request",
    )
    # Phase 50: 금지질문 필터 정보
    retrieval_skipped: bool = Field(
        default=False,
        description="Whether RAG retrieval was skipped (e.g., forbidden query)",
    )
    retrieval_skip_reason: Optional[str] = Field(
        default=None,
        description="Reason for skipping retrieval (e.g., FORBIDDEN_QUERY:rule_id)",
    )
    # Step 3: Backend API 차단 정보
    backend_skipped: bool = Field(
        default=False,
        description="Whether Backend API was skipped (e.g., forbidden query)",
    )
    backend_skip_reason: Optional[str] = Field(
        default=None,
        description="Reason for skipping Backend API (e.g., FORBIDDEN_BACKEND:rule_id)",
    )
    # Step 6: 금지질문 필터 상세 관측 필드 (임계값 튜닝/오탐 분석용)
    forbidden_match_type: Optional[str] = Field(
        default=None,
        description="Match engine type: exact, fuzzy, embedding (null if not forbidden)",
    )
    forbidden_score: Optional[float] = Field(
        default=None,
        description="Match score: fuzzy(0-100) or embedding(0-1) (null if exact or not forbidden)",
    )
    forbidden_ruleset_version: Optional[str] = Field(
        default=None,
        description="Ruleset version used for matching",
    )
    forbidden_rule_id: Optional[str] = Field(
        default=None,
        description="Matched rule ID (e.g., FR-A-001)",
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
