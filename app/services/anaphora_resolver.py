"""
Anaphora Resolver - 지시어 해소

"그 규정", "아까 것", "해당 문서" 등의 지시어를 이전 맥락에서 해소.

처리 순서:
1. 규칙 기반 해소 (빠름, 결정적)
2. 후보잠금 LLM fallback (복잡한 경우)
3. 되묻기 (해소 실패 시)

설계 원칙:
- 규칙 기반이 80% 케이스 커버
- LLM은 후보를 잠그고 선택만 하게 함 (자유 재작성 금지)
- 쿼리 자체는 변형 최소화, 구조화 정보 주입
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Pattern, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.conversation_state import ConversationState, DocReference

logger = logging.getLogger(__name__)


# =============================================================================
# 지시어 패턴 정의
# =============================================================================

class AnaphoraType(Enum):
    """지시어 유형"""

    PREV_DOC = "prev_doc"          # "그 규정", "해당 문서"
    PREV_EDUCATION = "prev_education"  # "그 교육", "해당 교육"
    PREV_ANSWER = "prev_answer"    # "그거", "아까 것"
    ELABORATE = "elaborate"        # "더 자세히", "상세히"
    SUMMARIZE = "summarize"        # "요약해줘", "정리해줘"
    ARTICLE_REF = "article_ref"    # "10조", "제3항" (조항 참조)
    NONE = "none"                  # 지시어 없음


@dataclass
class AnaphoraPattern:
    """지시어 패턴 정의"""

    pattern: Pattern[str]
    anaphora_type: AnaphoraType
    priority: int = 0  # 높을수록 우선


# 지시어 패턴 목록 (우선순위 순)
ANAPHORA_PATTERNS: List[AnaphoraPattern] = [
    # 문서 참조 (높은 우선순위)
    AnaphoraPattern(
        re.compile(r"(그|해당|위|아까)\s*(규정|문서|정책|지침)", re.IGNORECASE),
        AnaphoraType.PREV_DOC,
        priority=100,
    ),
    AnaphoraPattern(
        re.compile(r"(그|해당|위|아까)\s*(교육|영상|강의)", re.IGNORECASE),
        AnaphoraType.PREV_EDUCATION,
        priority=100,
    ),

    # 조항 참조 (숫자 + 조/항/호)
    AnaphoraPattern(
        re.compile(r"(제?\d+)\s*(조|항|호)", re.IGNORECASE),
        AnaphoraType.ARTICLE_REF,
        priority=90,
    ),

    # 일반 지시어
    AnaphoraPattern(
        re.compile(r"^(그거|그것|아까\s*것|방금\s*것|위\s*내용)", re.IGNORECASE),
        AnaphoraType.PREV_ANSWER,
        priority=80,
    ),

    # 메타 요청
    AnaphoraPattern(
        re.compile(r"^(더\s*자세히|상세히|구체적으로|자세하게)", re.IGNORECASE),
        AnaphoraType.ELABORATE,
        priority=70,
    ),
    AnaphoraPattern(
        re.compile(r"^(요약|정리|간단히|짧게)", re.IGNORECASE),
        AnaphoraType.SUMMARIZE,
        priority=70,
    ),
]


# =============================================================================
# 해소 결과
# =============================================================================

@dataclass
class ResolveResult:
    """지시어 해소 결과"""

    resolved: bool
    query: str  # 해소된 쿼리 (또는 원본)
    anaphora_type: AnaphoraType
    resolved_doc_id: Optional[str] = None
    resolved_doc_title: Optional[str] = None
    method: str = "none"  # "rule" | "llm" | "clarify" | "none"
    confidence: float = 1.0

    @classmethod
    def no_anaphora(cls, query: str) -> "ResolveResult":
        """지시어 없음"""
        return cls(
            resolved=True,
            query=query,
            anaphora_type=AnaphoraType.NONE,
            method="none",
        )

    @classmethod
    def needs_clarify(cls, query: str, anaphora_type: AnaphoraType) -> "ResolveResult":
        """되묻기 필요"""
        return cls(
            resolved=False,
            query=query,
            anaphora_type=anaphora_type,
            method="clarify",
            confidence=0.0,
        )


# =============================================================================
# 지시어 감지
# =============================================================================

def detect_anaphora(query: str) -> Tuple[AnaphoraType, Optional[re.Match]]:
    """
    쿼리에서 지시어 감지

    Args:
        query: 사용자 쿼리

    Returns:
        Tuple[AnaphoraType, Optional[Match]]: (지시어 유형, 매치 객체)
    """
    best_match: Optional[Tuple[AnaphoraType, re.Match, int]] = None

    for ap in ANAPHORA_PATTERNS:
        match = ap.pattern.search(query)
        if match:
            if best_match is None or ap.priority > best_match[2]:
                best_match = (ap.anaphora_type, match, ap.priority)

    if best_match:
        return best_match[0], best_match[1]

    return AnaphoraType.NONE, None


def has_anaphora(query: str) -> bool:
    """지시어 존재 여부"""
    anaphora_type, _ = detect_anaphora(query)
    return anaphora_type != AnaphoraType.NONE


# =============================================================================
# 규칙 기반 해소
# =============================================================================

def resolve_anaphora_rule(
    query: str,
    state: "ConversationState",
) -> Optional[ResolveResult]:
    """
    규칙 기반 지시어 해소

    Args:
        query: 사용자 쿼리
        state: 대화 상태

    Returns:
        Optional[ResolveResult]: 해소 결과 (해소 불가 시 None)
    """
    anaphora_type, match = detect_anaphora(query)

    if anaphora_type == AnaphoraType.NONE:
        return ResolveResult.no_anaphora(query)

    last_doc = state.get_last_doc()

    # 문서 참조: "그 규정" → "연차휴가규정"
    if anaphora_type == AnaphoraType.PREV_DOC:
        if last_doc and last_doc.domain in ("POLICY", "SECURITY", "INCIDENT"):
            resolved_query = _replace_anaphora(query, match, last_doc.title)
            return ResolveResult(
                resolved=True,
                query=resolved_query,
                anaphora_type=anaphora_type,
                resolved_doc_id=last_doc.doc_id,
                resolved_doc_title=last_doc.title,
                method="rule",
            )

    # 교육 참조: "그 교육" → "정보보안교육"
    if anaphora_type == AnaphoraType.PREV_EDUCATION:
        if last_doc and last_doc.domain in ("EDUCATION", "FOUR_MANDATORY"):
            resolved_query = _replace_anaphora(query, match, last_doc.title)
            return ResolveResult(
                resolved=True,
                query=resolved_query,
                anaphora_type=anaphora_type,
                resolved_doc_id=last_doc.doc_id,
                resolved_doc_title=last_doc.title,
                method="rule",
            )

    # 조항 참조: "10조" → 이전 문서 컨텍스트에서 해당 조항
    if anaphora_type == AnaphoraType.ARTICLE_REF:
        if last_doc:
            # 조항 번호 추출
            article_match = re.search(r"(제?\d+)\s*(조|항|호)", query)
            if article_match:
                article_num = article_match.group(0)
                # 문서 제목 + 조항으로 쿼리 보강
                resolved_query = f"{last_doc.title} {article_num}"
                return ResolveResult(
                    resolved=True,
                    query=resolved_query,
                    anaphora_type=anaphora_type,
                    resolved_doc_id=last_doc.doc_id,
                    resolved_doc_title=last_doc.title,
                    method="rule",
                )

    # 메타 요청: "더 자세히" → 이전 문서 컨텍스트 유지
    if anaphora_type in (AnaphoraType.ELABORATE, AnaphoraType.SUMMARIZE):
        if last_doc:
            # 쿼리는 그대로, doc_id만 전달
            return ResolveResult(
                resolved=True,
                query=query,
                anaphora_type=anaphora_type,
                resolved_doc_id=last_doc.doc_id,
                resolved_doc_title=last_doc.title,
                method="rule",
            )

    # 일반 지시어: "그거" → 해소 불가, LLM fallback 필요
    if anaphora_type == AnaphoraType.PREV_ANSWER:
        # 규칙으로는 해소 불가
        return None

    return None


def _replace_anaphora(query: str, match: re.Match, replacement: str) -> str:
    """지시어를 대체 텍스트로 치환"""
    start, end = match.span()
    return query[:start] + replacement + query[end:]


# =============================================================================
# 후보잠금 LLM 해소
# =============================================================================

async def resolve_anaphora_llm(
    query: str,
    state: "ConversationState",
    llm_client: Optional[object] = None,
) -> Optional[ResolveResult]:
    """
    후보잠금 LLM 지시어 해소

    state.recent_docs를 후보로 제공하고, LLM이 그 중에서만 선택하게 함.

    Args:
        query: 사용자 쿼리
        state: 대화 상태
        llm_client: LLM 클라이언트 (None이면 규칙 기반만 사용)

    Returns:
        Optional[ResolveResult]: 해소 결과
    """
    if not state.recent_docs:
        return None

    anaphora_type, _ = detect_anaphora(query)
    if anaphora_type == AnaphoraType.NONE:
        return ResolveResult.no_anaphora(query)

    # 후보 구성
    candidates = _build_candidates(state)
    if not candidates:
        return None

    # LLM 클라이언트가 없으면 첫 번째 후보 선택 (간단한 fallback)
    if llm_client is None:
        logger.debug("LLM client not available, using first candidate")
        first_doc = state.recent_docs[0]
        return ResolveResult(
            resolved=True,
            query=_resolve_with_doc(query, first_doc.title),
            anaphora_type=anaphora_type,
            resolved_doc_id=first_doc.doc_id,
            resolved_doc_title=first_doc.title,
            method="llm_fallback",
            confidence=0.7,
        )

    # LLM 호출 (후보잠금)
    try:
        result = await _call_llm_for_resolution(query, candidates, llm_client)
        if result:
            return result
    except Exception as e:
        logger.error(f"LLM resolution failed: {e}")

    return None


def _build_candidates(state: "ConversationState") -> List[Dict[str, str]]:
    """후보 목록 구성 (recent_docs + 엔티티)"""
    candidates = []

    for doc in state.recent_docs[:3]:  # 최대 3개
        candidates.append({
            "type": "doc",
            "id": doc.doc_id,
            "title": doc.title,
            "domain": doc.domain,
        })

    if state.last_entities:
        candidates.append({
            "type": "entities",
            "keywords": ", ".join(state.last_entities[:5]),
        })

    return candidates


def _resolve_with_doc(query: str, doc_title: str) -> str:
    """문서 제목으로 쿼리 보강"""
    # 지시어를 직접 치환하는 대신, 문서 맥락을 앞에 추가
    return f"{doc_title}에 대해: {query}"


async def _call_llm_for_resolution(
    query: str,
    candidates: List[Dict[str, str]],
    llm_client: object,
) -> Optional[ResolveResult]:
    """
    LLM 호출하여 지시어 해소

    NOTE: 실제 LLM 호출 로직은 프로젝트의 LLM 클라이언트에 맞게 구현 필요
    """
    # TODO: 실제 LLM 호출 구현
    # 현재는 placeholder
    logger.debug(f"LLM resolution called with {len(candidates)} candidates")
    return None


# =============================================================================
# 통합 해소 함수
# =============================================================================

async def resolve_anaphora(
    query: str,
    state: "ConversationState",
    use_llm: bool = True,
    llm_client: Optional[object] = None,
) -> ResolveResult:
    """
    지시어 해소 (통합)

    처리 순서:
    1. 지시어 없으면 바로 반환
    2. 규칙 기반 해소 시도
    3. LLM fallback (use_llm=True 시)
    4. 해소 실패 시 되묻기 필요 표시

    Args:
        query: 사용자 쿼리
        state: 대화 상태
        use_llm: LLM fallback 사용 여부
        llm_client: LLM 클라이언트

    Returns:
        ResolveResult: 해소 결과
    """
    # 1. 지시어 감지
    anaphora_type, _ = detect_anaphora(query)
    if anaphora_type == AnaphoraType.NONE:
        return ResolveResult.no_anaphora(query)

    logger.debug(f"Anaphora detected: {anaphora_type.value} in query: {query[:50]}...")

    # 2. 규칙 기반 해소
    rule_result = resolve_anaphora_rule(query, state)
    if rule_result and rule_result.resolved:
        logger.debug(f"Rule-based resolution: {rule_result.query[:50]}...")
        return rule_result

    # 3. LLM fallback
    if use_llm:
        llm_result = await resolve_anaphora_llm(query, state, llm_client)
        if llm_result and llm_result.resolved:
            logger.debug(f"LLM resolution: {llm_result.query[:50]}...")
            return llm_result

    # 4. 해소 실패 → 되묻기 필요
    logger.debug(f"Resolution failed, needs clarify for: {anaphora_type.value}")
    return ResolveResult.needs_clarify(query, anaphora_type)


# =============================================================================
# 부스팅 조건 판단
# =============================================================================

def should_apply_boost(
    query: str,
    state: "ConversationState",
    max_turn_distance: int = 2,
) -> bool:
    """
    부스팅 적용 여부 판단 (3종 세트 조건)

    조건:
    1. 지시어 존재
    2. 도메인 일치 (토픽 전환 아님)
    3. 턴 거리 짧음

    Args:
        query: 사용자 쿼리
        state: 대화 상태
        max_turn_distance: 최대 허용 턴 거리

    Returns:
        bool: 부스팅 적용 여부
    """
    # 1. 지시어 존재
    if not has_anaphora(query):
        return False

    # 2. 최근 문서 존재
    last_doc = state.get_last_doc()
    if not last_doc:
        return False

    # 3. 턴 거리 확인
    turn_distance = state.get_turn_distance(last_doc)
    if turn_distance > max_turn_distance:
        logger.debug(f"Turn distance {turn_distance} > {max_turn_distance}, boost disabled")
        return False

    # 4. 도메인 일치는 라우터 결과로 판단 (여기서는 체크 안 함)
    # → ChatService에서 라우터 결과와 비교

    return True


# =============================================================================
# 되묻기 옵션 생성
# =============================================================================

def build_clarify_options(
    state: "ConversationState",
    anaphora_type: AnaphoraType,
) -> List[Dict[str, str]]:
    """
    되묻기 선택지 생성

    Args:
        state: 대화 상태
        anaphora_type: 지시어 유형

    Returns:
        List[Dict[str, str]]: 선택지 목록 [{"id": ..., "label": ...}, ...]
    """
    options = []

    for doc in state.recent_docs[:3]:
        # 도메인에 따른 필터링
        if anaphora_type == AnaphoraType.PREV_DOC:
            if doc.domain not in ("POLICY", "SECURITY", "INCIDENT"):
                continue
        elif anaphora_type == AnaphoraType.PREV_EDUCATION:
            if doc.domain not in ("EDUCATION", "FOUR_MANDATORY"):
                continue

        options.append({
            "id": doc.doc_id,
            "label": doc.title,
            "domain": doc.domain,
        })

    return options


def build_clarify_message(
    options: List[Dict[str, str]],
    anaphora_type: AnaphoraType,
) -> str:
    """
    되묻기 메시지 생성

    Args:
        options: 선택지 목록
        anaphora_type: 지시어 유형

    Returns:
        str: 되묻기 메시지
    """
    if not options:
        return "어떤 문서에 대해 말씀하시는 건가요?"

    if anaphora_type == AnaphoraType.PREV_DOC:
        prefix = "어떤 규정/문서를 말씀하시는 건가요?"
    elif anaphora_type == AnaphoraType.PREV_EDUCATION:
        prefix = "어떤 교육을 말씀하시는 건가요?"
    else:
        prefix = "어떤 내용을 말씀하시는 건가요?"

    option_lines = [f"{i+1}) {opt['label']}" for i, opt in enumerate(options)]
    return f"{prefix}\n" + "\n".join(option_lines)
