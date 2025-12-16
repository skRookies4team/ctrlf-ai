"""
Phase 21: 라우터 오케스트레이터 (Router Orchestrator)

rule_router → llm_router → clarify/confirm 플로우를 통합 관리합니다.

주요 기능:
1. rule_router로 1차 분류 (키워드 기반)
2. 낮은 신뢰도면 llm_router로 2차 분류
3. needs_clarify=true면 되묻기 응답 생성
4. requires_confirmation=true면 확인 프롬프트 반환
5. 확인 상태 추적 (pending_action, pending_intent, trace_id)

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
needs_clarify? ───Yes──→ 되묻기 응답 반환
    ↓ No
requires_confirmation? ───Yes──→ 확인 프롬프트 반환 (상태 저장)
    ↓ No
최종 라우팅 실행
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


@dataclass
class PendingAction:
    """대기 중인 액션 상태.

    확인 게이트나 되묻기에서 사용자 응답을 기다리는 상태를 추적합니다.

    Attributes:
        action_type: 액션 유형 (CLARIFY 또는 CONFIRM)
        trace_id: 추적 ID (세션 내 고유)
        pending_intent: 대기 중인 의도
        sub_intent_id: 세부 의도 ID (치명 액션용)
        router_result: 원본 라우팅 결과
        created_at: 생성 시간
        expires_at: 만료 시간
    """

    action_type: PendingActionType
    trace_id: str
    pending_intent: Tier0Intent
    sub_intent_id: str = ""
    router_result: Optional[RouterResult] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None


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
        skip_pending_check: bool = False,
    ) -> OrchestrationResult:
        """사용자 질문을 라우팅합니다.

        Args:
            user_query: 사용자 질문 텍스트
            session_id: 세션 ID (대기 상태 추적용)
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

        # 되묻기 응답 - 새로운 라우팅 시도
        elif pending.action_type == PendingActionType.CLARIFY:
            self._pending_store.delete(session_id)
            # 새로운 컨텍스트로 라우팅
            return await self.route(
                user_query=user_query,
                session_id=session_id,
                skip_pending_check=True,
            )

        # 알 수 없는 액션 타입
        self._pending_store.delete(session_id)
        return await self.route(
            user_query=user_query,
            session_id=session_id,
            skip_pending_check=True,
        )

    def _create_clarify_result(
        self,
        router_result: RouterResult,
        session_id: str,
    ) -> OrchestrationResult:
        """되묻기 결과를 생성합니다.

        Args:
            router_result: 라우터 결과
            session_id: 세션 ID

        Returns:
            OrchestrationResult: 되묻기 결과
        """
        trace_id = str(uuid.uuid4())

        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id=trace_id,
            pending_intent=router_result.tier0_intent,
            router_result=router_result,
        )
        self._pending_store.set(session_id, pending)

        return OrchestrationResult(
            router_result=router_result,
            needs_user_response=True,
            response_message=router_result.clarify_question,
            pending_action=pending,
            can_execute=False,
        )

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
