"""
Chat Service Module

Business logic for AI chat functionality.
Implements the complete RAG + LLM pipeline for generating AI responses:
1. PII masking (input)
2. Intent classification and routing
3. Search relevant documents via RAGFlow (if route requires)
4. Build LLM prompt with context from RAG results
5. Generate response via LLM service
6. PII masking (output)
7. Return formatted response with sources and metadata
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
from app.models.intent import MaskingStage, RouteType
from app.services.intent_service import IntentService
from app.services.llm_client import LLMClient
from app.services.pii_service import PiiService
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

    This service implements the complete RAG + LLM pipeline:
    1. PII masking (input) - Mask PII in user query
    2. Intent classification - Determine intent and routing
    3. RAG search - Search relevant documents (if route requires)
    4. Build LLM prompt - Construct prompt with RAG context
    5. Generate response - Call LLM for response
    6. PII masking (output) - Mask any PII in LLM response
    7. Return response - Return formatted ChatResponse

    Attributes:
        _ragflow: RagflowClient for document search
        _llm: LLMClient for response generation
        _pii: PiiService for PII masking
        _intent: IntentService for intent classification

    Example:
        service = ChatService()
        response = await service.handle_chat(request)
    """

    def __init__(
        self,
        ragflow_client: Optional[RagflowClient] = None,
        llm_client: Optional[LLMClient] = None,
        pii_service: Optional[PiiService] = None,
        intent_service: Optional[IntentService] = None,
    ) -> None:
        """
        Initialize ChatService.

        Args:
            ragflow_client: RagflowClient instance. If None, creates a new instance.
            llm_client: LLMClient instance. If None, creates a new instance.
            pii_service: PiiService instance. If None, creates a new instance.
            intent_service: IntentService instance. If None, creates a new instance.
                           Pass custom services for testing or dependency injection.
        """
        self._ragflow = ragflow_client or RagflowClient()
        self._llm = llm_client or LLMClient()
        self._pii = pii_service or PiiService()
        self._intent = intent_service or IntentService()

    async def handle_chat(self, req: ChatRequest) -> ChatResponse:
        """
        Handle a chat request and generate a response using full pipeline.

        Pipeline steps:
        1. Extract user query from last message
        2. PII masking (INPUT stage)
        3. Intent classification and routing
        4. RAG search (if route requires)
        5. Build LLM messages with system prompt and RAG context
        6. Call LLM to generate response
        7. PII masking (OUTPUT stage)
        8. Return ChatResponse with answer, sources, and metadata

        Args:
            req: ChatRequest containing session info, user info, and messages

        Returns:
            ChatResponse with answer, sources, and metadata

        Note:
            - If RAGFlow/LLM not configured, returns fallback response
            - Gracefully handles errors without raising exceptions
            - PII masking is skipped if PII_ENABLED=False or PII_BASE_URL not set
        """
        start_time = time.perf_counter()

        logger.info(
            f"Processing chat request: session_id={req.session_id}, "
            f"user_id={req.user_id}, user_role={req.user_role}"
        )

        # Step 1: Extract the latest user message as query
        if not req.messages:
            return self._create_fallback_response(
                "No messages provided in request.",
                start_time,
                has_pii=False,
            )

        user_query = req.messages[-1].content
        logger.debug(f"User query: {user_query[:100]}...")

        # Step 2: PII Masking (INPUT stage)
        pii_input = await self._pii.detect_and_mask(
            text=user_query,
            stage=MaskingStage.INPUT,
        )
        masked_query = pii_input.masked_text

        if pii_input.has_pii:
            logger.info(
                f"PII detected in input: {len(pii_input.tags)} entities masked"
            )

        # Step 3: Intent Classification and Routing
        intent_result = self._intent.classify(
            req=req,
            user_query=masked_query,
        )
        intent = intent_result.intent
        domain = intent_result.domain or req.domain or "POLICY"
        route = intent_result.route

        logger.info(
            f"Intent classification: intent={intent.value}, "
            f"route={route.value}, domain={domain}"
        )

        # Step 4: RAG Search (based on route)
        sources: List[ChatSource] = []

        if route == RouteType.ROUTE_RAG_INTERNAL:
            # RAG search for internal policy/document queries
            try:
                sources = await self._ragflow.search(
                    query=masked_query,
                    domain=domain,
                    user_role=req.user_role,
                    department=req.department,
                    top_k=5,
                )
                logger.info(f"RAG search returned {len(sources)} sources")
            except Exception as e:
                logger.exception("RAG search failed")
                sources = []

        elif route in {RouteType.ROUTE_LLM_ONLY, RouteType.ROUTE_TRAINING}:
            # LLM only routes - no RAG search
            logger.debug(f"Skipping RAG search for route: {route.value}")
            sources = []

        elif route == RouteType.ROUTE_INCIDENT:
            # TODO: INCIDENT 경로는 별도 Incident 모듈로 라우팅 예정
            # 현재는 LLM only로 처리
            logger.debug("ROUTE_INCIDENT: Currently using LLM only (TODO: Incident module)")
            sources = []

        else:
            # Fallback or error routes
            logger.debug(f"Route {route.value}: No RAG search")
            sources = []

        # Step 5: Build LLM prompt messages
        llm_messages = self._build_llm_messages(masked_query, sources, req)

        # Step 6: Generate LLM response
        raw_answer: str
        final_route = route

        try:
            raw_answer = await self._llm.generate_chat_completion(
                messages=llm_messages,
                model=None,  # Use server default
                temperature=0.2,
                max_tokens=1024,
            )
            # Update route based on actual sources used
            if route == RouteType.ROUTE_RAG_INTERNAL and not sources:
                # TODO: RAG 결과가 없을 때 route를 ROUTE_LLM_ONLY로 바꿀지 여부
                # 현재는 그대로 둠
                pass
        except Exception as e:
            logger.exception("LLM generation failed")
            raw_answer = (
                "죄송합니다. 현재 AI 서비스에 일시적인 문제가 발생했습니다. "
                "잠시 후 다시 시도해 주세요."
            )
            final_route = RouteType.ROUTE_ERROR

        # Step 7: PII Masking (OUTPUT stage)
        pii_output = await self._pii.detect_and_mask(
            text=raw_answer,
            stage=MaskingStage.OUTPUT,
        )
        final_answer = pii_output.masked_text

        if pii_output.has_pii:
            logger.info(
                f"PII detected in output: {len(pii_output.tags)} entities masked"
            )

        # Determine if any PII was detected (input or output)
        has_pii = pii_input.has_pii or pii_output.has_pii

        # Calculate latency
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        # Build metadata
        meta = ChatAnswerMeta(
            used_model="internal-llm",  # TODO: Get actual model name from LLM response
            route=final_route.value,
            masked=has_pii,
            latency_ms=latency_ms,
        )

        logger.info(
            f"Chat response generated: session_id={req.session_id}, "
            f"latency_ms={latency_ms}, sources_count={len(sources)}, "
            f"route={final_route.value}, masked={has_pii}"
        )

        return ChatResponse(
            answer=final_answer,
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
            user_query: The user's question text (masked if PII was detected)
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
        has_pii: bool = False,
    ) -> ChatResponse:
        """
        Create a fallback response for error cases.

        Args:
            message: Error or fallback message
            start_time: Request start time for latency calculation
            has_pii: Whether PII was detected

        Returns:
            ChatResponse with fallback answer
        """
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        return ChatResponse(
            answer=message,
            sources=[],
            meta=ChatAnswerMeta(
                used_model=None,
                route=RouteType.ROUTE_FALLBACK.value,
                masked=has_pii,
                latency_ms=latency_ms,
            ),
        )
