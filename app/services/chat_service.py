"""
Chat Service Module

Business logic for AI chat functionality.
Implements the complete RAG + LLM pipeline for generating AI responses.

Phase 12 업데이트:
- 라우트별 fallback 전략 구현 (RAG 실패, Backend 실패, LLM 실패)
- UpstreamServiceError 처리 및 error_type/error_message 반환
- 개별 서비스 latency 측정 (rag_latency_ms, llm_latency_ms, backend_latency_ms)
- fallback_reason 필드로 fallback 원인 기록

Phase 11 업데이트:
- BACKEND_API 라우트: BackendDataClient를 통해 비즈니스 데이터 조회
- MIXED_BACKEND_RAG 라우트: RAG + 백엔드 데이터 조합
- BackendContextFormatter로 백엔드 데이터를 LLM 컨텍스트로 변환
- 역할×도메인×의도별 BackendDataClient 메서드 매핑

Phase 10 업데이트:
- 역할(UserRole) × 도메인(Domain) × 라우트(RouteType) 기반 처리
- RouteType.RAG_INTERNAL, LLM_ONLY, BACKEND_API, MIXED_BACKEND_RAG 분기
- IntentResult에서 user_role 정보 활용
- ChatAnswerMeta에 user_role 추가

RAG E2E 플로우 (RouteType.RAG_INTERNAL):
1. PII masking (INPUT) - 사용자 질문에서 PII 마스킹
2. Intent classification - 의도 분류 및 라우팅 결정 (역할×도메인 기반)
3. RAG search - RAGFlow에서 관련 문서 검색 (dataset=domain)
4. Build LLM prompt - RAG context를 포함한 프롬프트 구성
5. Generate response - LLM으로 답변 생성
6. PII masking (OUTPUT) - LLM 응답에서 PII 마스킹
7. Generate AI log - 백엔드로 로그 전송
8. Return ChatResponse - answer + sources + meta 반환

BACKEND_API 플로우:
1. PII masking (INPUT)
2. Intent classification
3. BackendDataClient로 비즈니스 데이터 조회 (교육현황, 사고통계 등)
4. BackendContextFormatter로 LLM 컨텍스트 생성
5. Generate response - LLM으로 답변 생성
6. PII masking (OUTPUT)
7. Return ChatResponse

MIXED_BACKEND_RAG 플로우:
1. PII masking (INPUT)
2. Intent classification
3. RAG search + BackendDataClient 병렬 호출
4. 두 결과를 BackendContextFormatter.format_mixed_context()로 통합
5. Generate response - LLM으로 답변 생성
6. PII masking (OUTPUT)
7. Return ChatResponse

Phase 12 Fallback 전략:
- RAG_INTERNAL: RAG 실패 시 → LLM-only로 진행 (fallback_reason: RAG_FAIL)
- BACKEND_API: Backend 실패 시 → "현재 정보 조회 불가" 안내 (fallback_reason: BACKEND_FAIL)
- MIXED_BACKEND_RAG:
  - RAG만 실패: Backend 데이터만으로 답변 (fallback_reason: RAG_FAIL)
  - Backend만 실패: RAG 데이터만으로 답변 (fallback_reason: BACKEND_FAIL)
- LLM 실패: 에러 응답 반환 (error_type: UPSTREAM_TIMEOUT/UPSTREAM_ERROR)
"""

import asyncio
import time
from typing import Dict, List, Optional, Tuple

from app.core.config import get_settings
from app.core.exceptions import ErrorType, ServiceType, UpstreamServiceError
from app.core.logging import get_logger
from app.core.metrics import (
    LOG_TAG_LLM_ERROR,
    LOG_TAG_LLM_FALLBACK,
    LOG_TAG_LLM_TIMEOUT,
    LOG_TAG_RAG_ERROR,
    LOG_TAG_RAG_FALLBACK,
    LOG_TAG_BACKEND_ERROR,
    LOG_TAG_BACKEND_FALLBACK,
    metrics,
)
from app.models.chat import (
    ChatAnswerMeta,
    ChatRequest,
    ChatResponse,
    ChatSource,
)
from app.models.intent import Domain, IntentType, MaskingStage, RouteType, UserRole
from app.models.router_types import (
    RouterResult,
    RouterRouteType,
    Tier0Intent,
    TIER0_ROUTING_POLICY,
)
from app.clients.backend_data_client import BackendDataClient
from app.clients.llm_client import LLMClient
from app.clients.milvus_client import MilvusSearchClient, get_milvus_client
from app.clients.ragflow_client import RagflowClient
from app.services.ai_log_service import AILogService
from app.services.backend_context_formatter import BackendContextFormatter
from app.services.guardrail_service import GuardrailService
from app.services.intent_service import IntentService
from app.services.pii_service import PiiService
from app.services.router_orchestrator import (
    OrchestrationResult,
    RouterOrchestrator,
)
from app.services.video_progress_service import VideoProgressService
from app.services.answer_guard_service import (
    AnswerGuardService,
    DebugInfo,
    RequestContext,
    get_answer_guard_service,
)
from app.utils.debug_log import (
    dbg_route,
    dbg_final_query,
    dbg_retrieval_top5,
    generate_request_id,
    is_debug_enabled,
)

logger = get_logger(__name__)


# =============================================================================
# Phase 12: Fallback 응답 메시지
# =============================================================================

# LLM 서비스 장애 시
LLM_FALLBACK_MESSAGE = (
    "죄송합니다. 현재 AI 서비스에 일시적인 문제가 발생했습니다. "
    "잠시 후 다시 시도해 주세요."
)

# Backend 데이터 조회 실패 시 (BACKEND_API 라우트)
BACKEND_FALLBACK_MESSAGE = (
    "현재 시스템에서 정보를 가져오는 데 문제가 발생했습니다. "
    "관리자 시스템에서 직접 확인하시거나, 잠시 후 다시 시도해 주세요."
)

# RAG 검색 실패 시 추가 안내
RAG_FAIL_NOTICE = (
    "\n\n※ 참고: 관련 문서 검색에 문제가 있어 일반적인 원칙만 안내드립니다. "
    "정확한 정책 정보는 담당 부서에 확인해 주세요."
)

# MIXED_BACKEND_RAG에서 Backend만 실패 시
MIXED_BACKEND_FAIL_NOTICE = (
    "\n\n※ 참고: 실제 현황 데이터를 조회하지 못했습니다. "
    "통계/현황 정보는 관리자 시스템에서 직접 확인해 주세요."
)


# =============================================================================
# Phase 14: RAG Gap 후보 판정
# =============================================================================

# RAG Gap 후보로 간주할 도메인 (POLICY, EDU만 대상)
RAG_GAP_TARGET_DOMAINS = {Domain.POLICY.value, Domain.EDU.value, "POLICY", "EDU"}

# RAG Gap 후보로 간주할 의도 (사규/교육 관련 QA만 대상)
RAG_GAP_TARGET_INTENTS = {
    IntentType.POLICY_QA.value,
    IntentType.EDUCATION_QA.value,
    # 추후 직무교육/4대교육 관련 Intent 추가 시 여기에 추가
    "POLICY_QA",
    "EDUCATION_QA",
    "EDU_QA",
    "EDU_COURSE_QA",
    "JOB_EDU_QA",
}

# RAG Gap 판정용 점수 임계값
RAG_GAP_SCORE_THRESHOLD = 0.4


def is_rag_gap_candidate(
    domain: str,
    intent: str,
    rag_source_count: int,
    rag_max_score: Optional[float],
    score_threshold: float = RAG_GAP_SCORE_THRESHOLD,
) -> bool:
    """
    RAG Gap 후보 여부를 판정합니다.

    POLICY/EDU 도메인에서 사규/교육 관련 질문인데 RAG 검색 결과가 없거나
    점수가 낮은 경우 True를 반환합니다.

    이 플래그는 "추후 사규/교육 콘텐츠 보완 추천"을 위한 것이며,
    최종 사용자 답변 내용에는 직접적인 영향을 주지 않습니다.

    Args:
        domain: 도메인 (Domain Enum value 또는 문자열)
        intent: 의도 (IntentType Enum value 또는 문자열)
        rag_source_count: RAG 검색 결과 개수
        rag_max_score: RAG 검색 결과 중 최고 점수 (None이면 결과 없음)
        score_threshold: RAG Gap으로 판정할 점수 임계값 (기본: 0.4)

    Returns:
        bool: RAG Gap 후보이면 True, 아니면 False

    Examples:
        >>> is_rag_gap_candidate("POLICY", "POLICY_QA", 0, None)
        True  # POLICY 도메인, 결과 0건

        >>> is_rag_gap_candidate("POLICY", "POLICY_QA", 3, 0.3)
        True  # POLICY 도메인, 최고 점수가 임계값 미만

        >>> is_rag_gap_candidate("POLICY", "POLICY_QA", 3, 0.8)
        False  # POLICY 도메인, 최고 점수가 충분히 높음

        >>> is_rag_gap_candidate("INCIDENT", "INCIDENT_REPORT", 0, None)
        False  # INCIDENT 도메인은 대상이 아님
    """
    # Step 1: 도메인 필터 - POLICY, EDU만 대상
    if domain not in RAG_GAP_TARGET_DOMAINS:
        return False

    # Step 2: 의도 필터 - 사규/교육 관련 QA만 대상
    if intent not in RAG_GAP_TARGET_INTENTS:
        return False

    # Step 3: RAG 결과 기준 판정
    # 검색 결과가 0건이면 Gap 후보
    if rag_source_count == 0:
        return True

    # 점수가 없으면 Gap이 아님 (결과가 있지만 점수가 없는 경우)
    if rag_max_score is None:
        return False

    # 최고 점수가 임계값 미만이면 Gap 후보
    return rag_max_score < score_threshold


# System prompt template for LLM (RAG context가 있는 경우)
# Phase 13: 조항 근거 명시 지침 추가
SYSTEM_PROMPT_WITH_RAG = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.
아래의 참고 문서 목록을 바탕으로 사용자의 질문에 한국어로 정확하고 친절하게 답변해 주세요.

답변 시 반드시 출처 문서와 해당 조항을 인용해 주세요.
가능하다면 답변 마지막에 "[참고 근거]" 섹션을 추가해서:
- 문서명
- 조문/항 번호 또는 위치 (예: 제10조 제2항, 제3장 > 제5조)
를 bullet으로 정리해 주세요.

예시:
[참고 근거]
- 연차휴가 관리 규정 제10조 (연차 이월) 제2항
- 인사관리 규정 제3장 근태관리 제5조 (지각/조퇴 처리 기준)

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

# Phase 11: MIXED_BACKEND_RAG용 LLM 시스템 프롬프트
SYSTEM_PROMPT_MIXED_BACKEND_RAG = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.

아래에 두 가지 정보가 제공됩니다:
1. [정책/규정 근거]: 관련 사내 규정/정책 문서
2. [실제 현황/통계]: 백엔드 시스템에서 조회한 실제 데이터

두 정보를 모두 고려하여 질문에 답변해 주세요:
1) 정책상 어떻게 규정되어 있는지
2) 현재 조직/부서의 실제 상황이 어떠한지
3) 다음에 취할 수 있는 일반적인 조치나 권고

주의사항:
- 구체적인 인사/징계 조치를 단정적으로 말하지 마세요.
- 개인 식별이 가능한 정보(실명, 사번 등)는 언급하지 마세요.
- 추측이나 거짓 정보를 제공하지 마세요.
"""

# Phase 11: BACKEND_API용 LLM 시스템 프롬프트
SYSTEM_PROMPT_BACKEND_API = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.

아래에 백엔드 시스템에서 조회한 실제 데이터가 제공됩니다.
이 데이터를 바탕으로 사용자의 질문에 친절하고 정확하게 답변해 주세요.

주의사항:
- 제공된 데이터 범위 내에서만 답변하세요.
- 추측이나 거짓 정보를 제공하지 마세요.
- 개인 식별이 가능한 정보는 언급하지 마세요.
"""


# =============================================================================
# Phase 22: Router Integration Constants
# =============================================================================

# 시스템 도움말 응답
SYSTEM_HELP_RESPONSE = """안녕하세요! CTRL+F AI 어시스턴트입니다.

제가 도와드릴 수 있는 것들:
- **사규/정책 질문**: 회사 규정, 보안 정책 등을 안내해 드립니다.
- **교육 관련**: 4대교육, 직무교육 내용을 설명해 드립니다.
- **HR 정보 조회**: 연차, 근태, 복지 등 개인 정보를 확인해 드립니다.
- **퀴즈**: 교육 퀴즈를 진행하거나 결과를 확인할 수 있습니다.

궁금한 점이 있으시면 편하게 질문해 주세요!"""

# UNKNOWN 라우트 응답
UNKNOWN_ROUTE_RESPONSE = (
    "죄송합니다. 질문을 이해하지 못했습니다. "
    "사규, 교육, HR 정보, 퀴즈 관련 질문을 해주시면 도움을 드릴 수 있습니다."
)

# 확인 응답 파싱용 키워드
CONFIRMATION_POSITIVE_KEYWORDS = frozenset([
    "예", "네", "응", "ㅇㅇ", "yes", "y", "진행", "시작", "확인", "좋아", "그래", "할래"
])
CONFIRMATION_NEGATIVE_KEYWORDS = frozenset([
    "아니오", "아니", "ㄴㄴ", "no", "n", "취소", "안해", "그만", "중단", "안할래"
])


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
        guardrail_service: Optional[GuardrailService] = None,
        backend_data_client: Optional[BackendDataClient] = None,
        router_orchestrator: Optional[RouterOrchestrator] = None,
        video_progress_service: Optional[VideoProgressService] = None,
        milvus_client: Optional[MilvusSearchClient] = None,
        answer_guard_service: Optional[AnswerGuardService] = None,
    ) -> None:
        """
        Initialize ChatService.

        Args:
            ragflow_client: RagflowClient instance. If None, creates a new instance.
            llm_client: LLMClient instance. If None, creates a new instance.
            pii_service: PiiService instance. If None, creates a new instance.
            intent_service: IntentService instance. If None, creates a new instance.
            ai_log_service: AILogService instance. If None, creates a new instance.
            guardrail_service: GuardrailService instance. If None, creates a new instance.
            backend_data_client: BackendDataClient instance. If None, creates a new instance.
            router_orchestrator: RouterOrchestrator instance. If None, creates a new instance. (Phase 22)
            video_progress_service: VideoProgressService instance. If None, creates a new instance. (Phase 22)
            milvus_client: MilvusSearchClient instance. If None and MILVUS_ENABLED=True, creates singleton. (Phase 24)
                              Pass custom services for testing or dependency injection.
            answer_guard_service: AnswerGuardService instance. If None, creates singleton. (Phase 39)
        """
        self._ragflow = ragflow_client or RagflowClient()
        self._llm = llm_client or LLMClient()
        self._pii = pii_service or PiiService()
        self._intent = intent_service or IntentService()
        self._ai_log = ai_log_service or AILogService(pii_service=self._pii)
        self._guardrail = guardrail_service or GuardrailService()
        # Phase 11: 백엔드 비즈니스 데이터 클라이언트
        self._backend_data = backend_data_client or BackendDataClient()
        self._context_formatter = BackendContextFormatter()

        # Phase 22: Router Orchestrator & Video Progress Service
        self._router_orchestrator = router_orchestrator or RouterOrchestrator(
            llm_client=self._llm
        )
        self._video_progress = video_progress_service or VideoProgressService()

        # Phase 39: Answer Guard Service (답변 품질 가드레일)
        self._answer_guard = answer_guard_service or get_answer_guard_service()

        # Phase 39: 마지막 에러 사유 저장 (불만 빠른 경로용)
        self._last_error_reason: Optional[str] = None

        # Phase 24: Milvus Vector Search Client
        # MILVUS_ENABLED=True 설정 시 RAGFlow 대신 Milvus 사용
        settings = get_settings()
        self._milvus_enabled = settings.MILVUS_ENABLED
        if self._milvus_enabled:
            self._milvus = milvus_client or get_milvus_client()
            logger.info("Milvus vector search enabled (Phase 24)")
        else:
            self._milvus = None

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

        # Phase 41: RAG 디버그용 request_id 생성
        request_id = generate_request_id()

        logger.info(
            f"Processing chat request: session_id={req.session_id}, "
            f"user_id={req.user_id}, user_role={req.user_role}, request_id={request_id}"
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

        # =====================================================================
        # Phase 39: [E] Complaint Fast Path (불만/욕설 빠른 경로)
        # =====================================================================
        # intent 분류 전에 먼저 실행 (전처리)
        # 불만 키워드 감지 시 RAG/툴 호출 없이 즉시 응답
        complaint_response = self._answer_guard.check_complaint_fast_path(
            user_query=masked_query,
            last_error_reason=self._last_error_reason,
        )
        if complaint_response:
            logger.info("Complaint fast path triggered - returning immediate response")
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            return ChatResponse(
                answer=complaint_response,
                sources=[],
                meta=ChatAnswerMeta(
                    route=RouteType.LLM_ONLY.value,
                    intent=IntentType.GENERAL_CHAT.value,
                    domain="GENERAL",
                    masked=pii_input.has_pii,
                    has_pii_input=pii_input.has_pii,
                    latency_ms=latency_ms,
                ),
            )

        # =====================================================================
        # Phase 22: Router Orchestrator Integration (Optional)
        # =====================================================================
        # RouterOrchestrator 사용 여부는 명시적 플래그(ROUTER_ORCHESTRATOR_ENABLED)로 결정합니다.
        # Phase 22 수정: bool(llm_base_url) 대신 명시적 플래그 사용
        from app.core.config import get_settings
        settings = get_settings()
        use_router_orchestrator = settings.ROUTER_ORCHESTRATOR_ENABLED

        orchestration_result: Optional[OrchestrationResult] = None

        if use_router_orchestrator:
            # Call orchestrator.route() to get routing decision with clarify/confirm handling
            orchestration_result = await self._router_orchestrator.route(
                user_query=masked_query,
                session_id=req.session_id,
            )

            # Only use orchestrator result if it's not an LLM failure fallback
            # (LLM failure typically results in UNKNOWN with very low confidence)
            is_llm_fallback = (
                orchestration_result.router_result.tier0_intent == Tier0Intent.UNKNOWN
                and orchestration_result.router_result.confidence < 0.5
            )

            if not is_llm_fallback:
                # Handle clarify/confirmation responses
                if orchestration_result.needs_user_response:
                    logger.info(
                        f"Router requires user response: "
                        f"intent={orchestration_result.router_result.tier0_intent.value}, "
                        f"needs_clarify={orchestration_result.router_result.needs_clarify}, "
                        f"requires_confirmation={orchestration_result.router_result.requires_confirmation}"
                    )
                    return self._create_router_response(
                        orchestration_result=orchestration_result,
                        start_time=start_time,
                        has_pii=pii_input.has_pii,
                    )

                # Handle SYSTEM_HELP route type
                if orchestration_result.router_result.route_type == RouterRouteType.ROUTE_SYSTEM_HELP:
                    return self._create_system_help_response(start_time, pii_input.has_pii)

                # Handle UNKNOWN route type (only if high confidence, not LLM fallback)
                if (orchestration_result.router_result.route_type == RouterRouteType.ROUTE_UNKNOWN
                    and orchestration_result.router_result.confidence >= 0.5):
                    return self._create_unknown_route_response(start_time, pii_input.has_pii)

        # Step 3: Intent Classification and Routing
        # Use IntentService for classification (always called for consistency)
        intent_result = self._intent.classify(
            req=req,
            user_query=masked_query,
        )

        # Override with router orchestrator result if available and valid
        if (orchestration_result is not None
            and orchestration_result.router_result.tier0_intent != Tier0Intent.UNKNOWN):
            router_result = orchestration_result.router_result
            intent = self._map_tier0_to_intent(router_result.tier0_intent) or intent_result.intent
            domain = router_result.domain.value if router_result.domain else (intent_result.domain or req.domain or "POLICY")
            route = self._map_router_route_to_route_type(router_result.route_type) or intent_result.route

            logger.info(
                f"Intent classification (Phase 22 Orchestrator): intent={intent.value}, "
                f"route={route.value}, domain={domain}, "
                f"tier0_intent={router_result.tier0_intent.value}"
            )

            # Phase 41: [A] route 결정 직후 디버그 로그
            dbg_route(
                request_id=request_id,
                user_message=masked_query,
                intent=intent.value,
                domain=domain,
                tool=route.value,
                reason=f"orchestrator: tier0={router_result.tier0_intent.value}",
                confidence=router_result.confidence,
            )
        else:
            # Use IntentService result directly
            intent = intent_result.intent
            domain = intent_result.domain or req.domain or "POLICY"
            route = intent_result.route

            logger.info(
                f"Intent classification: intent={intent.value}, "
                f"route={route.value}, domain={domain}"
            )

            # Phase 41: [A] route 결정 직후 디버그 로그
            dbg_route(
                request_id=request_id,
                user_message=masked_query,
                intent=intent.value,
                domain=domain,
                tool=route.value,
                reason=f"rule-based: IntentService",
            )

        # Step 4: RAG Search / Backend Data (based on route)
        # Phase 11: BACKEND_API, MIXED_BACKEND_RAG 실제 처리 로직 구현
        # Phase 12: latency 측정 및 fallback 처리 개선
        sources: List[ChatSource] = []
        rag_search_attempted = False
        rag_search_failed = False
        backend_context: str = ""  # Phase 11: 백엔드 데이터 컨텍스트
        backend_data_fetched = False  # Phase 11: 백엔드 데이터 조회 여부
        rag_latency_ms: Optional[int] = None  # Phase 12: RAG latency

        # RAG만 사용하는 경로 (MIXED_BACKEND_RAG 제외)
        rag_only_routes = {
            RouteType.RAG_INTERNAL,
            RouteType.ROUTE_RAG_INTERNAL,  # 레거시 호환
        }

        # MIXED: RAG + Backend 둘 다 사용하는 경로
        mixed_routes = {
            RouteType.MIXED_BACKEND_RAG,
        }

        # LLM만 사용하는 경로
        llm_only_routes = {
            RouteType.LLM_ONLY,
            RouteType.ROUTE_LLM_ONLY,  # 레거시 호환
            RouteType.TRAINING,
            RouteType.ROUTE_TRAINING,  # 레거시 호환
        }

        # 백엔드 API만 사용하는 경로 (RAG 없음)
        backend_api_routes = {
            RouteType.BACKEND_API,
        }

        # INCIDENT 경로
        incident_routes = {
            RouteType.INCIDENT,
            RouteType.ROUTE_INCIDENT,  # 레거시 호환
        }

        # =====================================================================
        # Phase 11/12: 라우트별 처리 로직 (latency 측정 포함)
        # =====================================================================

        if route in rag_only_routes:
            # RAG_INTERNAL: RAG만 사용
            rag_search_attempted = True
            rag_start = time.perf_counter()
            sources, rag_search_failed = await self._perform_rag_search_with_fallback(
                masked_query, domain, req, request_id=request_id
            )
            rag_latency_ms = int((time.perf_counter() - rag_start) * 1000)

        elif route in mixed_routes:
            # MIXED_BACKEND_RAG: RAG + Backend 병렬 호출
            rag_search_attempted = True
            logger.info(f"MIXED_BACKEND_RAG route: Fetching RAG + Backend data in parallel")

            # Phase 12: 병렬 호출에서 각각의 실패를 독립적으로 처리
            rag_start = time.perf_counter()
            rag_task = self._perform_rag_search_with_fallback(
                masked_query, domain, req, request_id=request_id
            )
            backend_task = self._fetch_backend_data_for_mixed(
                user_role=intent_result.user_role,
                domain=domain,
                intent=intent,
                user_id=req.user_id,
                department=req.department,
            )

            (sources, rag_search_failed), backend_context = await asyncio.gather(
                rag_task, backend_task
            )
            rag_latency_ms = int((time.perf_counter() - rag_start) * 1000)
            backend_data_fetched = bool(backend_context.strip())

            logger.info(
                f"MIXED_BACKEND_RAG: RAG sources={len(sources)}, rag_failed={rag_search_failed}, "
                f"backend_data_fetched={backend_data_fetched}, rag_latency_ms={rag_latency_ms}"
            )

        elif route in backend_api_routes:
            # BACKEND_API: Backend만 사용 (RAG 없음)
            logger.info(f"BACKEND_API route: Fetching backend data only")

            backend_context = await self._fetch_backend_data_for_api(
                user_role=intent_result.user_role,
                domain=domain,
                intent=intent,
                user_id=req.user_id,
                department=req.department,
            )
            backend_data_fetched = bool(backend_context.strip())
            logger.info(f"BACKEND_API: backend_data_fetched={backend_data_fetched}")

        elif route in llm_only_routes:
            # LLM only routes - no RAG, no Backend
            logger.debug(f"Skipping RAG/Backend for route: {route.value}")
            sources = []

        elif route in incident_routes:
            # INCIDENT 경로는 별도 Incident 모듈로 라우팅 예정
            # 현재는 LLM only로 처리
            logger.debug("INCIDENT route: Currently using LLM only (TODO: Incident module)")
            sources = []

        else:
            # Fallback or error routes
            logger.debug(f"Route {route.value}: No RAG/Backend")
            sources = []

        # =====================================================================
        # Phase 39: [A] Answerability Gate (답변 가능 여부 게이트)
        # =====================================================================
        # 내부 규정/사규/정책 질문에서 RAG 근거가 없으면 답변 생성 금지
        # 고정 템플릿으로 "문서 근거 없음" 안내하고 종료

        # Tier0Intent 결정: orchestration_result가 있으면 사용, 없으면 매핑
        tier0_intent = Tier0Intent.UNKNOWN
        if orchestration_result is not None:
            tier0_intent = orchestration_result.router_result.tier0_intent
        elif intent == IntentType.POLICY_QA:
            tier0_intent = Tier0Intent.POLICY_QA
        elif intent == IntentType.EDUCATION_QA:
            tier0_intent = Tier0Intent.EDUCATION_QA

        # RouterRouteType 매핑
        router_route_type = self._map_route_type_to_router_route_type(route)

        # 디버그 정보 생성
        debug_info = self._answer_guard.create_debug_info(
            intent=tier0_intent,
            domain=domain,
            route_type=router_route_type,
            route_reason=f"intent={intent.value}, rag_sources={len(sources)}",
        )

        # Answerability 체크
        is_answerable, no_evidence_template = self._answer_guard.check_answerability(
            intent=tier0_intent,
            sources=sources,
            route_type=router_route_type,
            top_k=5,  # 기본 topK 값
            debug_info=debug_info,
        )

        if not is_answerable:
            # 답변 불가 - 고정 템플릿으로 즉시 종료
            logger.warning(
                f"Answerability BLOCKED: intent={tier0_intent.value}, "
                f"sources={len(sources)}, returning no-evidence template"
            )
            # 마지막 에러 사유 저장 (불만 빠른 경로용)
            self._last_error_reason = "NO_RAG_EVIDENCE"

            # 디버그 로그 출력
            self._answer_guard.log_debug_info(debug_info, req.session_id)

            latency_ms = int((time.perf_counter() - start_time) * 1000)
            return ChatResponse(
                answer=no_evidence_template,
                sources=[],
                meta=ChatAnswerMeta(
                    route=route.value,
                    intent=intent.value,
                    domain=domain,
                    masked=pii_input.has_pii,
                    has_pii_input=pii_input.has_pii,
                    rag_used=False,
                    rag_source_count=0,
                    latency_ms=latency_ms,
                    error_type="NO_RAG_EVIDENCE",
                    error_message="내부 규정 질문에 대한 RAG 근거 없음",
                ),
            )

        # Step 5: Build LLM prompt messages (with guardrails)
        # Phase 11: 라우트에 따라 다른 프롬프트 빌더 사용
        if route in mixed_routes:
            # MIXED_BACKEND_RAG: RAG + Backend 통합 컨텍스트
            llm_messages = self._build_mixed_llm_messages(
                user_query=masked_query,
                sources=sources,
                backend_context=backend_context,
                domain=domain,
                user_role=intent_result.user_role,
                intent=intent,
            )
        elif route in backend_api_routes:
            # BACKEND_API: Backend 컨텍스트만
            llm_messages = self._build_backend_api_llm_messages(
                user_query=masked_query,
                backend_context=backend_context,
                user_role=intent_result.user_role,
                domain=domain,
                intent=intent,
            )
        else:
            # 기존 로직: RAG_INTERNAL, LLM_ONLY 등
            llm_messages = self._build_llm_messages(
                user_query=masked_query,
                sources=sources,
                req=req,
                rag_attempted=rag_search_attempted,
                user_role=intent_result.user_role,
                domain=domain,
                intent=intent,
            )

        # Step 6: Generate LLM response
        # Phase 12: latency 측정 및 에러 처리 개선
        raw_answer: str
        final_route = route
        error_type: Optional[str] = None
        error_message: Optional[str] = None
        fallback_reason: Optional[str] = None
        llm_latency_ms: Optional[int] = None

        try:
            # Phase 12: LLM 호출 with latency 측정
            llm_start = time.perf_counter()
            raw_answer = await self._llm.generate_chat_completion(
                messages=llm_messages,
                model=None,  # Use server default
                temperature=0.2,
                max_tokens=1024,
            )
            llm_latency_ms = int((time.perf_counter() - llm_start) * 1000)

            # RAG 시도했지만 결과 없으면 안내 문구 추가
            if rag_search_attempted and not sources and not rag_search_failed:
                # LLM 응답에 안내 문구 추가 (RAG 결과 0건인 경우)
                raw_answer = raw_answer.rstrip() + NO_RAG_RESULTS_NOTICE

            # Phase 12: RAG 실패로 인한 fallback인 경우 안내 추가
            if rag_search_failed:
                raw_answer = raw_answer.rstrip() + RAG_FAIL_NOTICE
                fallback_reason = "RAG_FAIL"

            # Phase 12: BACKEND_API에서 Backend 실패 시 fallback 안내
            if route in backend_api_routes and not backend_data_fetched:
                raw_answer = raw_answer.rstrip() + MIXED_BACKEND_FAIL_NOTICE
                fallback_reason = "BACKEND_FAIL"

            # Phase 12: MIXED_BACKEND_RAG에서 부분 실패 시 안내
            if route in mixed_routes:
                if rag_search_failed and backend_data_fetched:
                    # RAG만 실패
                    raw_answer = raw_answer.rstrip() + RAG_FAIL_NOTICE
                    fallback_reason = "RAG_FAIL"
                elif not rag_search_failed and not backend_data_fetched:
                    # Backend만 실패
                    raw_answer = raw_answer.rstrip() + MIXED_BACKEND_FAIL_NOTICE
                    fallback_reason = "BACKEND_FAIL"

        except UpstreamServiceError as e:
            # Phase 12: LLM UpstreamServiceError 처리
            llm_latency_ms = int((time.perf_counter() - llm_start) * 1000) if 'llm_start' in dir() else None
            logger.error(f"LLM generation failed: {e}")
            raw_answer = LLM_FALLBACK_MESSAGE
            final_route = RouteType.ERROR
            error_type = e.error_type.value
            error_message = e.message

            # Phase 12: 메트릭 수집
            if e.is_timeout:
                metrics.increment_error(LOG_TAG_LLM_TIMEOUT)
            else:
                metrics.increment_error(LOG_TAG_LLM_ERROR)
            metrics.increment_error(LOG_TAG_LLM_FALLBACK)

        except Exception as e:
            # 기타 예외
            llm_latency_ms = int((time.perf_counter() - llm_start) * 1000) if 'llm_start' in dir() else None
            logger.exception("LLM generation failed with unexpected error")
            raw_answer = LLM_FALLBACK_MESSAGE
            final_route = RouteType.ERROR
            error_type = ErrorType.INTERNAL_ERROR.value
            error_message = f"Unexpected error: {type(e).__name__}"

            # Phase 12: 메트릭 수집
            metrics.increment_error(LOG_TAG_LLM_ERROR)
            metrics.increment_error(LOG_TAG_LLM_FALLBACK)

        # =====================================================================
        # Phase 39: [B] Citation Hallucination Guard (가짜 조항 인용 차단)
        # =====================================================================
        # 답변에 "제N조/조항/항" 패턴이 있는데 RAG 소스에 없으면 답변 폐기
        # LLM_ONLY 경로에서는 "근거/조항 섹션"을 절대 붙이지 않음
        if final_route != RouteType.ERROR:  # 에러 응답은 검증 스킵
            citation_valid, validated_answer = self._answer_guard.validate_citation(
                answer=raw_answer,
                sources=sources,
                debug_info=debug_info,
            )

            if not citation_valid:
                logger.warning("Citation validation FAILED - replacing with blocked template")
                raw_answer = validated_answer  # 차단 템플릿으로 교체
                error_type = "CITATION_HALLUCINATION"
                error_message = "가짜 조항 인용 감지됨"
                self._last_error_reason = "CITATION_HALLUCINATION"
            else:
                raw_answer = validated_answer

        # =====================================================================
        # Phase 39: [D] Korean-only Output Enforcement (언어 가드레일)
        # =====================================================================
        # 중국어 혼입 탐지 시: 한국어 강제 재생성 1회 시도
        # 재생성도 실패하면 "언어 오류" 템플릿으로 대체
        if final_route != RouteType.ERROR:  # 에러 응답은 검증 스킵
            async def llm_regenerate(prompt: str) -> str:
                """LLM 재생성 함수."""
                return await self._llm.generate_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    model=None,
                    temperature=0.2,
                    max_tokens=1024,
                )

            korean_valid, korean_answer = await self._answer_guard.enforce_korean_output(
                answer=raw_answer,
                llm_regenerate_fn=llm_regenerate,
                original_query=masked_query,
                debug_info=debug_info,
            )

            if not korean_valid:
                logger.warning("Korean enforcement FAILED - replacing with error template")
                raw_answer = korean_answer  # 언어 오류 템플릿으로 교체
                error_type = "LANGUAGE_ERROR"
                error_message = "중국어 혼입 감지됨"
                self._last_error_reason = "LANGUAGE_ERROR"
            else:
                raw_answer = korean_answer

        # Phase 39: 성공 시 마지막 에러 사유 초기화
        if error_type is None:
            self._last_error_reason = None

        # Phase 39: 디버그 로그 출력
        self._answer_guard.log_debug_info(debug_info, req.session_id)

        # Step 7: PII Masking (OUTPUT stage)
        pii_output = await self._pii.detect_and_mask(
            text=raw_answer,
            stage=MaskingStage.OUTPUT,
        )
        masked_answer = pii_output.masked_text

        if pii_output.has_pii:
            logger.info(
                f"PII detected in output: {len(pii_output.tags)} entities masked"
            )

        # Step 7.5: Apply answer prefix guardrails
        # Phase 10: 역할별 답변 앞 안내 문구 적용
        final_answer = self._guardrail.apply_to_answer(
            answer=masked_answer,
            user_role=intent_result.user_role,
            domain=domain,
            intent=intent,
        )

        # Determine if any PII was detected (input or output)
        has_pii = pii_input.has_pii or pii_output.has_pii

        # Calculate latency
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        # Determine if RAG was actually used (results > 0)
        # Phase 6 정책: rag_used = len(sources) > 0
        rag_used = len(sources) > 0

        # Phase 12: Backend latency 가져오기
        backend_latency_ms: Optional[int] = None
        if backend_data_fetched or route in backend_api_routes or route in mixed_routes:
            backend_latency_ms = self._backend_data.get_last_latency_ms()

        # Phase 14: RAG Gap 후보 판정
        # RAG 결과에서 최고 점수 추출
        rag_max_score: Optional[float] = None
        if sources:
            scores = [s.score for s in sources if s.score is not None]
            rag_max_score = max(scores) if scores else None

        # RAG Gap 후보 여부 계산
        rag_gap_candidate_flag = is_rag_gap_candidate(
            domain=domain,
            intent=intent.value,
            rag_source_count=len(sources),
            rag_max_score=rag_max_score,
        )

        # Build metadata with extended fields for logging
        # Phase 10: user_role 추가
        # Phase 12: error_type, error_message, fallback_reason, 개별 latency 추가
        meta = ChatAnswerMeta(
            user_role=intent_result.user_role.value,  # Phase 10: 역할 정보 포함
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
            # Phase 12: 에러 정보 및 개별 latency
            error_type=error_type,
            error_message=error_message,
            fallback_reason=fallback_reason,
            rag_latency_ms=rag_latency_ms if rag_search_attempted else None,
            llm_latency_ms=llm_latency_ms,
            backend_latency_ms=backend_latency_ms,
            # Phase 14: RAG Gap 후보 플래그
            rag_gap_candidate=rag_gap_candidate_flag,
        )

        # Phase 12: 메트릭 기록
        metrics.increment_request(final_route.value)
        if llm_latency_ms:
            metrics.record_latency("llm", llm_latency_ms)
        if rag_latency_ms:
            metrics.record_latency("ragflow", rag_latency_ms)
        if backend_latency_ms:
            metrics.record_latency("backend", backend_latency_ms)

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
            rag_gap_candidate=rag_gap_candidate_flag,
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
        rag_gap_candidate: bool = False,
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
                rag_gap_candidate=rag_gap_candidate,
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
        user_role: Optional["UserRole"] = None,
        domain: Optional[str] = None,
        intent: Optional["IntentType"] = None,
    ) -> List[Dict[str, str]]:
        """
        Build message list for LLM chat completion.

        Constructs a message array with:
        1. Guardrail prefix (Phase 10: 역할별 가드레일)
        2. System prompt with instructions (RAG context 유무에 따라 다름)
        3. RAG context (if sources available)
        4. User's actual question

        Args:
            user_query: The user's question text (masked if PII was detected)
            sources: List of ChatSource from RAG search
            req: Original ChatRequest for context
            rag_attempted: Whether RAG search was attempted
            user_role: User role for guardrails (Phase 10)
            domain: Domain for guardrails (Phase 10)
            intent: Intent type for guardrails (Phase 10)

        Returns:
            List of message dicts with 'role' and 'content' keys
        """
        from app.models.intent import IntentType, UserRole

        messages: List[Dict[str, str]] = []

        # Phase 10: 역할별 가드레일을 system prompt 앞에 추가
        guardrail_prefix = ""
        if user_role and domain and intent:
            guardrail_prefix = self._guardrail.get_system_prompt_prefix(
                user_role=user_role,
                domain=domain,
                intent=intent,
            )

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
            # RAG 시도하지 않은 경우 (LLM_ONLY 등)
            system_content = SYSTEM_PROMPT_WITH_RAG
            system_content += "\n\n참고 문서: (검색 대상 아님)"

        # Combine guardrail prefix with system content
        if guardrail_prefix:
            system_content = guardrail_prefix + "\n\n" + system_content

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

        Phase 13: 조항 정보(article_label, article_path) 포함하도록 확장.
        Creates a numbered list of sources with doc_id, title, article location, and snippet.

        Args:
            sources: List of ChatSource from RAG search

        Returns:
            Formatted string with source information

        Example output:
            [근거 1]
            - 문서: 연차휴가 관리 규정 (2025.01 개정)
            - 위치: 제10조 (연차 이월) 제2항
            - 내용: "연차는 다음 해 말일까지 최대 10일까지 이월할 수 있다..."
        """
        lines: List[str] = []

        for i, source in enumerate(sources, start=1):
            lines.append(f"[근거 {i}]")

            # 문서 제목
            doc_line = f"- 문서: {source.title}"
            if source.page:
                doc_line += f" (p.{source.page})"
            lines.append(doc_line)

            # Phase 13: 조항 위치 정보
            if source.article_label or source.article_path:
                location = source.article_path or source.article_label
                lines.append(f"- 위치: {location}")

            # 관련도 점수 (디버깅용, 선택적)
            if source.score:
                lines.append(f"- 관련도: {source.score:.2f}")

            # 발췌 내용
            if source.snippet:
                snippet = source.snippet[:400]
                if len(source.snippet) > 400:
                    snippet += "..."
                lines.append(f"- 내용: {snippet}")

            lines.append("")  # 빈 줄로 구분

        return "\n".join(lines).strip()

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
                route=RouteType.FALLBACK.value,
                masked=has_pii,
                latency_ms=latency_ms,
                rag_used=False,
                rag_source_count=0,
            ),
        )

    # =========================================================================
    # Phase 11/12: RAG 검색 헬퍼
    # =========================================================================

    async def _perform_rag_search(
        self,
        query: str,
        domain: str,
        req: ChatRequest,
    ) -> List[ChatSource]:
        """
        RAG 검색을 수행합니다.

        Args:
            query: 검색 쿼리 (마스킹된 상태)
            domain: 도메인
            req: 원본 요청

        Returns:
            List[ChatSource]: RAG 검색 결과
        """
        try:
            sources = await self._ragflow.search_as_sources(
                query=query,
                domain=domain,
                user_role=req.user_role,
                department=req.department,
                top_k=5,
            )
            logger.info(f"RAG search returned {len(sources)} sources")

            if not sources:
                logger.warning(
                    f"RAG search returned no results for query: {query[:50]}..."
                )

            return sources

        except Exception as e:
            logger.exception(f"RAG search failed: {e}")
            return []

    async def _perform_rag_search_with_fallback(
        self,
        query: str,
        domain: str,
        req: ChatRequest,
        request_id: Optional[str] = None,
    ) -> Tuple[List[ChatSource], bool]:
        """
        RAG 검색을 수행하고 실패 여부를 함께 반환합니다.

        Phase 12: fallback 처리를 위해 실패 여부를 명시적으로 반환.
        Phase 24: MILVUS_ENABLED=True 시 Milvus 벡터 검색 사용.
        Phase 41: RAG 디버그 로그 추가.

        Args:
            query: 검색 쿼리 (마스킹된 상태)
            domain: 도메인
            req: 원본 요청
            request_id: 디버그용 요청 ID (Phase 41)

        Returns:
            Tuple[List[ChatSource], bool]: (검색 결과, 실패 여부)
            - 실패 여부: 예외 발생 시 True, 정상 (0건 포함) 시 False
        """
        # Phase 41: [C] final_query 디버그 로그
        # 현재 프로젝트에는 query rewrite가 없으므로 원본 쿼리만 로깅
        if request_id:
            dbg_final_query(
                request_id=request_id,
                original_query=query,
                rewritten_query=None,  # 리라이트 없음
                keywords=None,  # 별도 키워드 추출 없음
            )

        try:
            # Phase 24: Milvus 사용 시 Milvus 클라이언트로 검색
            if self._milvus_enabled and self._milvus:
                logger.info(f"Using Milvus for vector search (Phase 24)")
                sources = await self._milvus.search_as_sources(
                    query=query,
                    domain=domain,
                    user_role=req.user_role,
                    department=req.department,
                    top_k=5,
                    request_id=request_id,  # Phase 41: request_id 전달
                )
            else:
                # 기존 RAGFlow 검색
                sources = await self._ragflow.search_as_sources(
                    query=query,
                    domain=domain,
                    user_role=req.user_role,
                    department=req.department,
                    top_k=5,
                )

            search_backend = "Milvus" if (self._milvus_enabled and self._milvus) else "RAGFlow"
            logger.info(f"{search_backend} search returned {len(sources)} sources")

            if not sources:
                logger.warning(
                    f"{search_backend} search returned no results for query: {query[:50]}..."
                )

            # Phase 41: [D] retrieval_top5 디버그 로그 (RAGFlow 경로용)
            # Milvus는 milvus_client.py에서 직접 로깅
            if request_id and not (self._milvus_enabled and self._milvus):
                top5_results = [
                    {
                        "doc_title": s.title,
                        "chunk_id": s.doc_id,
                        "score": s.score,
                    }
                    for s in sources[:5]
                ]
                dbg_retrieval_top5(request_id=request_id, results=top5_results)

            # 0건도 정상 응답 (실패 아님)
            return sources, False

        except UpstreamServiceError as e:
            # Phase 12: UpstreamServiceError 처리 (fallback으로 진행)
            logger.error(f"RAG search failed with UpstreamServiceError: {e}")
            metrics.increment_error(LOG_TAG_RAG_ERROR)
            metrics.increment_error(LOG_TAG_RAG_FALLBACK)
            return [], True

        except Exception as e:
            logger.exception(f"RAG search failed: {e}")
            metrics.increment_error(LOG_TAG_RAG_ERROR)
            metrics.increment_error(LOG_TAG_RAG_FALLBACK)
            return [], True

    # =========================================================================
    # Phase 11: Backend 데이터 조회 헬퍼
    # =========================================================================

    async def _fetch_backend_data_for_api(
        self,
        user_role: UserRole,
        domain: str,
        intent: IntentType,
        user_id: str,
        department: Optional[str] = None,
    ) -> str:
        """
        BACKEND_API 라우트용 백엔드 데이터를 조회합니다.

        역할×도메인×의도 조합에 따라 적절한 BackendDataClient 메서드를 호출하고,
        결과를 LLM 컨텍스트 텍스트로 변환합니다.

        Args:
            user_role: 사용자 역할
            domain: 도메인
            intent: 의도
            user_id: 사용자 ID
            department: 부서 ID

        Returns:
            str: LLM 컨텍스트용 텍스트

        Phase 11 역할×도메인×의도 매핑:
        - EMPLOYEE × EDU_STATUS → get_employee_edu_status(user_id)
        - EMPLOYEE × INCIDENT_REPORT → get_report_guide()
        - ADMIN × EDU_STATUS → get_department_edu_stats(department)
        """
        try:
            # EMPLOYEE × EDU_STATUS: 본인 교육 현황
            if user_role == UserRole.EMPLOYEE and intent == IntentType.EDU_STATUS:
                response = await self._backend_data.get_employee_edu_status(user_id)
                if response.success:
                    return self._context_formatter.format_edu_status_for_llm(response.data)

            # EMPLOYEE × INCIDENT_REPORT: 신고 안내
            elif user_role == UserRole.EMPLOYEE and intent == IntentType.INCIDENT_REPORT:
                response = await self._backend_data.get_report_guide()
                if response.success:
                    return self._context_formatter.format_report_guide_for_llm(response.data)

            # ADMIN × EDU_STATUS: 부서 교육 통계
            elif user_role == UserRole.ADMIN and intent == IntentType.EDU_STATUS:
                response = await self._backend_data.get_department_edu_stats(department)
                if response.success:
                    return self._context_formatter.format_edu_stats_for_llm(response.data)

            # 기타 조합: 데이터 없음
            logger.debug(
                f"No backend data mapping for: role={user_role.value}, "
                f"domain={domain}, intent={intent.value}"
            )
            return ""

        except Exception as e:
            logger.warning(f"Backend data fetch failed: {e}")
            return ""

    async def _fetch_backend_data_for_mixed(
        self,
        user_role: UserRole,
        domain: str,
        intent: IntentType,
        user_id: str,
        department: Optional[str] = None,
    ) -> str:
        """
        MIXED_BACKEND_RAG 라우트용 백엔드 데이터를 조회합니다.

        Args:
            user_role: 사용자 역할
            domain: 도메인
            intent: 의도
            user_id: 사용자 ID
            department: 부서 ID

        Returns:
            str: LLM 컨텍스트용 텍스트

        Phase 11 역할×도메인×의도 매핑:
        - ADMIN × INCIDENT → get_incident_overview()
        - ADMIN × EDU_STATUS → get_department_edu_stats()
        - INCIDENT_MANAGER × INCIDENT → get_incident_overview()
        """
        try:
            # ADMIN × INCIDENT: 사고 현황 통계
            if user_role == UserRole.ADMIN and domain == "INCIDENT":
                response = await self._backend_data.get_incident_overview()
                if response.success:
                    return self._context_formatter.format_incident_overview_for_llm(response.data)

            # ADMIN × EDU_STATUS: 부서 교육 통계 (MIXED에서도 사용 가능)
            elif user_role == UserRole.ADMIN and intent == IntentType.EDU_STATUS:
                response = await self._backend_data.get_department_edu_stats(department)
                if response.success:
                    return self._context_formatter.format_edu_stats_for_llm(response.data)

            # INCIDENT_MANAGER × INCIDENT: 사고 현황
            elif user_role == UserRole.INCIDENT_MANAGER and domain == "INCIDENT":
                response = await self._backend_data.get_incident_overview()
                if response.success:
                    return self._context_formatter.format_incident_overview_for_llm(response.data)

            # 기타 조합: 데이터 없음
            logger.debug(
                f"No mixed backend data mapping for: role={user_role.value}, "
                f"domain={domain}, intent={intent.value}"
            )
            return ""

        except Exception as e:
            logger.warning(f"Mixed backend data fetch failed: {e}")
            return ""

    # =========================================================================
    # Phase 11: MIXED_BACKEND_RAG용 LLM 메시지 빌더
    # =========================================================================

    def _build_mixed_llm_messages(
        self,
        user_query: str,
        sources: List[ChatSource],
        backend_context: str,
        domain: str,
        user_role: UserRole,
        intent: IntentType,
    ) -> List[Dict[str, str]]:
        """
        MIXED_BACKEND_RAG용 LLM 메시지를 구성합니다.

        RAG 결과와 백엔드 데이터를 통합하여 LLM 프롬프트를 구성합니다.

        Args:
            user_query: 사용자 질문
            sources: RAG 검색 결과
            backend_context: 백엔드 데이터 포맷팅 결과
            domain: 도메인
            user_role: 사용자 역할
            intent: 의도

        Returns:
            List[Dict[str, str]]: LLM 메시지 목록
        """
        messages: List[Dict[str, str]] = []

        # 가드레일 prefix
        guardrail_prefix = self._guardrail.get_system_prompt_prefix(
            user_role=user_role,
            domain=domain,
            intent=intent,
        )

        # RAG 컨텍스트 포맷팅
        rag_context = ""
        if sources:
            rag_context = self._format_sources_for_prompt(sources)

        # RAG + Backend 통합 컨텍스트
        mixed_context = self._context_formatter.format_mixed_context(
            rag_context=rag_context,
            backend_context=backend_context,
            domain=domain,
        )

        # 시스템 프롬프트 구성
        system_content = SYSTEM_PROMPT_MIXED_BACKEND_RAG
        system_content += f"\n\n{mixed_context}"

        if guardrail_prefix:
            system_content = guardrail_prefix + "\n\n" + system_content

        messages.append({
            "role": "system",
            "content": system_content,
        })

        messages.append({
            "role": "user",
            "content": user_query,
        })

        return messages

    # =========================================================================
    # Phase 22: Router Orchestrator Helper Methods
    # =========================================================================

    def _create_router_response(
        self,
        orchestration_result: OrchestrationResult,
        start_time: float,
        has_pii: bool,
    ) -> ChatResponse:
        """
        RouterOrchestrator의 되묻기/확인 응답을 ChatResponse로 변환합니다.

        Args:
            orchestration_result: 오케스트레이션 결과
            start_time: 요청 시작 시간
            has_pii: PII 검출 여부

        Returns:
            ChatResponse: 되묻기/확인 응답
        """
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        router_result = orchestration_result.router_result

        # Route 결정
        if router_result.needs_clarify:
            route_value = "CLARIFY"
        elif router_result.requires_confirmation:
            route_value = "CONFIRMATION"
        else:
            route_value = router_result.route_type.value

        return ChatResponse(
            answer=orchestration_result.response_message,
            sources=[],
            meta=ChatAnswerMeta(
                used_model=None,
                route=route_value,
                intent=router_result.tier0_intent.value,
                domain=router_result.domain.value if router_result.domain else "GENERAL",
                masked=has_pii,
                latency_ms=latency_ms,
                rag_used=False,
                rag_source_count=0,
            ),
        )

    def _create_system_help_response(
        self,
        start_time: float,
        has_pii: bool,
    ) -> ChatResponse:
        """
        시스템 도움말 응답을 생성합니다.

        Args:
            start_time: 요청 시작 시간
            has_pii: PII 검출 여부

        Returns:
            ChatResponse: 시스템 도움말 응답
        """
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        return ChatResponse(
            answer=SYSTEM_HELP_RESPONSE,
            sources=[],
            meta=ChatAnswerMeta(
                used_model=None,
                route=RouterRouteType.ROUTE_SYSTEM_HELP.value,
                intent=Tier0Intent.SYSTEM_HELP.value,
                domain="GENERAL",
                masked=has_pii,
                latency_ms=latency_ms,
                rag_used=False,
                rag_source_count=0,
            ),
        )

    def _create_unknown_route_response(
        self,
        start_time: float,
        has_pii: bool,
    ) -> ChatResponse:
        """
        UNKNOWN 라우트 응답을 생성합니다.

        Args:
            start_time: 요청 시작 시간
            has_pii: PII 검출 여부

        Returns:
            ChatResponse: UNKNOWN 응답
        """
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        return ChatResponse(
            answer=UNKNOWN_ROUTE_RESPONSE,
            sources=[],
            meta=ChatAnswerMeta(
                used_model=None,
                route=RouterRouteType.ROUTE_UNKNOWN.value,
                intent=Tier0Intent.UNKNOWN.value,
                domain="GENERAL",
                masked=has_pii,
                latency_ms=latency_ms,
                rag_used=False,
                rag_source_count=0,
            ),
        )

    def _map_tier0_to_intent(self, tier0_intent: Tier0Intent) -> Optional[IntentType]:
        """
        Tier0Intent를 IntentType으로 매핑합니다.

        Args:
            tier0_intent: Tier-0 의도

        Returns:
            Optional[IntentType]: 매핑된 IntentType 또는 None
        """
        mapping = {
            Tier0Intent.POLICY_QA: IntentType.POLICY_QA,
            Tier0Intent.EDUCATION_QA: IntentType.EDUCATION_QA,
            Tier0Intent.BACKEND_STATUS: IntentType.EDU_STATUS,  # 또는 상황에 따라 달라짐
            Tier0Intent.GENERAL_CHAT: IntentType.GENERAL_CHAT,
            Tier0Intent.SYSTEM_HELP: IntentType.SYSTEM_HELP,
            Tier0Intent.UNKNOWN: IntentType.UNKNOWN,
        }
        return mapping.get(tier0_intent)

    def _map_router_route_to_route_type(
        self, router_route: RouterRouteType
    ) -> Optional[RouteType]:
        """
        RouterRouteType을 RouteType으로 매핑합니다.

        Args:
            router_route: Router 라우트 타입

        Returns:
            Optional[RouteType]: 매핑된 RouteType 또는 None
        """
        mapping = {
            RouterRouteType.RAG_INTERNAL: RouteType.RAG_INTERNAL,
            RouterRouteType.BACKEND_API: RouteType.BACKEND_API,
            RouterRouteType.LLM_ONLY: RouteType.LLM_ONLY,
            RouterRouteType.ROUTE_SYSTEM_HELP: RouteType.LLM_ONLY,  # LLM_ONLY로 처리
            RouterRouteType.ROUTE_UNKNOWN: RouteType.FALLBACK,
        }
        return mapping.get(router_route)

    def _map_route_type_to_router_route_type(
        self, route: RouteType
    ) -> RouterRouteType:
        """
        RouteType을 RouterRouteType으로 매핑합니다 (역방향).

        Phase 39: Answerability 체크에 필요한 역방향 매핑.

        Args:
            route: RouteType

        Returns:
            RouterRouteType: 매핑된 RouterRouteType
        """
        mapping = {
            RouteType.RAG_INTERNAL: RouterRouteType.RAG_INTERNAL,
            RouteType.ROUTE_RAG_INTERNAL: RouterRouteType.RAG_INTERNAL,
            RouteType.BACKEND_API: RouterRouteType.BACKEND_API,
            RouteType.LLM_ONLY: RouterRouteType.LLM_ONLY,
            RouteType.ROUTE_LLM_ONLY: RouterRouteType.LLM_ONLY,
            RouteType.TRAINING: RouterRouteType.LLM_ONLY,
            RouteType.ROUTE_TRAINING: RouterRouteType.LLM_ONLY,
            RouteType.MIXED_BACKEND_RAG: RouterRouteType.RAG_INTERNAL,
            RouteType.INCIDENT: RouterRouteType.BACKEND_API,
            RouteType.ROUTE_INCIDENT: RouterRouteType.BACKEND_API,
            RouteType.FALLBACK: RouterRouteType.ROUTE_UNKNOWN,
            RouteType.ERROR: RouterRouteType.ROUTE_UNKNOWN,
        }
        return mapping.get(route, RouterRouteType.ROUTE_UNKNOWN)

    # =========================================================================
    # Phase 11: BACKEND_API용 LLM 메시지 빌더
    # =========================================================================

    def _build_backend_api_llm_messages(
        self,
        user_query: str,
        backend_context: str,
        user_role: UserRole,
        domain: str,
        intent: IntentType,
    ) -> List[Dict[str, str]]:
        """
        BACKEND_API용 LLM 메시지를 구성합니다.

        백엔드 데이터만 사용하여 LLM 프롬프트를 구성합니다.

        Args:
            user_query: 사용자 질문
            backend_context: 백엔드 데이터 포맷팅 결과
            user_role: 사용자 역할
            domain: 도메인
            intent: 의도

        Returns:
            List[Dict[str, str]]: LLM 메시지 목록
        """
        messages: List[Dict[str, str]] = []

        # 가드레일 prefix
        guardrail_prefix = self._guardrail.get_system_prompt_prefix(
            user_role=user_role,
            domain=domain,
            intent=intent,
        )

        # 시스템 프롬프트 구성
        system_content = SYSTEM_PROMPT_BACKEND_API

        if backend_context:
            system_content += f"\n\n[조회된 데이터]\n{backend_context}"
        else:
            system_content += "\n\n[조회된 데이터]\n(데이터를 조회하지 못했습니다)"

        if guardrail_prefix:
            system_content = guardrail_prefix + "\n\n" + system_content

        messages.append({
            "role": "system",
            "content": system_content,
        })

        messages.append({
            "role": "user",
            "content": user_query,
        })

        return messages
