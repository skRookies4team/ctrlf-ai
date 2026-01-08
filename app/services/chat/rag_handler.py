"""
RAG 검색 핸들러 (RAG Search Handler)

ChatService에서 사용하는 RAG 검색 로직을 담당합니다.

Phase 2 리팩토링:
- ChatService._perform_rag_search → RagHandler.perform_search
- ChatService._perform_rag_search_with_fallback → RagHandler.perform_search_with_fallback

Option 3 통합 (Chat):
- CHAT_RETRIEVER_BACKEND=milvus 시 Milvus 직접 검색 사용
- Milvus 실패/empty 시 RAGFlow로 fallback
- retriever_used 필드로 실제 사용된 검색 엔진 반환
- 컨텍스트 길이 제한 (CHAT_CONTEXT_MAX_CHARS)

Phase 44: 2nd-chance retrieval & Query Normalization
- 1차 검색 결과 0건 시 top_k 올려서 재시도 (5 → 15)
- 검색 전 마스킹 토큰 제거 ([PERSON], [PHONE] 등)
- 과도한 공백/특수문자 정규화

Phase 45: Similarity 분포 로깅 (디버깅/진단용)
- 검색 결과의 L2 거리 분포 로깅 (min/max/avg)
- 0건 결과 시 원인 분석을 위한 상세 로깅

Phase 48: Low-relevance Gate (L2 거리 기준)
- min_score(최소 거리) > RAG_MAX_L2_DISTANCE → sources soft 강등
- L2 거리: 낮을수록 유사함 (0 = 완전 일치)
- 앵커 키워드가 sources 텍스트에 없으면 → sources=[] 강등
- 저관련 검색 결과로 '근거 있는 척' 하는 현상 방지

Phase 57: 고급 RAG 기법
- Query Expansion: 짧은 쿼리를 검색 키워드로 확장 (LLM 사용)
- RAG Fusion (RRF): 원문 + 확장 쿼리 결과를 Reciprocal Rank Fusion으로 융합
"""

import re
from dataclasses import dataclass
from typing import Iterator, List, Literal, Optional, Tuple



# =============================================================================
# Phase 48.1: ASCII-safe query preview (로그 한글 깨짐 방지)
# =============================================================================

def ascii_safe_preview(text: str, max_len: int = 50) -> str:
    """
    로그 출력용 ASCII-safe 텍스트 미리보기를 생성합니다.

    Git Bash 파이프, Windows cp949, locale 문제로 인한 한글 깨짐(mojibake) 방지.
    한글은 \\uXXXX 형태로 escape하여 터미널 환경에 무관하게 안전하게 출력.

    Args:
        text: 원본 텍스트
        max_len: 최대 길이 (기본 50자)

    Returns:
        ASCII-safe 문자열 (한글은 \\uXXXX로 변환)

    Example:
        >>> ascii_safe_preview("연차 규정 알려줘")
        '\\uc5f0\\ucc28 \\uaddc\\uc815 \\uc54c\\ub824\\uc918'
    """
    if not text:
        return ""
    truncated = text[:max_len]
    # ASCII 문자만 남기고, 나머지는 \\uXXXX로 escape
    return truncated.encode("unicode_escape").decode("ascii")


from app.clients.milvus_client import (
    MilvusSearchClient,
    MilvusSearchError,
    get_milvus_client,
)
from app.clients.ab_milvus_client import (
    get_milvus_client_by_model,
    get_client_info_by_model,
    # Deprecated: 하위 호환용
    get_ab_milvus_client,
    get_ab_client_info,
)
from app.core.config import get_settings
from app.core.exceptions import UpstreamServiceError
from app.core.logging import get_logger
from app.core.metrics import (
    LOG_TAG_RAG_ERROR,
    metrics,
)
from app.models.chat import ChatRequest, ChatSource
from app.utils.debug_log import dbg_final_query, dbg_retrieval_top5, dbg_retrieval_target
from app.core.retrieval_context import (
    is_retrieval_blocked,
    get_block_reason,
    RetrievalBlockedError,
)
from app.services.search_merger import rrf_fuse_with_sources
from app.services.chat.query_rewriter import expand_query_sync, RewriteResult
from app.services.chat.rag_quality_log import (
    build_rag_quality_log,
    log_rag_quality,
)
from app.services.chat.quality_gate import (
    QualityAction,
    evaluate_sources_quality,
    build_clarification_response,
    log_quality_gate_decision,
)

logger = get_logger(__name__)

# retriever_used 타입 정의 (Phase 50: BLOCKED 추가)
RetrieverUsed = Literal["MILVUS", "RAGFLOW", "RAGFLOW_FALLBACK", "BLOCKED"]


# =============================================================================
# Phase 58: RagRetrievalResult 데이터클래스
# =============================================================================

@dataclass
class RagRetrievalResult:
    """
    RAG 검색 결과 (Phase 58 확장)

    기존 tuple 반환 (sources, failed, retriever_used)와 역호환되며,
    Phase 58 품질 게이트 필드를 추가로 제공합니다.

    Attributes:
        sources: 검색된 ChatSource 리스트
        failed: 검색 실패 여부 (0건도 False)
        retriever_used: 사용된 검색기 (MILVUS, RAGFLOW 등)
        insufficient_evidence: 근거 부족 판정 (True면 LLM 호출 스킵)
        quality_grade: 품질 등급 (OK, LOW, INSUFFICIENT)
        quality_action: 권장 액션 (PROCEED, PROCEED_WITH_WARNING, REJECT)
        min_l2_distance: 최소 L2 거리
        clarify_message: 명확화 메시지 (INSUFFICIENT 시)
        warning_message: 경고 메시지 (LOW 시)
    """
    sources: List["ChatSource"]
    failed: bool
    retriever_used: RetrieverUsed

    # Phase 58: Quality Gate 필드
    insufficient_evidence: bool = False
    quality_grade: str = "OK"
    quality_action: str = "PROCEED"
    min_l2_distance: float = 0.0
    clarify_message: Optional[str] = None
    warning_message: Optional[str] = None

    def __iter__(self) -> Iterator:
        """
        기존 tuple unpacking과의 역호환성 지원.

        Usage:
            sources, failed, retriever = result  # 기존 코드
            # 또는
            result.insufficient_evidence  # 새 필드 접근
        """
        return iter((self.sources, self.failed, self.retriever_used))


# Phase 44: 검색 설정 상수
DEFAULT_TOP_K = 5
RETRY_TOP_K = 15  # 2nd-chance retrieval에서 사용할 top_k

# =============================================================================
# Phase 50: LowRelevanceGate 개선
# =============================================================================

# Phase 50: anchor_gate 안전장치 - 최소 보장 개수
# anchor 미매칭 시에도 최소 1개는 유지 (hard-drop 방지)
ANCHOR_GATE_MIN_KEEP = 1

# Phase 50: 행동 표현 접미사 패턴 (anchor에서 제거)
# "요약해줘" → "요약", "알려주세요" → "" (완전 제거)
ACTION_SUFFIX_PATTERN = re.compile(
    r'(해줘|해주세요|해줄래|해줄게|할래|하세요|해봐|해라|'
    r'알려줘|알려주세요|알려줄래|알려주라|'
    r'설명해|설명해줘|설명해주세요|'
    r'정리해|정리해줘|정리해주세요|'
    r'보여줘|보여주세요|찾아줘|찾아주세요|'
    r'줘|주세요|줄래|주라)$'
)

# Phase 50: 행동 표현 전체 토큰 (anchor에서 완전 제거할 토큰들)
# 이 토큰들은 명사가 아닌 요청/행동 표현이므로 anchor에서 제외
ACTION_TOKENS = frozenset([
    # 요약/정리 요청
    "요약해줘", "요약해주세요", "요약해", "요약좀", "요약",
    "정리해줘", "정리해주세요", "정리해", "정리좀", "정리",
    # 설명/알려줘 요청
    "알려줘", "알려주세요", "알려줄래", "알려줘요",
    "설명해줘", "설명해주세요", "설명해", "설명좀",
    # 보여줘/찾아줘 요청
    "보여줘", "보여주세요", "찾아줘", "찾아주세요",
    # 일반 조동사/요청어
    "해줘", "해주세요", "해줄래", "좀", "부탁", "뭐야", "뭔가",
])

# Phase 44: 마스킹 토큰 패턴 (PII 마스킹 후 남은 토큰들)
MASKING_TOKEN_PATTERN = re.compile(
    r'\[(PERSON|NAME|PHONE|EMAIL|ADDRESS|SSN|CARD|ACCOUNT|DATE|ORG)\]',
    re.IGNORECASE
)

# Phase 44: 특수문자/과도한 공백 정규화 패턴
SPECIAL_CHAR_PATTERN = re.compile(r'[^\w\s가-힣?!.,]')
MULTI_SPACE_PATTERN = re.compile(r'\s+')


def log_similarity_distribution(
    sources: List["ChatSource"],
    search_stage: str,
    query_preview: str,
    domain: str,
) -> None:
    """
    Phase 45: 검색 결과의 L2 거리 분포를 로깅합니다.

    디버깅/진단용으로, 검색 결과가 0건일 때 원인 분석에 유용합니다.
    - 검색 결과 수, min/max/avg L2 거리 로깅
    - L2 거리 구간별 분포 로깅 (낮을수록 유사함)
      - <0.8: 매우 유사
      - 0.8~1.2: 유사
      - 1.2~1.5: 중간
      - >=1.5: 관련성 낮음

    Args:
        sources: RAG 검색 결과 리스트
        search_stage: 검색 단계 ("1st_search" 또는 "2nd_chance")
        query_preview: 검색 쿼리 앞부분 (로깅용, 50자 제한)
        domain: 검색 도메인
    """
    if not sources:
        # Phase 48.1: ASCII-safe query preview
        query_safe = ascii_safe_preview(query_preview, 50)
        logger.info(
            f"[L2Distance] {search_stage}: 0 results | "
            f"domain={domain} | query='{query_safe}'"
        )
        return

    scores = [s.score for s in sources if s.score is not None]
    if not scores:
        logger.info(
            f"[L2Distance] {search_stage}: {len(sources)} results (no scores) | "
            f"domain={domain}"
        )
        return

    min_score = min(scores)  # 최소 거리 = 가장 유사
    max_score = max(scores)  # 최대 거리 = 가장 멀음
    avg_score = sum(scores) / len(scores)

    # L2 거리 구간별 분포 (낮을수록 좋음)
    very_close = sum(1 for s in scores if s < 0.8)        # 매우 유사
    close = sum(1 for s in scores if 0.8 <= s < 1.2)      # 유사
    medium = sum(1 for s in scores if 1.2 <= s < 1.5)     # 중간
    far = sum(1 for s in scores if s >= 1.5)              # 관련성 낮음

    logger.info(
        f"[L2Distance] {search_stage}: {len(sources)} results | "
        f"min={min_score:.3f}, max={max_score:.3f}, avg={avg_score:.3f} | "
        f"distribution: [<0.8:{very_close}, 0.8-1.2:{close}, 1.2-1.5:{medium}, >=1.5:{far}] | "
        f"domain={domain}"
    )


def normalize_query_for_search(query: str) -> str:
    """
    RAG 검색용으로 쿼리를 정규화합니다.

    Phase 44: 마스킹 토큰, 특수문자, 과도한 공백 제거

    Args:
        query: 원본 쿼리 (마스킹 처리된 상태)

    Returns:
        검색용 정규화된 쿼리

    Examples:
        >>> normalize_query_for_search("[PERSON]의 연차 규정은?")
        "의 연차 규정은?"
        >>> normalize_query_for_search("  연차   규정이   뭐야??  ")
        "연차 규정이 뭐야?"
    """
    # Step 1: 마스킹 토큰 제거
    normalized = MASKING_TOKEN_PATTERN.sub('', query)

    # Step 2: 연속 물음표/느낌표 → 단일화
    normalized = re.sub(r'\?{2,}', '?', normalized)
    normalized = re.sub(r'!{2,}', '!', normalized)

    # Step 3: 과도한 공백 정규화
    normalized = MULTI_SPACE_PATTERN.sub(' ', normalized)

    # Step 4: 앞뒤 공백 제거
    normalized = normalized.strip()

    return normalized


# =============================================================================
# Phase 48: Low-relevance Gate
# =============================================================================


def get_anchor_stopwords() -> set:
    """
    Phase 48: 앵커 키워드 추출용 불용어 세트를 반환합니다.

    settings.RAG_ANCHOR_STOPWORDS에서 로드.
    """
    settings = get_settings()
    stopwords_str = settings.RAG_ANCHOR_STOPWORDS
    return set(word.strip() for word in stopwords_str.split(",") if word.strip())


def extract_anchor_keywords(query: str) -> set:
    """
    Phase 48/50: 쿼리에서 앵커 키워드를 추출합니다.

    Phase 50 개선:
    - 행동 표현 토큰(요약해줘, 알려줘 등) 완전 제거
    - 행동 접미사(-해줘, -해주세요 등) 제거 후 명사 부분만 추출

    Args:
        query: 원본 쿼리

    Returns:
        set: 앵커 키워드 세트 (소문자, 명사/핵심어만)

    Examples:
        >>> extract_anchor_keywords("연차휴가 규정 알려줘")
        {'연차휴가'}  # '규정'은 stopwords, '알려줘'는 ACTION_TOKENS
        >>> extract_anchor_keywords("보안 관련 문서 요약해줘")
        {'보안'}  # '관련', '문서', '요약해줘'는 제거됨
    """
    stopwords = get_anchor_stopwords()

    # 특수문자 제거 (한글, 영문, 숫자만 유지)
    cleaned = re.sub(r'[^\w\s가-힣]', ' ', query)

    # 공백 분리 및 소문자 변환
    tokens = cleaned.lower().split()

    anchor_keywords = set()
    for token in tokens:
        # Phase 50: 1글자 토큰 제거
        if len(token) <= 1:
            continue

        # Phase 50: 행동 표현 전체 토큰 제거
        if token in ACTION_TOKENS:
            continue

        # Phase 50: 불용어 제거
        if token in stopwords:
            continue

        # Phase 50: 행동 접미사 제거하여 명사 부분 추출
        # "요약해줘" → "요약", "보안설명해줘" → "보안"
        stripped = ACTION_SUFFIX_PATTERN.sub('', token)
        if stripped and len(stripped) > 1:
            # 접미사 제거 후 남은 부분이 유효하면 추가
            if stripped not in stopwords and stripped not in ACTION_TOKENS:
                anchor_keywords.add(stripped)
        elif stripped and len(stripped) == 1:
            # 1글자만 남으면 원본 토큰이 모두 접미사였음 → 스킵
            continue
        else:
            # 접미사가 없거나 접미사 제거 후에도 유효 → 원본 사용
            anchor_keywords.add(token)

    return anchor_keywords


def check_anchor_keywords_in_sources(
    anchor_keywords: set,
    sources: List["ChatSource"],
) -> bool:
    """
    Phase 48/50: 앵커 키워드가 sources 텍스트에 하나라도 있는지 확인합니다.

    Phase 50 개선:
    - title, snippet뿐만 아니라 article_label, article_path도 검사
    - 더 넓은 범위에서 키워드 매칭 시도

    Args:
        anchor_keywords: 앵커 키워드 세트
        sources: RAG 검색 결과

    Returns:
        bool: 하나라도 매칭되면 True
    """
    if not anchor_keywords:
        # 앵커 키워드가 없으면 (모두 불용어) → 통과
        return True

    # Phase 50: 모든 sources의 snippet/title/article_label/article_path를 합쳐서 검색
    combined_text = ""
    for source in sources:
        if source.snippet:
            combined_text += source.snippet.lower() + " "
        if source.title:
            combined_text += source.title.lower() + " "
        # Phase 50: article_label, article_path 추가
        if source.article_label:
            combined_text += source.article_label.lower() + " "
        if source.article_path:
            combined_text += source.article_path.lower() + " "

    # 앵커 키워드 중 하나라도 있으면 통과
    for keyword in anchor_keywords:
        if keyword in combined_text:
            return True

    return False


def apply_low_relevance_gate(
    sources: List["ChatSource"],
    query: str,
    domain: str,
) -> Tuple[List["ChatSource"], Optional[str]]:
    """
    Phase 48/50/52.1: 저관련 검색 결과를 필터링합니다. (L2 거리 기준)

    L2 거리: 낮을수록 유사함 (0 = 완전 일치)
    - min_score(최소 거리) = 가장 유사한 결과의 거리
    - min_score > threshold → 가장 가까운 결과도 너무 멀다 → low relevance

    Phase 50 개선:
    - score_gate: min_score > threshold 시에도 최소 1개는 유지 (soft gate)
    - anchor_gate: 미매칭 시에도 최소 ANCHOR_GATE_MIN_KEEP개는 유지

    Phase 52.1 개선 (RAG 품질 게이트 강화):
    - RAG_QUALITY_HARD_DROP_ENABLED=True면:
      - Gate A': min_score > RAG_QUALITY_DROP_THRESHOLD → HARD_DROP (sources=[])
      - Gate B: 앵커 키워드 미매칭 → HARD_DROP (sources=[])
    - RAG가 억지로 근거 없는 정책 안내를 하는 것 방지

    두 가지 게이트를 적용:
    A. L2 거리 게이트: min_score > threshold → soft/hard 강등
    B. 앵커 키워드 게이트: 핵심어 미매칭 → soft/hard 강등

    Args:
        sources: RAG 검색 결과
        query: 원본 쿼리
        domain: 검색 도메인

    Returns:
        Tuple[List[ChatSource], Optional[str]]:
            - 필터링된 sources
            - gate_reason (강등 시 사유, 통과 시 None)
    """
    settings = get_settings()

    if not sources:
        return sources, None

    # L2 거리 계산 (낮을수록 유사함)
    scores = [s.score for s in sources if s.score is not None]
    if not scores:
        # score가 없는 경우 → 통과 (RAGFlow 등에서 score 없이 반환하는 경우)
        return sources, None

    min_score = min(scores)  # 최소 거리 = 가장 유사한 결과
    max_score = max(scores)  # 최대 거리 = 가장 먼 결과
    avg_score = sum(scores) / len(scores)
    max_l2_threshold = settings.RAG_MAX_L2_DISTANCE

    # Phase 52.1: 품질 게이트 강화 설정
    hard_drop_enabled = settings.RAG_QUALITY_HARD_DROP_ENABLED
    hard_drop_threshold = settings.RAG_QUALITY_DROP_THRESHOLD

    # Phase 50: 안전장치 - 최소 유지 개수 (soft gate용)
    min_keep = ANCHOR_GATE_MIN_KEEP

    # Gate A': L2 거리 HARD 게이트 (Phase 52.1)
    # min_score > hard_drop_threshold → 완전 drop (검색 결과가 너무 관련 없음)
    if hard_drop_enabled and min_score > hard_drop_threshold:
        query_safe = ascii_safe_preview(query, 50)
        logger.warning(
            f"[LowRelevanceGate] HARD_DROP by l2_distance_gate | "
            f"min_score={min_score:.3f} > hard_threshold={hard_drop_threshold} (extremely far) | "
            f"query='{query_safe}' | domain={domain} | "
            f"avg_score={avg_score:.3f} | max_score={max_score:.3f} | top_k={len(sources)}"
        )
        return [], "l2_distance_hard_drop"

    # Gate A: L2 거리 soft 게이트 (최소 거리가 threshold보다 크면 = 너무 멀면)
    if min_score > max_l2_threshold:
        # Phase 50: 완전 drop 대신 최소 min_keep개 유지
        kept_sources = sources[:min_keep]
        query_safe = ascii_safe_preview(query, 50)
        logger.warning(
            f"[LowRelevanceGate] SOFT_DEMOTE by l2_distance_gate | "
            f"min_score={min_score:.3f} > threshold={max_l2_threshold} (too far) | "
            f"query='{query_safe}' | domain={domain} | "
            f"avg_score={avg_score:.3f} | max_score={max_score:.3f} | top_k={len(sources)} | "
            f"kept_count={len(kept_sources)} (min_keep={min_keep})"
        )
        return kept_sources, "min_l2_distance_above_threshold_soft"

    # Gate B: 앵커 키워드 게이트
    anchor_keywords = extract_anchor_keywords(query)
    has_anchor_match = check_anchor_keywords_in_sources(anchor_keywords, sources)

    if not has_anchor_match:
        # Phase 52.1: hard_drop_enabled면 완전 drop
        if hard_drop_enabled:
            query_safe = ascii_safe_preview(query, 50)
            keywords_safe = {ascii_safe_preview(kw, 20) for kw in anchor_keywords}
            logger.warning(
                f"[LowRelevanceGate] HARD_DROP by anchor_gate | "
                f"anchor_keywords={keywords_safe} not found in sources | "
                f"query='{query_safe}' | domain={domain} | "
                f"min_score={min_score:.3f} | avg_score={avg_score:.3f} | "
                f"top_k={len(sources)}"
            )
            return [], "anchor_no_match_hard_drop"

        # Phase 50: soft gate - 완전 drop 대신 최소 min_keep개 유지
        kept_sources = sources[:min_keep]
        query_safe = ascii_safe_preview(query, 50)
        keywords_safe = {ascii_safe_preview(kw, 20) for kw in anchor_keywords}
        logger.warning(
            f"[LowRelevanceGate] SOFT_DEMOTE by anchor_gate | "
            f"anchor_keywords={keywords_safe} not found in sources | "
            f"query='{query_safe}' | domain={domain} | "
            f"min_score={min_score:.3f} | avg_score={avg_score:.3f} | "
            f"top_k={len(sources)} | kept_count={len(kept_sources)} (min_keep={min_keep})"
        )
        return kept_sources, "no_anchor_term_match_soft"

    # 통과
    query_safe = ascii_safe_preview(query, 30)
    logger.info(
        f"[LowRelevanceGate] PASSED | "
        f"min_score={min_score:.3f} <= threshold={max_l2_threshold} | "
        f"anchor_match=True | query='{query_safe}' | domain={domain}"
    )
    return sources, None


class RagSearchUnavailableError(Exception):
    """RAG 검색 서비스 사용 불가 예외.

    RAGFlow/Milvus 모두 장애 시 503 반환을 위한 예외.
    """
    def __init__(self, message: str = "RAG 검색 서비스를 사용할 수 없습니다."):
        self.message = message
        super().__init__(self.message)


class RagHandler:
    """
    RAG 검색을 처리하는 핸들러 클래스.

    Option 3 통합:
    - CHAT_RETRIEVER_BACKEND 설정에 따라 Milvus 또는 RAGFlow 사용
    - Milvus 실패 시 RAGFlow로 fallback
    - retriever_used 필드로 실제 사용된 검색 엔진 추적

    Attributes:
        _milvus: Milvus 검색 클라이언트
        _use_milvus: Milvus 사용 여부
    
    Note:
        RAGFlow 클라이언트는 제거되었습니다. Milvus만 사용합니다.
    """

    def __init__(
        self,
        milvus_client: Optional[MilvusSearchClient] = None,
    ) -> None:
        """
        RagHandler 초기화.

        Args:
            milvus_client: Milvus 검색 클라이언트 (선택, None이면 자동 생성)
        
        Note:
            RAGFlow 클라이언트는 제거되었습니다 (재개발 예정).
            MILVUS_ENABLED=True 필수.
        """
        self._settings = get_settings()

        # Milvus 클라이언트 초기화
        self._use_milvus = self._settings.MILVUS_ENABLED

        if self._use_milvus:
            self._milvus = milvus_client or get_milvus_client()
            logger.info(
                f"RagHandler initialized with Milvus"
            )
        else:
            self._milvus = None
            logger.warning(
                "RagHandler: MILVUS_ENABLED=False, RAG search unavailable (RAGFlow removed)"
            )

    async def perform_search(
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

        Raises:
            RagSearchUnavailableError: 검색 서비스 장애 시
        """
        sources, _, _ = await self.perform_search_with_fallback(
            query=query,
            domain=domain,
            req=req,
            request_id=None,
        )
        return sources

    async def perform_search_with_fallback(
        self,
        query: str,
        domain: str,
        req: Optional[ChatRequest] = None,
        request_id: Optional[str] = None,
        top_k: Optional[int] = None,
        model: Optional[str] = None,
    ) -> RagRetrievalResult:
        """
        RAG 검색을 수행하고 실패 여부와 사용된 retriever를 함께 반환합니다.

        Option 3 통합:
        - CHAT_RETRIEVER_BACKEND=milvus: Milvus 먼저 시도 → 실패/empty 시 RAGFlow fallback
        - CHAT_RETRIEVER_BACKEND=ragflow: RAGFlow만 사용

        Phase 44: 2nd-chance retrieval & Query Normalization
        - 검색 전 쿼리 정규화 (마스킹 토큰 제거)
        - 1차 검색 결과 0건 → top_k 올려서 재시도 (5 → 15)

        Step 7: req 파라미터 옵셔널화
        - FaqService 등에서 ChatRequest 없이도 사용 가능
        - req=None이면 user_role, department는 None으로 전달

        Phase AB: A/B 테스트 지원
        - model 파라미터로 임베딩 모델 직접 선택 (권장)
        - request_id는 하위 호환용으로 유지

        Args:
            query: 검색 쿼리 (마스킹된 상태)
            domain: 도메인
            req: 원본 요청 (선택, None이면 user_role/department 없이 검색)
            request_id: 디버그용 요청 ID (deprecated for A/B)
            top_k: 검색 결과 개수 (선택, None이면 설정값 사용)
            model: A/B 테스트 모델 ("openai" | "sroberta", 권장)

        Returns:
            Tuple[List[ChatSource], bool, RetrieverUsed]:
                - 검색 결과
                - 실패 여부 (0건도 정상=False)
                - 사용된 retriever ("MILVUS", "RAGFLOW", "RAGFLOW_FALLBACK", "BLOCKED")

        Raises:
            RagSearchUnavailableError: 모든 검색 서비스 장애 시 (503 반환용)
        """
        # Phase 50: 2차 가드 - 컨텍스트 플래그 확인
        if is_retrieval_blocked():
            reason = get_block_reason() or "unknown"
            logger.warning(
                f"RagHandler: Retrieval blocked by context flag, returning empty sources. "
                f"reason={reason}"
            )
            return RagRetrievalResult(sources=[], failed=False, retriever_used="BLOCKED")

        # Phase 44: 검색용 쿼리 정규화 (마스킹 토큰 제거)
        normalized_query = normalize_query_for_search(query)

        # 디버그 로그: final_query
        if request_id:
            dbg_final_query(
                request_id=request_id,
                original_query=query,
                rewritten_query=normalized_query if normalized_query != query else None,
                keywords=None,
            )

        # Milvus 사용 시: Milvus → RAGFlow fallback
        if self._use_milvus and self._milvus:
            sources, failed, retriever = await self._search_with_milvus_fallback(
                query=normalized_query,
                domain=domain,
                req=req,
                request_id=request_id,
                top_k=top_k,
                model=model,
            )
        else:
            # RAGFlow만 사용
            sources, failed, retriever = await self._search_ragflow_only(
                query=normalized_query,
                domain=domain,
                req=req,
                request_id=request_id,
            )

        # Phase 48: Low-relevance Gate 적용
        # 저관련 검색 결과를 sources=[]로 강등
        if sources and not failed:
            sources, gate_reason = apply_low_relevance_gate(
                sources=sources,
                query=query,  # 원본 쿼리 사용 (마스킹 토큰 포함)
                domain=domain,
            )
            # gate_reason은 로깅용으로만 사용 (함수 내에서 이미 로깅됨)

        # =====================================================================
        # Phase 58: Quality Gate (L2 Distance 기반 응답 제어)
        # =====================================================================
        # SOFT_DEMOTE 이후에도 극단적 저품질 케이스 방지
        # min_l2 > reject_threshold → INSUFFICIENT → LLM 생성 스킵
        settings = get_settings()
        quality_gate_enabled = settings.RAG_QUALITY_DISTANCE_GATE_ENABLED

        if quality_gate_enabled and not failed:
            quality_decision = evaluate_sources_quality(
                sources=sources,
                warn_threshold=settings.RAG_QUALITY_L2_WARN,
                reject_threshold=settings.RAG_QUALITY_L2_REJECT,
            )

            # 품질 게이트 판정 로깅
            log_quality_gate_decision(quality_decision, query, domain)

            # REJECT 판정 시: 근거 부족 → LLM 생성 스킵
            if quality_decision.action == QualityAction.REJECT:
                clarify_msg = build_clarification_response(
                    decision=quality_decision,
                    query=query,
                    domain=domain,
                )

                return RagRetrievalResult(
                    sources=[],  # 소스 미사용 (환각 방지)
                    failed=False,
                    retriever_used=retriever,
                    insufficient_evidence=True,
                    quality_grade=quality_decision.grade.value,
                    quality_action=quality_decision.action.value,
                    min_l2_distance=quality_decision.min_l2_distance,
                    clarify_message=clarify_msg,
                )

            # PROCEED_WITH_WARNING 판정 시: 경고 메시지 첨부
            if quality_decision.action == QualityAction.PROCEED_WITH_WARNING:
                return RagRetrievalResult(
                    sources=sources,
                    failed=failed,
                    retriever_used=retriever,
                    insufficient_evidence=False,
                    quality_grade=quality_decision.grade.value,
                    quality_action=quality_decision.action.value,
                    min_l2_distance=quality_decision.min_l2_distance,
                    warning_message=quality_decision.warning_message,
                )

            # OK 판정: 정상 진행
            return RagRetrievalResult(
                sources=sources,
                failed=failed,
                retriever_used=retriever,
                insufficient_evidence=False,
                quality_grade=quality_decision.grade.value,
                quality_action=quality_decision.action.value,
                min_l2_distance=quality_decision.min_l2_distance,
            )

        # Quality Gate 비활성화 또는 검색 실패 시: 기존 동작 유지
        min_distance = 0.0
        if sources:
            distances = [s.score for s in sources if s.score is not None]
            if distances:
                min_distance = min(distances)

        return RagRetrievalResult(
            sources=sources,
            failed=failed,
            retriever_used=retriever,
            min_l2_distance=min_distance,
        )

    async def _search_with_milvus_fallback(
        self,
        query: str,
        domain: str,
        req: Optional[ChatRequest] = None,
        request_id: Optional[str] = None,
        top_k: Optional[int] = None,
        model: Optional[str] = None,
    ) -> Tuple[List[ChatSource], bool, RetrieverUsed]:
        """
        Milvus 전용 검색을 수행합니다.

        Phase 48 bugfix: RAGFlow fallback 제거 - Milvus만 사용합니다.
        Milvus 실패 시 503 에러를 반환하고, 결과 0건은 정상 처리됩니다.

        Phase AB: A/B 테스트 지원
        - model 파라미터로 임베딩 모델 직접 선택 (권장)
        - 모델에 따라 적절한 Milvus 클라이언트 선택 (임베딩 + 컬렉션)

        Phase 57: Query Expansion + RRF Fusion
        - 짧은 쿼리는 LLM으로 확장
        - 원문 + 확장 쿼리 2번 검색 후 RRF로 융합

        Args:
            query: 검색 쿼리
            domain: 도메인
            req: ChatRequest (선택)
            request_id: 디버그용 요청 ID
            top_k: 검색 결과 개수
            model: A/B 테스트 모델 ("openai" | "sroberta", 권장)

        Returns:
            Tuple[List[ChatSource], bool, RetrieverUsed]
        """
        settings = self._settings

        # Step 7: top_k 결정 (파라미터 > 설정값)
        effective_top_k = top_k if top_k is not None else settings.CHAT_CONTEXT_MAX_SOURCES

        # Phase AB: A/B 테스트 클라이언트 선택 (방식 B - model 직접 사용)
        # model 파라미터로 직접 클라이언트 선택 (권장)
        milvus_client = get_milvus_client_by_model(model)
        ab_info = get_client_info_by_model(model)

        # 디버그 로그: retrieval_target (Milvus)
        if request_id:
            dbg_retrieval_target(
                request_id=request_id,
                collection=ab_info.get("collection_name", settings.MILVUS_COLLECTION_NAME),
                partition=None,
                filter_expr=None,
                top_k=effective_top_k,
                domain=domain,
            )

        # Phase AB: A/B 테스트 정보 로깅
        if ab_info.get("is_ab_test"):
            logger.info(
                f"[A/B Search] model={model}, "
                f"embedding={ab_info.get('embedding_model')}, "
                f"collection={ab_info.get('collection_name')}"
            )

        try:
            # =================================================================
            # Phase 57: Query Expansion + RRF Fusion 파이프라인
            # =================================================================

            # Step 1: 원문 쿼리 검색
            original_sources = await milvus_client.search_as_sources(
                query=query,
                domain=domain,
                user_role=req.user_role if req else None,
                department=req.department if req else None,
                top_k=effective_top_k * 2,  # RRF용으로 더 많이 가져옴
                request_id=request_id,
            )

            # Phase 45: 원문 검색 similarity 분포 로깅
            log_similarity_distribution(
                sources=original_sources,
                search_stage="milvus_original",
                query_preview=query,
                domain=domain,
            )

            # Step 2: Query Expansion (조건부)
            expanded_sources = []
            rewrite_result: Optional[RewriteResult] = None

            if settings.QUERY_EXPANSION_ENABLED:
                # 동기 버전 쿼리 확장 (규칙 기반, LLM 미사용)
                # LLM 버전은 ChatService에서 호출 가능
                rewrite_result = expand_query_sync(query, domain)

                if rewrite_result.used:
                    # 확장 쿼리로 추가 검색
                    logger.info(
                        f"[QueryExpansion] '{query[:20]}...' → '{rewrite_result.rewritten[:30]}...' "
                        f"(reason={rewrite_result.reason})"
                    )

                    expanded_sources = await milvus_client.search_as_sources(
                        query=rewrite_result.rewritten,
                        domain=domain,
                        user_role=req.user_role if req else None,
                        department=req.department if req else None,
                        top_k=effective_top_k * 2,
                        request_id=request_id,
                    )

                    # 확장 검색 similarity 분포 로깅
                    log_similarity_distribution(
                        sources=expanded_sources,
                        search_stage="milvus_expanded",
                        query_preview=rewrite_result.rewritten,
                        domain=domain,
                    )

            # Step 3: RRF Fusion (조건부)
            rrf_result = None  # Phase 57: 품질 로그용 초기화
            if settings.RAG_FUSION_ENABLED and expanded_sources:
                # RRF로 융합
                rrf_result = rrf_fuse_with_sources(
                    original_results=original_sources,
                    expanded_results=expanded_sources,
                    k=settings.RRF_K_PARAMETER,
                    top_n=effective_top_k * 2,  # truncate 전이므로 여유 있게
                )
                sources = rrf_result.results

                logger.info(
                    f"[RRF Fusion] Applied: original={len(original_sources)}, "
                    f"expanded={len(expanded_sources)} → fused={len(sources)}"
                )
            else:
                # RRF 비활성화 또는 확장 검색 없음 → 원문 결과만 사용
                sources = original_sources

            # =================================================================
            # 기존 로직 유지
            # =================================================================

            # 컨텍스트 길이 제한 적용
            sources = self._truncate_context(sources)

            logger.info(
                f"Milvus search returned {len(sources)} sources (retriever_used=MILVUS)"
            )

            # Phase 57: RAG 품질 로그 (구조화)
            rag_quality_log = build_rag_quality_log(
                request_id=request_id or "",
                domain=domain,
                query=query,
                normalized_query=query,  # 이미 정규화된 쿼리
                original_sources=original_sources,
                expanded_sources=expanded_sources if expanded_sources else None,
                rewrite_result=rewrite_result,
                rrf_result=rrf_result,
                final_sources=sources,
                retriever_used="MILVUS",
                settings=settings,
            )
            log_rag_quality(rag_quality_log)

            # 디버그 로그: retrieval_top5
            if request_id:
                self._log_retrieval_top5(request_id, sources)

            # 결과 0건도 정상 처리 (failed=False)
            return sources, False, "MILVUS"

        except RetrievalBlockedError as e:
            # Phase 50: 2차 가드 - MilvusClient에서 올라온 RetrievalBlockedError 안전 처리
            logger.warning(
                f"RagHandler: RetrievalBlockedError caught from Milvus | reason={e.reason}"
            )
            return [], False, "BLOCKED"

        except MilvusSearchError as e:
            logger.error(f"Milvus search failed: {e}")
            metrics.increment_error(LOG_TAG_RAG_ERROR)
            raise RagSearchUnavailableError(
                f"Milvus 검색 서비스 장애: {e}"
            ) from e

        except Exception as e:
            logger.exception(f"Milvus unexpected error: {e}")
            metrics.increment_error(LOG_TAG_RAG_ERROR)
            raise RagSearchUnavailableError(
                f"Milvus 검색 서비스 장애: {type(e).__name__}"
            ) from e

    async def _search_ragflow_only(
        self,
        query: str,
        domain: str,
        req: ChatRequest,
        request_id: Optional[str] = None,
    ) -> Tuple[List[ChatSource], bool, RetrieverUsed]:
        """
        RAGFlow 검색 (제거됨).

        RAGFlow 클라이언트가 제거되었으므로 항상 에러를 발생시킵니다.
        MILVUS_ENABLED=True로 설정하여 Milvus를 사용하세요.

        Raises:
            RagSearchUnavailableError: 항상 발생
        """
        logger.error(
            "RAGFlow search called but RAGFlow client has been removed. "
            "Please enable Milvus (MILVUS_ENABLED=True)."
        )
        raise RagSearchUnavailableError(
            "RAGFlow 클라이언트가 제거되었습니다. MILVUS_ENABLED=True로 설정하세요."
        )

    def _truncate_context(self, sources: List[ChatSource]) -> List[ChatSource]:
        """
        컨텍스트 길이를 제한합니다.

        CHAT_CONTEXT_MAX_CHARS 설정에 따라 snippet을 truncate합니다.

        Args:
            sources: 검색 결과

        Returns:
            List[ChatSource]: truncate된 검색 결과
        """
        max_chars = self._settings.CHAT_CONTEXT_MAX_CHARS
        max_sources = self._settings.CHAT_CONTEXT_MAX_SOURCES

        # 소스 수 제한
        sources = sources[:max_sources]

        # 전체 컨텍스트 길이 계산 및 truncate
        total_chars = 0
        truncated_sources = []

        for source in sources:
            snippet_len = len(source.snippet) if source.snippet else 0

            if total_chars + snippet_len > max_chars:
                # 남은 공간만큼만 snippet 사용
                remaining = max_chars - total_chars
                if remaining > 100:  # 최소 100자는 포함
                    truncated_snippet = source.snippet[:remaining] + "..."
                    truncated_source = ChatSource(
                        doc_id=source.doc_id,
                        title=source.title,
                        snippet=truncated_snippet,
                        score=source.score,
                        page=source.page,
                        article_label=source.article_label,
                        article_path=source.article_path,
                        source_type=source.source_type,
                    )
                    truncated_sources.append(truncated_source)
                break

            truncated_sources.append(source)
            total_chars += snippet_len

        return truncated_sources

    def _log_retrieval_top5(
        self,
        request_id: str,
        sources: List[ChatSource],
    ) -> None:
        """retrieval_top5 디버그 로그를 출력합니다."""
        top5_results = [
            {
                "doc_title": s.title,
                "chunk_id": s.doc_id,
                "score": s.score,
            }
            for s in sources[:5]
        ]
        dbg_retrieval_top5(request_id=request_id, results=top5_results)
