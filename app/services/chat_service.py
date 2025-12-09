"""
Chat Service Module

Business logic for AI chat functionality.
Implements the complete RAG + LLM pipeline for generating AI responses.

Phase 6 RAG E2E 플로우 (ROUTE_RAG_INTERNAL):
1. PII masking (INPUT) - 사용자 질문에서 PII 마스킹
2. Intent classification - 의도 분류 및 라우팅 결정
3. RAG search - RAGFlow에서 관련 문서 검색 (dataset=domain)
4. Build LLM prompt - RAG context를 포함한 프롬프트 구성
5. Generate response - LLM으로 답변 생성
6. PII masking (OUTPUT) - LLM 응답에서 PII 마스킹
7. Generate AI log - 백엔드로 로그 전송
8. Return ChatResponse - answer + sources + meta 반환

RAG Fallback 정책:
- RAG 호출 실패 시: 로그에 경고 남기고 RAG 없이 LLM-only로 진행
- RAG 결과 0건 시: "관련 문서를 찾지 못했습니다" 안내와 함께 일반 QA로 처리
- meta.rag_used = len(sources) > 0
- meta.rag_source_count = len(sources)
"""

import asyncio
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
from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient
from app.services.ai_log_service import AILogService
from app.services.intent_service import IntentService
from app.services.pii_service import PiiService

logger = get_logger(__name__)


# System prompt template for LLM (RAG context가 있는 경우)
SYSTEM_PROMPT_WITH_RAG = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.
아래의 참고 문서 목록을 바탕으로 사용자의 질문에 한국어로 정확하고 친절하게 답변해 주세요.
답변 시 출처 문서를 인용하면 더 좋습니다.

만약 참고 문서에서 답을 찾을 수 없다면, 솔직하게 "해당 내용은 참고 문서에서 찾을 수 없습니다"라고 말해 주세요.
추측이나 거짓 정보를 제공하지 마세요.
"""

# System prompt template for LLM (RAG context가 없는 경우)
SYSTEM_PROMPT_NO_RAG = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.
현재 관련 문서를 찾지 못했습니다. 일반적인 지식을 바탕으로 답변하되,
구체적인 사내 규정이나 정책에 대해서는 "관련 문서를 찾지 못했으므로, 담당 부서에 직접 문의해 주세요"라고 안내해 주세요.
추측이나 거짓 정보를 제공하지 마세요.
"""

# RAG 검색 결과가 없을 때 사용자에게 안내할 메시지
NO_RAG_RESULTS_NOTICE = (
    "\n\n※ 참고: 관련 문서를 찾지 못하여 일반적인 답변을 드립니다. "
    "정확한 정보는 담당 부서에 확인해 주세요."
)


class ChatService:
    """
    Chat service handling AI conversation logic.

    This service implements the complete RAG + LLM pipeline:
    1. PII masking (INPUT) - Mask PII in user query
    2. Intent classification - Determine intent and routing
    3. RAG search - Search relevant documents (if route requires)
    4. Build LLM prompt - Construct prompt with RAG context
    5. Generate response - Call LLM for response
    6. PII masking (OUTPUT) - Mask any PII in LLM response
    7. Generate and send AI log - Create log entry and send to backend
    8. Return response - Return formatted ChatResponse

    RAG Fallback Strategy:
    - RAG 호출 실패 시: 경고 로그 남기고 RAG 없이 LLM-only로 진행
    - RAG 결과 0건 시: 안내 문구와 함께 일반 QA로 처리
    - meta.rag_used = True only if len(sources) > 0

    Attributes:
        _ragflow: RagflowClient for document search
        _llm: LLMClient for response generation
        _pii: PiiService for PII masking
        _intent: IntentService for intent classification
        _ai_log: AILogService for logging to backend

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
        ai_log_service: Optional[AILogService] = None,
    ) -> None:
        """
        Initialize ChatService.

        Args:
            ragflow_client: RagflowClient instance. If None, creates a new instance.
            llm_client: LLMClient instance. If None, creates a new instance.
            pii_service: PiiService instance. If None, creates a new instance.
            intent_service: IntentService instance. If None, creates a new instance.
            ai_log_service: AILogService instance. If None, creates a new instance.
                           Pass custom services for testing or dependency injection.
        """
        self._ragflow = ragflow_client or RagflowClient()
        self._llm = llm_client or LLMClient()
        self._pii = pii_service or PiiService()
        self._intent = intent_service or IntentService()
        self._ai_log = ai_log_service or AILogService(pii_service=self._pii)

    async def handle_chat(self, req: ChatRequest) -> ChatResponse:
        """
        Handle a chat request and generate a response using full pipeline.

        Pipeline steps:
        1. Extract user query from last message
        2. PII masking (INPUT stage)
        3. Intent classification and routing
        4. RAG search (if route requires) - ROUTE_RAG_INTERNAL
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
        rag_search_attempted = False
        rag_search_failed = False

        if route == RouteType.ROUTE_RAG_INTERNAL:
            # RAG search for internal policy/document queries
            rag_search_attempted = True
            try:
                # search_as_sources: RagDocument → ChatSource 변환 포함
                sources = await self._ragflow.search_as_sources(
                    query=masked_query,
                    domain=domain,
                    user_role=req.user_role,
                    department=req.department,
                    top_k=5,
                )
                logger.info(f"RAG search returned {len(sources)} sources")

                if not sources:
                    logger.warning(
                        f"RAG search returned no results for query: {masked_query[:50]}..."
                    )

            except Exception as e:
                # RAG Fallback 정책: RAG 호출 실패 시 로그 남기고 LLM-only로 진행
                logger.exception(
                    f"RAG search failed, proceeding with LLM-only: {e}"
                )
                rag_search_failed = True
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
        llm_messages = self._build_llm_messages(
            user_query=masked_query,
            sources=sources,
            req=req,
            rag_attempted=rag_search_attempted,
        )

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

            # RAG 시도했지만 결과 없으면 안내 문구 추가
            if rag_search_attempted and not sources and not rag_search_failed:
                # LLM 응답에 안내 문구 추가 (RAG 결과 0건인 경우)
                raw_answer = raw_answer.rstrip() + NO_RAG_RESULTS_NOTICE

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

        # Determine if RAG was actually used (results > 0)
        # Phase 6 정책: rag_used = len(sources) > 0
        rag_used = len(sources) > 0

        # Build metadata with extended fields for logging
        meta = ChatAnswerMeta(
            used_model="internal-llm",  # TODO: Get actual model name from LLM response
            route=final_route.value,
            intent=intent.value,
            domain=domain,
            masked=has_pii,
            has_pii_input=pii_input.has_pii,
            has_pii_output=pii_output.has_pii,
            rag_used=rag_used,
            rag_source_count=len(sources),
            latency_ms=latency_ms,
        )

        logger.info(
            f"Chat response generated: session_id={req.session_id}, "
            f"latency_ms={latency_ms}, sources_count={len(sources)}, "
            f"route={final_route.value}, intent={intent.value}, "
            f"rag_used={rag_used}, masked={has_pii}"
        )

        # Step 8: Generate and send AI log (fire-and-forget)
        await self._send_ai_log(
            req=req,
            response_answer=final_answer,
            user_query=user_query,
            intent=intent.value,
            domain=domain,
            route=final_route.value,
            has_pii_input=pii_input.has_pii,
            has_pii_output=pii_output.has_pii,
            rag_used=rag_used,
            rag_source_count=len(sources),
            latency_ms=latency_ms,
            model_name="internal-llm",
        )

        return ChatResponse(
            answer=final_answer,
            sources=sources,
            meta=meta,
        )

    async def _send_ai_log(
        self,
        req: ChatRequest,
        response_answer: str,
        user_query: str,
        intent: str,
        domain: str,
        route: str,
        has_pii_input: bool,
        has_pii_output: bool,
        rag_used: bool,
        rag_source_count: int,
        latency_ms: int,
        model_name: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """
        AI 로그를 생성하고 백엔드로 전송합니다 (fire-and-forget).

        LOG 단계 PII 마스킹을 적용하여 question_masked, answer_masked를 생성하고
        AILogEntry를 만들어 백엔드로 비동기 전송합니다.

        Args:
            req: 원본 ChatRequest
            response_answer: 최종 응답 텍스트
            user_query: 원본 사용자 질문
            intent: 분류된 의도
            domain: 보정된 도메인
            route: 라우팅 결과
            has_pii_input: 입력 PII 검출 여부
            has_pii_output: 출력 PII 검출 여부
            rag_used: RAG 사용 여부
            rag_source_count: RAG 소스 개수
            latency_ms: 처리 시간
            model_name: 사용된 모델명
            error_code: 에러 코드
            error_message: 에러 메시지
        """
        try:
            # LOG 단계 PII 마스킹 적용
            question_masked, answer_masked = await self._ai_log.mask_for_log(
                question=user_query,
                answer=response_answer,
            )

            # 로그 엔트리 생성
            log_entry = self._ai_log.create_log_entry(
                request=req,
                response=ChatResponse(answer=response_answer, sources=[], meta=ChatAnswerMeta()),
                intent=intent,
                domain=domain,
                route=route,
                has_pii_input=has_pii_input,
                has_pii_output=has_pii_output,
                rag_used=rag_used,
                rag_source_count=rag_source_count,
                latency_ms=latency_ms,
                model_name=model_name,
                error_code=error_code,
                error_message=error_message,
                question_masked=question_masked,
                answer_masked=answer_masked,
            )

            # 비동기 전송 (fire-and-forget)
            asyncio.create_task(self._ai_log.send_log_async(log_entry))

        except Exception as e:
            # 로그 생성/전송 실패는 메인 로직에 영향 주지 않음
            logger.warning(f"Failed to send AI log: {e}")

    def _build_llm_messages(
        self,
        user_query: str,
        sources: List[ChatSource],
        req: ChatRequest,
        rag_attempted: bool = False,
    ) -> List[Dict[str, str]]:
        """
        Build message list for LLM chat completion.

        Constructs a message array with:
        1. System prompt with instructions (RAG context 유무에 따라 다름)
        2. RAG context (if sources available)
        3. User's actual question

        Args:
            user_query: The user's question text (masked if PII was detected)
            sources: List of ChatSource from RAG search
            req: Original ChatRequest for context
            rag_attempted: Whether RAG search was attempted

        Returns:
            List of message dicts with 'role' and 'content' keys
        """
        messages: List[Dict[str, str]] = []

        # System message - RAG context 유무에 따라 다른 프롬프트 사용
        if sources:
            # RAG 결과가 있는 경우
            system_content = SYSTEM_PROMPT_WITH_RAG
            context_text = self._format_sources_for_prompt(sources)
            system_content += f"\n\n참고 문서:\n{context_text}"
        elif rag_attempted:
            # RAG 시도했지만 결과 없는 경우
            system_content = SYSTEM_PROMPT_NO_RAG
        else:
            # RAG 시도하지 않은 경우 (ROUTE_LLM_ONLY 등)
            system_content = SYSTEM_PROMPT_WITH_RAG
            system_content += "\n\n참고 문서: (검색 대상 아님)"

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
                rag_used=False,
                rag_source_count=0,
            ),
        )
