"""
Phase 21: 라우터 오케스트레이터 (Router Orchestrator)

rule_router → llm_router → clarify/confirm 플로우를 통합 관리합니다.

주요 기능:
1. rule_router로 1차 분류 (키워드 기반)
2. 낮은 신뢰도면 llm_router로 2차 분류
3. needs_clarify=true면 되묻기 응답 생성
4. requires_confirmation=true면 확인 프롬프트 반환
5. 확인 상태 추적 (pending_action, pending_intent, trace_id)

Phase 23 업데이트:
- 되묻기 후 2턴 처리: PendingAction에 original_query, clarify_group 저장
- TTL 5분 (ROUTER_PENDING_TIMEOUT_SECONDS 설정 사용)
- 짧은 응답(20자 이하)은 원문+응답 결합하여 재라우팅
- clarify_group별 키워드 매핑으로 sub_intent 결정 (ClarifyAnswerHandler)
- one-shot 처리: 처리 완료 시 pending 즉시 삭제

플로우:
```
사용자 질문
    ↓
rule_router (키워드 기반 1차 분류)
    ↓
confidence >= 0.9? ───Yes──→ 최종 결과
    ↓ No
llm_router (LLM 기반 2차 분류)
    ↓
needs_clarify? ───Yes──→ 되묻기 응답 반환 + PendingAction 저장
    ↓ No                   (original_query, clarify_group, expires_at)
requires_confirmation? ───Yes──→ 확인 프롬프트 반환 (상태 저장)
    ↓ No
최종 라우팅 실행

[2턴째 - 되묻기 응답 처리]
사용자 응답
    ↓
pending clarify 확인
    ↓
응답 길이 <= 20자? ───No──→ 새 질문으로 처리
    ↓ Yes
ClarifyAnswerHandler로 키워드 매핑
    ↓
결합된 컨텍스트로 라우팅 또는 직접 처리
```
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

from app.clients.llm_client import LLMClient
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.router_types import (
    CRITICAL_ACTION_SUB_INTENTS,
    RouterDebugInfo,
    RouterDomain,
    RouterResult,
    RouterRouteType,
    Tier0Intent,
)
from app.services.llm_router import LLMRouter
from app.services.rule_router import RuleRouter

logger = get_logger(__name__)


# =============================================================================
# Pending Action State
# =============================================================================


class PendingActionType(str, Enum):
    """대기 중인 액션 유형."""

    CLARIFY = "CLARIFY"  # 되묻기 대기
    CONFIRM = "CONFIRM"  # 확인 대기


class ClarifyGroup(str, Enum):
    """되묻기 유형 그룹.

    Phase 23: 되묻기 후 2턴 처리에서 사용자 응답을 분류하기 위한 그룹입니다.
    """

    EDU = "EDU"  # 교육 관련 (이수현황 vs 내용설명)
    FAQ = "FAQ"  # FAQ 관련
    PROFILE = "PROFILE"  # 개인정보/프로필 관련
    POLICY = "POLICY"  # 사규/정책 관련
    INCIDENT = "INCIDENT"  # 사고/재해 관련
    UNKNOWN = "UNKNOWN"  # 분류 불명


# Phase 23: 되묻기 응답 키워드 매핑
# 각 clarify_group에서 사용자 응답 키워드 → sub_intent 결정
CLARIFY_KEYWORD_MAPPING: dict[ClarifyGroup, dict[str, str]] = {
    ClarifyGroup.EDU: {
        # 이수/진도/조회/내역/기록 → 백엔드 상태 조회
        "이수": "BACKEND_STATUS",
        "진도": "BACKEND_STATUS",
        "조회": "BACKEND_STATUS",
        "내역": "BACKEND_STATUS",
        "기록": "BACKEND_STATUS",
        "현황": "BACKEND_STATUS",
        "상태": "BACKEND_STATUS",
        "수료": "BACKEND_STATUS",
        "완료": "BACKEND_STATUS",
        # 내용/설명/요약/정리 → RAG 검색 (교육 콘텐츠 설명)
        "내용": "RAG_INTERNAL",
        "설명": "RAG_INTERNAL",
        "요약": "RAG_INTERNAL",
        "정리": "RAG_INTERNAL",
        "안내": "RAG_INTERNAL",
        "어떤": "RAG_INTERNAL",
        "뭐야": "RAG_INTERNAL",
        "알려": "RAG_INTERNAL",
    },
    ClarifyGroup.POLICY: {
        "조회": "BACKEND_STATUS",
        "현황": "BACKEND_STATUS",
        "상태": "BACKEND_STATUS",
        "내용": "RAG_INTERNAL",
        "설명": "RAG_INTERNAL",
        "규정": "RAG_INTERNAL",
        "정책": "RAG_INTERNAL",
    },
    ClarifyGroup.PROFILE: {
        "조회": "BACKEND_STATUS",
        "확인": "BACKEND_STATUS",
        "내역": "BACKEND_STATUS",
        "정보": "BACKEND_STATUS",
    },
    ClarifyGroup.INCIDENT: {
        "통계": "BACKEND_STATUS",
        "현황": "BACKEND_STATUS",
        "조회": "BACKEND_STATUS",
        "신고": "BACKEND_API",
        "보고": "BACKEND_API",
    },
}

# Phase 23: 짧은 응답으로 간주할 최대 길이
CLARIFY_SHORT_RESPONSE_MAX_LENGTH = 20


@dataclass
class PendingAction:
    """대기 중인 액션 상태.

    확인 게이트나 되묻기에서 사용자 응답을 기다리는 상태를 추적합니다.

    Phase 23 업데이트:
    - original_query: 되묻기 전 원문 질문
    - clarify_group: 되묻기 유형 (EDU, FAQ, PROFILE 등)
    - user_id: 사용자 ID (가능하면)
    - expires_at: TTL 기반 만료 시간 (default: 5분)

    Attributes:
        action_type: 액션 유형 (CLARIFY 또는 CONFIRM)
        trace_id: 추적 ID (세션 내 고유)
        pending_intent: 대기 중인 의도
        sub_intent_id: 세부 의도 ID (치명 액션용)
        router_result: 원본 라우팅 결과
        created_at: 생성 시간
        expires_at: 만료 시간
        original_query: 되묻기 전 원문 질문 (Phase 23)
        clarify_group: 되묻기 유형 그룹 (Phase 23)
        user_id: 사용자 ID (Phase 23)
    """

    action_type: PendingActionType
    trace_id: str
    pending_intent: Tier0Intent
    sub_intent_id: str = ""
    router_result: Optional[RouterResult] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    # Phase 23: 되묻기 후 2턴 처리를 위한 필드
    original_query: str = ""
    clarify_group: ClarifyGroup = ClarifyGroup.UNKNOWN
    user_id: str = ""


# =============================================================================
# Orchestration Result
# =============================================================================


@dataclass
class OrchestrationResult:
    """오케스트레이션 결과.

    라우터 오케스트레이터의 최종 결과를 담습니다.

    Attributes:
        router_result: 라우팅 결과
        needs_user_response: 사용자 응답 필요 여부 (되묻기/확인)
        response_message: 사용자에게 보여줄 메시지
        pending_action: 대기 중인 액션 (있으면)
        can_execute: 즉시 실행 가능 여부
    """

    router_result: RouterResult
    needs_user_response: bool = False
    response_message: str = ""
    pending_action: Optional[PendingAction] = None
    can_execute: bool = True


# =============================================================================
# In-Memory Pending Action Store
# =============================================================================


class PendingActionStore:
    """대기 중인 액션 저장소 (인메모리).

    세션별 pending action을 추적합니다.
    실제 운영에서는 Redis 등 외부 저장소로 교체 권장.

    Usage:
        store = PendingActionStore()
        store.set("session-123", pending_action)
        action = store.get("session-123")
        store.delete("session-123")
    """

    def __init__(self) -> None:
        """PendingActionStore 초기화."""
        self._store: Dict[str, PendingAction] = {}

    def set(self, session_id: str, action: PendingAction) -> None:
        """대기 액션을 저장합니다."""
        self._store[session_id] = action

    def get(self, session_id: str) -> Optional[PendingAction]:
        """대기 액션을 조회합니다."""
        action = self._store.get(session_id)
        if action and action.expires_at:
            if datetime.now(timezone.utc) > action.expires_at:
                self.delete(session_id)
                return None
        return action

    def delete(self, session_id: str) -> None:
        """대기 액션을 삭제합니다."""
        self._store.pop(session_id, None)

    def clear(self) -> None:
        """모든 대기 액션을 삭제합니다."""
        self._store.clear()


# 전역 pending action 저장소
_pending_action_store: Optional[PendingActionStore] = None


def get_pending_action_store() -> PendingActionStore:
    """Pending action 저장소 인스턴스를 반환합니다."""
    global _pending_action_store
    if _pending_action_store is None:
        _pending_action_store = PendingActionStore()
    return _pending_action_store


def clear_pending_action_store() -> None:
    """Pending action 저장소를 초기화합니다 (테스트용)."""
    global _pending_action_store
    if _pending_action_store is not None:
        _pending_action_store.clear()
        _pending_action_store = None


# =============================================================================
# RouterOrchestrator 클래스
# =============================================================================


class RouterOrchestrator:
    """라우터 오케스트레이터.

    rule_router → llm_router → clarify/confirm 플로우를 통합 관리합니다.

    Usage:
        orchestrator = RouterOrchestrator()

        # 일반 라우팅
        result = await orchestrator.route(
            user_query="연차 규정 알려줘",
            session_id="session-123",
        )

        # 사용자 확인 응답 처리
        if result.needs_user_response:
            # 되묻기/확인 프롬프트 표시
            print(result.response_message)
        else:
            # 라우팅 실행
            execute_route(result.router_result)

        # 확인 응답 처리
        if user_confirmed:
            result = await orchestrator.handle_confirmation(
                session_id="session-123",
                confirmed=True,
            )
    """

    # Rule router만 사용할 최소 신뢰도 (이상이면 LLM router 스킵)
    RULE_ROUTER_CONFIDENCE_THRESHOLD = 0.85

    def __init__(
        self,
        rule_router: Optional[RuleRouter] = None,
        llm_router: Optional[LLMRouter] = None,
        llm_client: Optional[LLMClient] = None,
        pending_store: Optional[PendingActionStore] = None,
    ) -> None:
        """RouterOrchestrator 초기화.

        Args:
            rule_router: 규칙 기반 라우터
            llm_router: LLM 기반 라우터
            llm_client: LLM 클라이언트 (llm_router 생성용)
            pending_store: 대기 액션 저장소
        """
        self._rule_router = rule_router or RuleRouter()
        self._llm_router = llm_router or LLMRouter(llm_client=llm_client)
        self._pending_store = pending_store or get_pending_action_store()

        # 설정에서 LLM router 사용 여부 확인
        settings = get_settings()
        self._use_llm_router = getattr(settings, "ROUTER_USE_LLM", True)

    async def route(
        self,
        user_query: str,
        session_id: str,
        user_id: str = "",
        skip_pending_check: bool = False,
    ) -> OrchestrationResult:
        """사용자 질문을 라우팅합니다.

        Phase 23 업데이트:
        - user_id 파라미터 추가
        - 되묻기 시 original_query, clarify_group, expires_at 저장

        Args:
            user_query: 사용자 질문 텍스트
            session_id: 세션 ID (대기 상태 추적용)
            user_id: 사용자 ID (Phase 23: 되묻기 상태 추적용)
            skip_pending_check: 대기 상태 체크 스킵 여부

        Returns:
            OrchestrationResult: 오케스트레이션 결과
        """
        # Step 0: 대기 중인 액션 체크 (확인/되묻기 응답인지)
        if not skip_pending_check:
            pending = self._pending_store.get(session_id)
            if pending:
                return await self._handle_pending_response(
                    user_query=user_query,
                    session_id=session_id,
                    pending=pending,
                )

        # Step 1: Rule Router로 1차 분류
        rule_result = self._rule_router.route(user_query)

        logger.info(
            f"Orchestrator: rule_router result - "
            f"intent={rule_result.tier0_intent.value}, "
            f"confidence={rule_result.confidence}, "
            f"needs_clarify={rule_result.needs_clarify}"
        )

        # Step 2: 되묻기 필요하면 즉시 반환
        if rule_result.needs_clarify:
            return self._create_clarify_result(
                router_result=rule_result,
                session_id=session_id,
                user_id=user_id,
                original_query=user_query,
            )

        # Step 3: 높은 신뢰도면 LLM router 스킵
        if rule_result.confidence >= self.RULE_ROUTER_CONFIDENCE_THRESHOLD:
            logger.debug(
                f"Orchestrator: High confidence ({rule_result.confidence}), "
                "skipping LLM router"
            )
            final_result = rule_result
        else:
            # Step 4: 낮은 신뢰도면 LLM router로 2차 분류
            if self._use_llm_router:
                logger.debug(
                    f"Orchestrator: Low confidence ({rule_result.confidence}), "
                    "calling LLM router"
                )
                final_result = await self._llm_router.route(
                    user_query=user_query,
                    rule_router_result=rule_result,
                )

                # LLM 결과에서도 되묻기 필요하면 반환
                if final_result.needs_clarify:
                    return self._create_clarify_result(
                        router_result=final_result,
                        session_id=session_id,
                        user_id=user_id,
                        original_query=user_query,
                    )
            else:
                final_result = rule_result

        # Step 5: 확인 게이트 필요하면 반환
        if final_result.requires_confirmation:
            return self._create_confirmation_result(
                router_result=final_result,
                session_id=session_id,
            )

        # Step 6: 즉시 실행 가능
        return OrchestrationResult(
            router_result=final_result,
            needs_user_response=False,
            can_execute=True,
        )

    async def handle_confirmation(
        self,
        session_id: str,
        confirmed: bool,
    ) -> OrchestrationResult:
        """사용자의 확인 응답을 처리합니다.

        Args:
            session_id: 세션 ID
            confirmed: 사용자가 확인했는지 여부

        Returns:
            OrchestrationResult: 처리 결과
        """
        pending = self._pending_store.get(session_id)
        if not pending:
            logger.warning(f"No pending action found for session: {session_id}")
            return OrchestrationResult(
                router_result=RouterResult(
                    tier0_intent=Tier0Intent.UNKNOWN,
                    route_type=RouterRouteType.ROUTE_UNKNOWN,
                ),
                needs_user_response=False,
                response_message="이전 요청을 찾을 수 없습니다. 다시 말씀해 주세요.",
                can_execute=False,
            )

        # 대기 액션 삭제
        self._pending_store.delete(session_id)

        if not confirmed:
            logger.info(f"User declined action: {pending.sub_intent_id}")
            return OrchestrationResult(
                router_result=RouterResult(
                    tier0_intent=Tier0Intent.GENERAL_CHAT,
                    route_type=RouterRouteType.LLM_ONLY,
                ),
                needs_user_response=False,
                response_message="알겠습니다. 취소되었습니다.",
                can_execute=False,
            )

        # 확인됨 - 원본 라우팅 결과로 실행
        logger.info(f"User confirmed action: {pending.sub_intent_id}")
        if pending.router_result:
            # 확인 플래그 제거하여 실행 가능하게
            pending.router_result.requires_confirmation = False
            return OrchestrationResult(
                router_result=pending.router_result,
                needs_user_response=False,
                can_execute=True,
            )

        return OrchestrationResult(
            router_result=RouterResult(
                tier0_intent=pending.pending_intent,
                route_type=RouterRouteType.BACKEND_API,
            ),
            needs_user_response=False,
            can_execute=True,
        )

    async def _handle_pending_response(
        self,
        user_query: str,
        session_id: str,
        pending: PendingAction,
    ) -> OrchestrationResult:
        """대기 중인 액션에 대한 사용자 응답을 처리합니다.

        Phase 23 업데이트:
        - 되묻기 응답 처리 (ClarifyAnswerHandler)
        - 짧은 응답(20자 이하)은 키워드 매핑으로 sub_intent 결정
        - 긴 응답(20자 초과)은 새 질문으로 처리
        - one-shot: 처리 완료 시 pending 즉시 삭제

        Args:
            user_query: 사용자 응답 텍스트
            session_id: 세션 ID
            pending: 대기 중인 액션

        Returns:
            OrchestrationResult: 처리 결과
        """
        query_lower = user_query.lower().strip()

        # 확인 응답 체크
        if pending.action_type == PendingActionType.CONFIRM:
            # 긍정 응답
            if query_lower in ("예", "네", "응", "ㅇㅇ", "yes", "진행", "시작", "확인"):
                return await self.handle_confirmation(session_id, confirmed=True)
            # 부정 응답
            elif query_lower in ("아니오", "아니", "ㄴㄴ", "no", "취소", "안해"):
                return await self.handle_confirmation(session_id, confirmed=False)
            # 불명확한 응답 - 다시 확인
            else:
                return OrchestrationResult(
                    router_result=pending.router_result or RouterResult(),
                    needs_user_response=True,
                    response_message=(
                        "'예' 또는 '아니오'로 답변해 주세요. "
                        + (pending.router_result.confirmation_prompt if pending.router_result else "")
                    ),
                    pending_action=pending,
                    can_execute=False,
                )

        # 되묻기 응답 - Phase 23: ClarifyAnswerHandler 로직
        elif pending.action_type == PendingActionType.CLARIFY:
            # one-shot: 처리 후 pending 삭제
            self._pending_store.delete(session_id)

            # Phase 23: 응답 길이 체크
            response_length = len(user_query.strip())

            # 긴 응답(20자 초과)이면 새 질문으로 처리
            if response_length > CLARIFY_SHORT_RESPONSE_MAX_LENGTH:
                logger.info(
                    f"Clarify response too long ({response_length} chars), "
                    f"treating as new query: {user_query[:50]}..."
                )
                return await self.route(
                    user_query=user_query,
                    session_id=session_id,
                    user_id=pending.user_id,
                    skip_pending_check=True,
                )

            # 짧은 응답: ClarifyAnswerHandler로 키워드 매핑
            resolved_route = self._resolve_clarify_answer(
                answer=user_query,
                clarify_group=pending.clarify_group,
                original_query=pending.original_query,
                pending=pending,
            )

            if resolved_route:
                logger.info(
                    f"Clarify answer resolved: group={pending.clarify_group.value}, "
                    f"answer='{user_query}', route={resolved_route.route_type.value}"
                )
                return OrchestrationResult(
                    router_result=resolved_route,
                    needs_user_response=False,
                    can_execute=True,
                )

            # 키워드 매핑 실패: 원문+응답 결합하여 재라우팅
            combined_query = f"{pending.original_query} {user_query}".strip()
            logger.info(
                f"Clarify keyword not matched, re-routing with combined query: "
                f"'{combined_query[:50]}...'"
            )
            return await self.route(
                user_query=combined_query,
                session_id=session_id,
                user_id=pending.user_id,
                skip_pending_check=True,
            )

        # 알 수 없는 액션 타입
        self._pending_store.delete(session_id)
        return await self.route(
            user_query=user_query,
            session_id=session_id,
            skip_pending_check=True,
        )

    def _resolve_clarify_answer(
        self,
        answer: str,
        clarify_group: ClarifyGroup,
        original_query: str,
        pending: PendingAction,
    ) -> Optional[RouterResult]:
        """되묻기 응답에서 키워드 매핑으로 라우팅 결과를 결정합니다.

        Phase 23: ClarifyAnswerHandler 로직

        Args:
            answer: 사용자 응답 (짧은 응답)
            clarify_group: 되묻기 유형 그룹
            original_query: 되묻기 전 원문 질문
            pending: 대기 중인 액션

        Returns:
            RouterResult: 결정된 라우팅 결과, 또는 None (매핑 실패 시)
        """
        answer_lower = answer.lower().strip()

        # clarify_group에 해당하는 키워드 맵 조회
        keyword_map = CLARIFY_KEYWORD_MAPPING.get(clarify_group, {})

        # 키워드 매칭
        matched_route_type = None
        for keyword, route_type_str in keyword_map.items():
            if keyword in answer_lower:
                matched_route_type = route_type_str
                break

        if not matched_route_type:
            return None

        # 라우팅 결과 생성
        if matched_route_type == "BACKEND_STATUS":
            return RouterResult(
                tier0_intent=pending.pending_intent,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id="STATUS_QUERY",
                confidence=0.9,
                domain=pending.router_result.domain if pending.router_result else None,
            )
        elif matched_route_type == "RAG_INTERNAL":
            return RouterResult(
                tier0_intent=pending.pending_intent,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.9,
                domain=pending.router_result.domain if pending.router_result else None,
            )
        elif matched_route_type == "BACKEND_API":
            return RouterResult(
                tier0_intent=pending.pending_intent,
                route_type=RouterRouteType.BACKEND_API,
                confidence=0.9,
                domain=pending.router_result.domain if pending.router_result else None,
            )

        return None

    def _create_clarify_result(
        self,
        router_result: RouterResult,
        session_id: str,
        user_id: str = "",
        original_query: str = "",
    ) -> OrchestrationResult:
        """되묻기 결과를 생성합니다.

        Phase 23 업데이트:
        - original_query, clarify_group, user_id 저장
        - expires_at = now + ROUTER_PENDING_TIMEOUT_SECONDS (TTL 5분)

        Args:
            router_result: 라우터 결과
            session_id: 세션 ID
            user_id: 사용자 ID (Phase 23)
            original_query: 되묻기 전 원문 질문 (Phase 23)

        Returns:
            OrchestrationResult: 되묻기 결과
        """
        from datetime import timedelta

        trace_id = str(uuid.uuid4())
        settings = get_settings()

        # Phase 23: TTL 기반 만료 시간 설정
        ttl_seconds = getattr(settings, "ROUTER_PENDING_TIMEOUT_SECONDS", 300)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        # Phase 23: tier0_intent에서 clarify_group 결정
        clarify_group = self._determine_clarify_group(router_result.tier0_intent)

        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id=trace_id,
            pending_intent=router_result.tier0_intent,
            router_result=router_result,
            expires_at=expires_at,
            original_query=original_query,
            clarify_group=clarify_group,
            user_id=user_id,
        )
        self._pending_store.set(session_id, pending)

        logger.info(
            f"Clarify pending created: session={session_id}, "
            f"group={clarify_group.value}, original_query='{original_query[:30]}...', "
            f"expires_at={expires_at.isoformat()}"
        )

        return OrchestrationResult(
            router_result=router_result,
            needs_user_response=True,
            response_message=router_result.clarify_question,
            pending_action=pending,
            can_execute=False,
        )

    def _determine_clarify_group(self, tier0_intent: Tier0Intent) -> ClarifyGroup:
        """Tier0Intent에서 ClarifyGroup을 결정합니다.

        Phase 23: 되묻기 유형 결정

        Args:
            tier0_intent: Tier0 의도

        Returns:
            ClarifyGroup: 되묻기 유형 그룹
        """
        intent_to_group = {
            Tier0Intent.EDUCATION_QA: ClarifyGroup.EDU,
            Tier0Intent.BACKEND_STATUS: ClarifyGroup.EDU,  # 교육 현황도 EDU 그룹
            Tier0Intent.POLICY_QA: ClarifyGroup.POLICY,
            Tier0Intent.GENERAL_CHAT: ClarifyGroup.FAQ,
            Tier0Intent.SYSTEM_HELP: ClarifyGroup.FAQ,
        }
        return intent_to_group.get(tier0_intent, ClarifyGroup.UNKNOWN)

    def _create_confirmation_result(
        self,
        router_result: RouterResult,
        session_id: str,
    ) -> OrchestrationResult:
        """확인 게이트 결과를 생성합니다.

        Args:
            router_result: 라우터 결과
            session_id: 세션 ID

        Returns:
            OrchestrationResult: 확인 게이트 결과
        """
        trace_id = str(uuid.uuid4())

        pending = PendingAction(
            action_type=PendingActionType.CONFIRM,
            trace_id=trace_id,
            pending_intent=router_result.tier0_intent,
            sub_intent_id=router_result.sub_intent_id,
            router_result=router_result,
        )
        self._pending_store.set(session_id, pending)

        return OrchestrationResult(
            router_result=router_result,
            needs_user_response=True,
            response_message=router_result.confirmation_prompt,
            pending_action=pending,
            can_execute=False,
        )

    def clear_pending(self, session_id: str) -> None:
        """세션의 대기 상태를 클리어합니다.

        Args:
            session_id: 세션 ID
        """
        self._pending_store.delete(session_id)
