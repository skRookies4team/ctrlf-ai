"""
Chat Service Module

Business logic for AI chat functionality.
Implements the RAG + LLM pipeline for generating AI responses:
1. Search relevant documents via RAGFlow
2. Build LLM prompt with context from RAG results
3. Generate response via LLM service
4. Return formatted response with sources and metadata

PII masking and intent classification are planned for future phases.
"""

import time
from typing import Dict, List, Optional

from app.core.logging import get_logger
from app.models.chat import (
    ChatAnswerMeta,
    ChatRequest,
    ChatResponse,
    ChatSource,
)
from app.services.llm_client import LLMClient
from app.services.ragflow_client import RagflowClient

logger = get_logger(__name__)


# System prompt template for LLM
SYSTEM_PROMPT_TEMPLATE = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.
아래의 참고 문서 목록을 바탕으로 사용자의 질문에 한국어로 정확하고 친절하게 답변해 주세요.
답변 시 출처 문서를 인용하면 더 좋습니다.

만약 참고 문서에서 답을 찾을 수 없다면, 솔직하게 "해당 내용은 참고 문서에서 찾을 수 없습니다"라고 말해 주세요.
추측이나 거짓 정보를 제공하지 마세요.
"""


class ChatService:
    """
    Chat service handling AI conversation logic.

    This service implements the RAG + LLM pipeline:
    1. Extract user query from conversation history
    2. Search relevant documents via RAGFlow
    3. Build LLM prompt with RAG context
    4. Generate response via LLM
    5. Return formatted response with sources

    Attributes:
        _ragflow: RagflowClient for document search
        _llm: LLMClient for response generation

    Example:
        service = ChatService()
        response = await service.handle_chat(request)
    """

    def __init__(
        self,
        ragflow_client: Optional[RagflowClient] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        """
        Initialize ChatService.

        Args:
            ragflow_client: RagflowClient instance. If None, creates a new instance.
            llm_client: LLMClient instance. If None, creates a new instance.
                       Pass custom clients for testing or dependency injection.
        """
        self._ragflow = ragflow_client or RagflowClient()
        self._llm = llm_client or LLMClient()

    async def handle_chat(self, req: ChatRequest) -> ChatResponse:
        """
        Handle a chat request and generate a response using RAG + LLM pipeline.

        Pipeline steps:
        1. Extract user query from last message
        2. Search relevant documents via RAGFlow
        3. Build LLM messages with system prompt and RAG context
        4. Call LLM to generate response
        5. Return ChatResponse with answer, sources, and metadata

        Args:
            req: ChatRequest containing session info, user info, and messages

        Returns:
            ChatResponse with answer, sources, and metadata

        Note:
            - If RAGFlow/LLM not configured, returns fallback response
            - Gracefully handles errors without raising exceptions
        """
        start_time = time.perf_counter()

        logger.info(
            f"Processing chat request: session_id={req.session_id}, "
            f"user_id={req.user_id}, user_role={req.user_role}"
        )

        # Extract the latest user message as query
        if not req.messages:
            return self._create_fallback_response(
                "No messages provided in request.",
                start_time,
            )

        user_query = req.messages[-1].content
        logger.debug(f"User query: {user_query[:100]}...")

        # TODO: Step 1 - PII Masking (planned for future phase)
        # masked_query = await self._mask_pii(user_query)

        # TODO: Step 2 - Intent Classification (planned for future phase)
        # intent = await self._classify_intent(masked_query, req.domain)

        # Step 3 - RAG Search
        try:
            sources = await self._ragflow.search(
                query=user_query,
                domain=req.domain,
                user_role=req.user_role,
                department=req.department,
                top_k=5,
            )
            logger.info(f"RAG search returned {len(sources)} sources")
        except Exception as e:
            logger.exception("RAG search failed")
            sources = []

        # Step 4 - Build LLM prompt messages
        llm_messages = self._build_llm_messages(user_query, sources, req)

        # Step 5 - Generate LLM response
        try:
            answer_text = await self._llm.generate_chat_completion(
                messages=llm_messages,
                model=None,  # Use server default
                temperature=0.2,
                max_tokens=1024,
            )
            route = "ROUTE_RAG_INTERNAL" if sources else "ROUTE_LLM_ONLY"
        except Exception as e:
            logger.exception("LLM generation failed")
            answer_text = (
                "죄송합니다. 현재 AI 서비스에 일시적인 문제가 발생했습니다. "
                "잠시 후 다시 시도해 주세요."
            )
            route = "ROUTE_ERROR"

        # Calculate latency
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        # Build metadata
        meta = ChatAnswerMeta(
            used_model="internal-llm",  # TODO: Get actual model name from LLM response
            route=route,
            masked=None,  # TODO: Set after PII masking implementation
            latency_ms=latency_ms,
        )

        logger.info(
            f"Chat response generated: session_id={req.session_id}, "
            f"latency_ms={latency_ms}, sources_count={len(sources)}"
        )

        return ChatResponse(
            answer=answer_text,
            sources=sources,
            meta=meta,
        )

    def _build_llm_messages(
        self,
        user_query: str,
        sources: List[ChatSource],
        req: ChatRequest,
    ) -> List[Dict[str, str]]:
        """
        Build message list for LLM chat completion.

        Constructs a message array with:
        1. System prompt with instructions
        2. RAG context (if sources available)
        3. User's actual question

        Args:
            user_query: The user's question text
            sources: List of ChatSource from RAG search
            req: Original ChatRequest for context

        Returns:
            List of message dicts with 'role' and 'content' keys
        """
        messages: List[Dict[str, str]] = []

        # System message with instructions
        system_content = SYSTEM_PROMPT_TEMPLATE

        # Add RAG context to system message if sources found
        if sources:
            context_text = self._format_sources_for_prompt(sources)
            system_content += f"\n\n참고 문서:\n{context_text}"
        else:
            system_content += "\n\n참고 문서: (검색된 문서가 없습니다)"

        messages.append({
            "role": "system",
            "content": system_content,
        })

        # Add conversation history (optional: include previous messages for context)
        # For now, just include the latest user query
        # TODO: Consider including recent conversation history for multi-turn context

        messages.append({
            "role": "user",
            "content": user_query,
        })

        return messages

    def _format_sources_for_prompt(self, sources: List[ChatSource]) -> str:
        """
        Format RAG sources as text for LLM prompt.

        Creates a numbered list of sources with doc_id, title, page, and snippet.

        Args:
            sources: List of ChatSource from RAG search

        Returns:
            Formatted string with source information
        """
        lines: List[str] = []

        for i, source in enumerate(sources, start=1):
            line = f"{i}) [{source.doc_id}] {source.title}"
            if source.page:
                line += f" (p.{source.page})"
            if source.score:
                line += f" [관련도: {source.score:.2f}]"
            lines.append(line)

            # Add snippet if available (truncate if too long)
            if source.snippet:
                snippet = source.snippet[:300]
                if len(source.snippet) > 300:
                    snippet += "..."
                lines.append(f"   발췌: {snippet}")

        return "\n".join(lines)

    def _create_fallback_response(
        self,
        message: str,
        start_time: float,
    ) -> ChatResponse:
        """
        Create a fallback response for error cases.

        Args:
            message: Error or fallback message
            start_time: Request start time for latency calculation

        Returns:
            ChatResponse with fallback answer
        """
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        return ChatResponse(
            answer=message,
            sources=[],
            meta=ChatAnswerMeta(
                used_model=None,
                route="ROUTE_FALLBACK",
                masked=None,
                latency_ms=latency_ms,
            ),
        )
