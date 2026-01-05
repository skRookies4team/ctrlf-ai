"""
Chat Context Handler - ChatService 통합 모듈

멀티턴 맥락 유지를 위한 통합 핸들러.
ChatService에서 호출하여 상태 관리, 히스토리 처리, 지시어 해소 등을 처리.

설계 원칙:
- A: 저장소 (Redis/Memory) + TTL + 키 정책
- B: 상태 갱신 규칙 (신뢰도 메타)
- C: 라우터 결과를 단일 진실로
- D: recent_docs 스택
- E: 검색 병합 + rank bump
- F: 품질 게이트
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, TYPE_CHECKING

from app.core.config import get_settings
from app.models.chat import ChatMessage, ChatSource
from app.models.conversation_state import (
    ConversationState,
    DocReference,
    DocReferenceReason,
    TopicSwitchAction,
    TopicSwitchResult,
)
from app.services.state_store import get_state_store, get_or_create_state, save_state
from app.services.history_manager import truncate_history_safe, HistoryConfig
from app.services.anaphora_resolver import (
    resolve_anaphora,
    resolve_anaphora_rule,
    has_anaphora,
    ResolveResult,
    AnaphoraType,
    build_clarify_options,
    build_clarify_message,
    should_apply_boost,
)
from app.services.search_merger import (
    QualityAction,
    QualityAssessment,
    assess_quality,
    apply_rank_bump_multi,
    merge_results,
    SearchMergerConfig,
)

if TYPE_CHECKING:
    from app.models.intent import RouteType

logger = logging.getLogger(__name__)


# =============================================================================
# 컨텍스트 처리 결과
# =============================================================================

@dataclass
class ContextProcessResult:
    """컨텍스트 처리 결과"""

    # 상태
    state: ConversationState

    # 히스토리
    history_for_prompt: List[ChatMessage]  # MessageBuilder에 전달할 히스토리 (현재 질문 제외)

    # 지시어 해소
    resolved_query: str  # 해소된 쿼리
    anaphora_resolved: bool = False  # 규칙/LLM으로 지시어가 해소되었는지
    anaphora_type: AnaphoraType = AnaphoraType.NONE
    resolved_doc_id: Optional[str] = None

    # 사용자 명시 선택 (Clarify 옵션에서 선택)
    user_selected: bool = False  # Clarify 후 사용자가 옵션을 선택했는지

    # 부스팅
    boost_enabled: bool = True
    boost_doc_ids: List[str] = field(default_factory=list)

    # 토픽 전환
    topic_switched: bool = False
    topic_switch_action: TopicSwitchAction = TopicSwitchAction.NONE


@dataclass
class SearchProcessResult:
    """검색 처리 결과"""

    sources: List[ChatSource]
    quality: QualityAssessment
    boost_applied: bool = False
    needs_clarify: bool = False
    clarify_message: Optional[str] = None


@dataclass
class StateUpdateResult:
    """상태 갱신 결과"""

    updated: bool
    doc_added: bool = False
    version: int = 0


# =============================================================================
# ChatContextHandler
# =============================================================================

class ChatContextHandler:
    """
    ChatService 통합 핸들러

    멀티턴 맥락 유지를 위한 모든 처리를 담당:
    - 상태 로드/저장
    - 히스토리 truncation
    - 지시어 해소
    - 토픽 전환 감지
    - 검색 결과 처리
    - 상태 갱신
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._store = get_state_store()
        self._history_config = HistoryConfig(
            max_turns=self._settings.CHAT_HISTORY_MAX_TURNS,
            max_tokens=self._settings.CHAT_HISTORY_MAX_TOKENS,
            token_counting_mode=self._settings.CHAT_TOKEN_COUNTING_MODE,
        )
        self._search_config = SearchMergerConfig(
            merge_enabled=self._settings.SEARCH_MERGE_ENABLED,
            rank_bump_max=self._settings.SEARCH_RANK_BUMP_MAX,
            quality_top1_threshold=self._settings.QUALITY_TOP1_THRESHOLD,
            quality_gap_threshold=self._settings.QUALITY_GAP_THRESHOLD,
        )

    # =========================================================================
    # Step 0: State 로드
    # =========================================================================

    async def load_state(
        self,
        user_id: str,
        session_id: str,
    ) -> ConversationState:
        """
        상태 로드 (없으면 새로 생성)

        Args:
            user_id: 사용자 ID (인증 컨텍스트에서 가져와야 함)
            session_id: 세션 ID

        Returns:
            ConversationState: 대화 상태
        """
        state = await get_or_create_state(user_id, session_id)
        logger.debug(
            f"State loaded: user={user_id}, session={session_id}, "
            f"version={state.state_version}, turn={state.turn_count}"
        )
        return state

    # =========================================================================
    # Step 1-4: 컨텍스트 처리 (통합)
    # =========================================================================

    async def process_context(
        self,
        state: ConversationState,
        messages: List[ChatMessage],
        user_query: str,
        current_domain: Optional[str] = None,
        current_intent: Optional[str] = None,
    ) -> ContextProcessResult:
        """
        컨텍스트 처리 (Step 1-4 통합)

        1. 히스토리 truncation
        2. 규칙 기반 지시어 해소
        3. 토픽 전환 감지 (라우팅 결과 기반)
        4. 부스팅 후보 결정

        Args:
            state: 대화 상태
            messages: 전체 메시지 목록
            user_query: 현재 사용자 쿼리
            current_domain: 라우터가 결정한 도메인
            current_intent: 라우터가 결정한 인텐트

        Returns:
            ContextProcessResult: 처리 결과
        """
        # Step 1: 히스토리 truncation
        truncated = truncate_history_safe(
            messages,
            max_turns=self._history_config.max_turns,
            max_tokens=self._history_config.max_tokens,
        )

        # 현재 질문 제외 (마지막 메시지가 현재 질문)
        # 인덱스 기반으로 제외 (content 비교 X)
        if truncated and truncated[-1].role == "user":
            history_for_prompt = truncated[:-1]
        else:
            history_for_prompt = truncated

        # Step 2: 규칙 기반 지시어 해소 (라우팅 전에도 안전)
        resolve_result = resolve_anaphora_rule(user_query, state)
        resolved_query = user_query
        anaphora_resolved = False
        anaphora_type = AnaphoraType.NONE
        resolved_doc_id = None

        if resolve_result and resolve_result.resolved:
            resolved_query = resolve_result.query
            anaphora_resolved = True
            anaphora_type = resolve_result.anaphora_type
            resolved_doc_id = resolve_result.resolved_doc_id
            logger.debug(f"Anaphora resolved (rule): {user_query[:30]}... → {resolved_query[:30]}...")

        # Step 3: 토픽 전환 감지 (라우터 결과 기반)
        topic_switched = False
        topic_switch_action = TopicSwitchAction.NONE

        if current_domain:
            switch_result = state.detect_topic_switch(current_domain, current_intent or "")
            topic_switched = switch_result.switched
            topic_switch_action = switch_result.action

            if topic_switched:
                logger.info(f"Topic switch detected: {switch_result.reason}")

        # Step 4: 부스팅 후보 결정
        boost_enabled = True
        boost_doc_ids: List[str] = []

        if topic_switch_action == TopicSwitchAction.RESET_BOOST:
            boost_enabled = False
        elif topic_switch_action == TopicSwitchAction.DECAY_BOOST:
            # 동일 도메인 문서만 후보로
            boost_doc_ids = [
                doc.doc_id for doc in state.recent_docs
                if doc.domain == current_domain
            ][:2]
        else:
            # 기본: 최근 2개
            boost_doc_ids = state.get_recent_doc_ids()[:2]

        # 부스팅 조건 체크
        if boost_enabled and not should_apply_boost(user_query, state):
            boost_enabled = False

        return ContextProcessResult(
            state=state,
            history_for_prompt=history_for_prompt,
            resolved_query=resolved_query,
            anaphora_resolved=anaphora_resolved,
            anaphora_type=anaphora_type,
            resolved_doc_id=resolved_doc_id,
            boost_enabled=boost_enabled,
            boost_doc_ids=boost_doc_ids,
            topic_switched=topic_switched,
            topic_switch_action=topic_switch_action,
        )

    # =========================================================================
    # Step 4b: 후보잠금 LLM 지시어 해소 (필요 시)
    # =========================================================================

    async def resolve_anaphora_llm_if_needed(
        self,
        context: ContextProcessResult,
        user_query: str,
        current_domain: str,
    ) -> Tuple[str, Optional[str], bool]:
        """
        후보잠금 LLM 지시어 해소 (필요 시에만)

        조건:
        - 지시어 있음
        - 규칙 기반 해소 실패
        - 후보(recent_docs) 있음

        Args:
            context: 컨텍스트 처리 결과
            user_query: 원본 쿼리
            current_domain: 현재 도메인

        Returns:
            Tuple[resolved_query, selected_doc_id, needs_clarify]
        """
        # 이미 해소됐으면 스킵
        if context.anaphora_resolved:
            return context.resolved_query, context.resolved_doc_id, False

        # 지시어 없으면 스킵
        if not has_anaphora(user_query):
            return user_query, None, False

        # 후보 없으면 clarify 필요
        state = context.state
        if not state.recent_docs:
            return user_query, None, True

        # 동일 도메인 후보만 필터링
        domain_docs = [
            doc for doc in state.recent_docs
            if doc.domain == current_domain
        ]

        if not domain_docs:
            # 도메인 불일치 → clarify
            return user_query, None, True

        # LLM 호출 (현재는 첫 번째 후보 선택으로 간소화)
        # TODO: 실제 LLM 호출 구현
        first_doc = domain_docs[0]
        resolved_query = f"{first_doc.title}에 대해: {user_query}"

        logger.debug(f"Anaphora resolved (LLM fallback): {user_query[:30]}... → {resolved_query[:30]}...")

        return resolved_query, first_doc.doc_id, False

    # =========================================================================
    # Step 5: 검색 결과 처리
    # =========================================================================

    def process_search_results(
        self,
        general_results: List[ChatSource],
        filter_results: List[ChatSource],
        query: str,
        context: ContextProcessResult,
    ) -> SearchProcessResult:
        """
        검색 결과 처리 (병합 + 품질 게이트 + rank bump)

        Args:
            general_results: 일반 검색 결과
            filter_results: doc_id filter 검색 결과
            query: 검색 쿼리
            context: 컨텍스트 처리 결과

        Returns:
            SearchProcessResult: 처리 결과
        """
        # 병합 (E: 단독 filter 반환 금지)
        if filter_results:
            merged = merge_results(general_results, filter_results)
        else:
            merged = general_results

        # 품질 게이트 (F)
        quality = assess_quality(merged, query, self._search_config)

        # 품질에 따른 액션
        needs_clarify = False
        clarify_message = None

        if quality.action == QualityAction.CLARIFY:
            needs_clarify = True
            options = build_clarify_options(context.state, context.anaphora_type)
            clarify_message = build_clarify_message(options, context.anaphora_type)

        # Rank bump (조건부)
        boost_applied = False
        if context.boost_enabled and context.boost_doc_ids:
            bumped = apply_rank_bump_multi(merged, context.boost_doc_ids, self._search_config)
            if bumped != merged:
                merged = bumped
                boost_applied = True

        return SearchProcessResult(
            sources=merged,
            quality=quality,
            boost_applied=boost_applied,
            needs_clarify=needs_clarify,
            clarify_message=clarify_message,
        )

    # =========================================================================
    # Step 7: 상태 갱신 (B, D)
    # =========================================================================

    def update_state_from_response(
        self,
        state: ConversationState,
        current_domain: str,
        current_intent: str,
        sources: List[ChatSource],
        resolved_doc_id: Optional[str] = None,
        resolved_by_anaphora: bool = False,
        selected_by_user: bool = False,
    ) -> StateUpdateResult:
        """
        응답 생성 후 상태 갱신 (B: 갱신 규칙)

        갱신 우선순위:
        1. USER_SELECTED: 사용자가 Clarify 옵션에서 명시 선택 → 무조건 갱신
        2. ANAPHORA_RESOLVED: 규칙/LLM으로 지시어 해소 → 약한 확정 (고신뢰 승격 금지)
        3. RAG_TOP1_HIGH: score >= threshold AND gap >= threshold
        4. RAG_TOP1_LOW: 낮은 신뢰도 → 갱신하되 reason 구분
        5. FALLBACK_FILTER: 갱신 안 함

        주의: ANAPHORA_RESOLVED는 "후보 특정"이지 "정답 확정"이 아니므로
        RAG_TOP1_HIGH로 승격하지 않음 (오답 고착 방지)

        Args:
            state: 대화 상태
            current_domain: 현재 도메인
            current_intent: 현재 인텐트
            sources: RAG 검색 결과
            resolved_doc_id: 지시어 해소로 선택된 doc_id
            resolved_by_anaphora: 규칙/LLM으로 지시어가 해소되었는지
            selected_by_user: Clarify 옵션에서 사용자가 명시적으로 선택했는지

        Returns:
            StateUpdateResult: 갱신 결과
        """
        # 도메인/인텐트 갱신 (C: Single Source of Truth)
        state.last_domain = current_domain
        state.last_intent = current_intent

        # 턴 증가 (버전도 함께 증가)
        state.increment_turn()

        doc_added = False

        # 문서 갱신 규칙 (B)
        # 케이스 1: 사용자가 Clarify 옵션에서 명시 선택 → USER_SELECTED
        if selected_by_user and resolved_doc_id:
            doc = state.get_doc_by_id(resolved_doc_id)
            if doc:
                doc.reason = DocReferenceReason.USER_SELECTED
                doc.turn = state.turn_count
                state.add_recent_doc(doc)
                doc_added = True
                logger.debug(f"Doc updated (USER_SELECTED): {resolved_doc_id}")

        # 케이스 2: 지시어 해소로 문서 특정됨 (규칙/LLM) → ANAPHORA_RESOLVED
        # 고신뢰 승격 금지: 지시어 해소는 "후보 특정"이지 "정답 확정"이 아님
        # 오해소 1번이 RAG_TOP1_HIGH로 저장되어 오답 고착되는 경로를 차단
        elif resolved_by_anaphora and resolved_doc_id:
            doc = state.get_doc_by_id(resolved_doc_id)
            if doc:
                doc.reason = DocReferenceReason.ANAPHORA_RESOLVED
                doc.turn = state.turn_count
                state.add_recent_doc(doc)
                doc_added = True
                logger.debug(f"Doc updated (ANAPHORA_RESOLVED): {resolved_doc_id}")

        elif sources:
            top1 = sources[0]
            top2 = sources[1] if len(sources) > 1 else None

            score = top1.score or 0.0
            gap = (score - (top2.score or 0.0)) if top2 else score

            high_threshold = self._settings.STATE_UPDATE_HIGH_SCORE_THRESHOLD
            gap_threshold = self._settings.STATE_UPDATE_SCORE_GAP_THRESHOLD

            # RAG_TOP1_HIGH 판정
            if score >= high_threshold and gap >= gap_threshold:
                reason = DocReferenceReason.RAG_TOP1_HIGH
            else:
                reason = DocReferenceReason.RAG_TOP1_LOW

            # DocReference 생성
            new_doc = DocReference(
                doc_id=top1.doc_id or top1.title or "",
                title=top1.title or "",
                domain=current_domain,
                score=score,
                reason=reason,
                turn=state.turn_count,
                citations=[],
            )

            # 갱신 조건 체크
            if state.should_update_doc(new_doc, high_threshold):
                state.add_recent_doc(new_doc)
                doc_added = True
                logger.debug(f"Doc updated ({reason.value}): {new_doc.doc_id}, score={score:.3f}")

        return StateUpdateResult(
            updated=True,
            doc_added=doc_added,
            version=state.state_version,
        )

    # =========================================================================
    # Step 8: 상태 저장 (A)
    # =========================================================================

    async def save_state(self, state: ConversationState) -> None:
        """
        상태 저장 (TTL sliding 포함)

        Args:
            state: 저장할 상태
        """
        await save_state(state)
        logger.debug(
            f"State saved: user={state.user_id}, session={state.session_id}, "
            f"version={state.state_version}"
        )

    # =========================================================================
    # 유틸리티
    # =========================================================================

    def get_prompt_context(self, state: ConversationState) -> str:
        """LLM 프롬프트용 상태 컨텍스트"""
        return state.to_prompt_context()

    def build_clarify_response_data(
        self,
        state: ConversationState,
        anaphora_type: AnaphoraType,
    ) -> Tuple[List[dict], str]:
        """되묻기 응답 데이터 생성"""
        options = build_clarify_options(state, anaphora_type)
        message = build_clarify_message(options, anaphora_type)
        return options, message


# =============================================================================
# 싱글턴
# =============================================================================

_context_handler: Optional[ChatContextHandler] = None


def get_context_handler() -> ChatContextHandler:
    """ChatContextHandler 싱글턴 인스턴스"""
    global _context_handler
    if _context_handler is None:
        _context_handler = ChatContextHandler()
    return _context_handler


def clear_context_handler_cache() -> None:
    """테스트용 캐시 클리어"""
    global _context_handler
    _context_handler = None
