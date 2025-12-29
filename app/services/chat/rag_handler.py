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
- 검색 결과의 similarity 점수 분포 로깅 (min/max/avg)
- 0건 결과 시 원인 분석을 위한 상세 로깅

Phase 48: Low-relevance Gate
- max_score < RAG_MIN_MAX_SCORE → sources=[] 강등
- 앵커 키워드가 sources 텍스트에 없으면 → sources=[] 강등
- 저관련 검색 결과로 '근거 있는 척' 하는 현상 방지
"""

import re
from typing import List, Literal, Optional, Tuple

from app.clients.ragflow_client import RagflowClient


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
from app.core.config import get_settings
from app.core.exceptions import UpstreamServiceError
from app.core.logging import get_logger
from app.core.metrics import (
    LOG_TAG_RAG_ERROR,
    metrics,
)
from app.models.chat import ChatRequest, ChatSource
from app.utils.debug_log import dbg_final_query, dbg_retrieval_top5, dbg_retrieval_target

logger = get_logger(__name__)

# retriever_used 타입 정의
RetrieverUsed = Literal["MILVUS", "RAGFLOW", "RAGFLOW_FALLBACK"]

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
    Phase 45: 검색 결과의 similarity 분포를 로깅합니다.

    디버깅/진단용으로, 검색 결과가 0건일 때 원인 분석에 유용합니다.
    - 검색 결과 수, min/max/avg similarity 점수 로깅
    - 점수 구간별 분포 로깅 (0.9+, 0.7-0.9, 0.5-0.7, <0.5)

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
            f"[Similarity] {search_stage}: 0 results | "
            f"domain={domain} | query='{query_safe}'"
        )
        return

    scores = [s.score for s in sources if s.score is not None]
    if not scores:
        logger.info(
            f"[Similarity] {search_stage}: {len(sources)} results (no scores) | "
            f"domain={domain}"
        )
        return

    min_score = min(scores)
    max_score = max(scores)
    avg_score = sum(scores) / len(scores)

    # 점수 구간별 분포
    high = sum(1 for s in scores if s >= 0.9)
    mid_high = sum(1 for s in scores if 0.7 <= s < 0.9)
    mid_low = sum(1 for s in scores if 0.5 <= s < 0.7)
    low = sum(1 for s in scores if s < 0.5)

    logger.info(
        f"[Similarity] {search_stage}: {len(sources)} results | "
        f"min={min_score:.3f}, max={max_score:.3f}, avg={avg_score:.3f} | "
        f"distribution: [>=0.9:{high}, 0.7-0.9:{mid_high}, 0.5-0.7:{mid_low}, <0.5:{low}] | "
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
    Phase 48/50: 저관련 검색 결과를 필터링합니다.

    Phase 50 개선:
    - score_gate: max_score < threshold 시에도 최소 1개는 유지 (soft gate)
    - anchor_gate: 미매칭 시에도 최소 ANCHOR_GATE_MIN_KEEP개는 유지

    두 가지 게이트를 적용:
    A. score soft 게이트: max_score < RAG_MIN_MAX_SCORE → 경고만, 최소 1개 유지
    B. 앵커 키워드 soft 게이트: 핵심어 미매칭 → 경고만, 최소 1개 유지

    Args:
        sources: RAG 검색 결과
        query: 원본 쿼리
        domain: 검색 도메인

    Returns:
        Tuple[List[ChatSource], Optional[str]]:
            - 필터링된 sources (최소 1개 보장)
            - gate_reason (soft 강등 시 사유, 통과 시 None)
    """
    settings = get_settings()

    if not sources:
        return sources, None

    # score 계산
    scores = [s.score for s in sources if s.score is not None]
    if not scores:
        # score가 없는 경우 → 통과 (RAGFlow 등에서 score 없이 반환하는 경우)
        return sources, None

    max_score = max(scores)
    avg_score = sum(scores) / len(scores)
    min_threshold = settings.RAG_MIN_MAX_SCORE

    # Phase 50: 안전장치 - 최소 유지 개수
    min_keep = ANCHOR_GATE_MIN_KEEP

    # Gate A: score soft 게이트
    if max_score < min_threshold:
        # Phase 50: 완전 drop 대신 최소 min_keep개 유지
        kept_sources = sources[:min_keep]
        query_safe = ascii_safe_preview(query, 50)
        logger.warning(
            f"[LowRelevanceGate] SOFT_DEMOTE by score_gate | "
            f"max_score={max_score:.3f} < threshold={min_threshold} | "
            f"query='{query_safe}' | domain={domain} | "
            f"avg_score={avg_score:.3f} | top_k={len(sources)} | "
            f"kept_count={len(kept_sources)} (min_keep={min_keep})"
        )
        return kept_sources, "max_score_below_threshold_soft"

    # Gate B: 앵커 키워드 soft 게이트
    anchor_keywords = extract_anchor_keywords(query)
    has_anchor_match = check_anchor_keywords_in_sources(anchor_keywords, sources)

    if not has_anchor_match:
        # Phase 50: 완전 drop 대신 최소 min_keep개 유지
        kept_sources = sources[:min_keep]
        query_safe = ascii_safe_preview(query, 50)
        keywords_safe = {ascii_safe_preview(kw, 20) for kw in anchor_keywords}
        logger.warning(
            f"[LowRelevanceGate] SOFT_DEMOTE by anchor_gate | "
            f"anchor_keywords={keywords_safe} not found in sources | "
            f"query='{query_safe}' | domain={domain} | "
            f"max_score={max_score:.3f} | avg_score={avg_score:.3f} | "
            f"top_k={len(sources)} | kept_count={len(kept_sources)} (min_keep={min_keep})"
        )
        return kept_sources, "no_anchor_term_match_soft"

    # 통과
    query_safe = ascii_safe_preview(query, 30)
    logger.info(
        f"[LowRelevanceGate] PASSED | "
        f"max_score={max_score:.3f} >= threshold={min_threshold} | "
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
        _ragflow: RAGFlow 검색 클라이언트
        _milvus: Milvus 검색 클라이언트 (Optional)
        _use_milvus: Milvus 사용 여부
    """

    def __init__(
        self,
        ragflow_client: RagflowClient,
        milvus_client: Optional[MilvusSearchClient] = None,
    ) -> None:
        """
        RagHandler 초기화.

        Args:
            ragflow_client: RAGFlow 검색 클라이언트
            milvus_client: Milvus 검색 클라이언트 (선택, None이면 자동 생성)
        """
        self._ragflow = ragflow_client
        self._settings = get_settings()

        # Milvus 클라이언트 초기화 (CHAT_RETRIEVER_BACKEND=milvus 시)
        self._use_milvus = (
            self._settings.chat_retriever_backend == "milvus"
            and self._settings.MILVUS_ENABLED
        )

        if self._use_milvus:
            self._milvus = milvus_client or get_milvus_client()
            logger.info(
                f"RagHandler initialized with Milvus (CHAT_RETRIEVER_BACKEND={self._settings.chat_retriever_backend})"
            )
        else:
            self._milvus = None
            logger.info(
                f"RagHandler initialized with RAGFlow only (CHAT_RETRIEVER_BACKEND={self._settings.chat_retriever_backend})"
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
        req: ChatRequest,
        request_id: Optional[str] = None,
    ) -> Tuple[List[ChatSource], bool, RetrieverUsed]:
        """
        RAG 검색을 수행하고 실패 여부와 사용된 retriever를 함께 반환합니다.

        Option 3 통합:
        - CHAT_RETRIEVER_BACKEND=milvus: Milvus 먼저 시도 → 실패/empty 시 RAGFlow fallback
        - CHAT_RETRIEVER_BACKEND=ragflow: RAGFlow만 사용

        Phase 44: 2nd-chance retrieval & Query Normalization
        - 검색 전 쿼리 정규화 (마스킹 토큰 제거)
        - 1차 검색 결과 0건 → top_k 올려서 재시도 (5 → 15)

        Args:
            query: 검색 쿼리 (마스킹된 상태)
            domain: 도메인
            req: 원본 요청
            request_id: 디버그용 요청 ID

        Returns:
            Tuple[List[ChatSource], bool, RetrieverUsed]:
                - 검색 결과
                - 실패 여부 (0건도 정상=False)
                - 사용된 retriever ("MILVUS", "RAGFLOW", "RAGFLOW_FALLBACK")

        Raises:
            RagSearchUnavailableError: 모든 검색 서비스 장애 시 (503 반환용)
        """
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

        return sources, failed, retriever

    async def _search_with_milvus_fallback(
        self,
        query: str,
        domain: str,
        req: ChatRequest,
        request_id: Optional[str] = None,
    ) -> Tuple[List[ChatSource], bool, RetrieverUsed]:
        """
        Milvus 검색을 시도하고 실패 시 RAGFlow로 fallback합니다.

        Returns:
            Tuple[List[ChatSource], bool, RetrieverUsed]
        """
        settings = self._settings

        # 디버그 로그: retrieval_target (Milvus)
        if request_id:
            dbg_retrieval_target(
                request_id=request_id,
                collection=settings.MILVUS_COLLECTION_NAME,
                partition=None,
                filter_expr=None,
                top_k=settings.CHAT_CONTEXT_MAX_SOURCES,
                domain=domain,
            )

        try:
            # Milvus 검색
            sources = await self._milvus.search_as_sources(
                query=query,
                domain=domain,
                user_role=req.user_role,
                department=req.department,
                top_k=settings.CHAT_CONTEXT_MAX_SOURCES,
                request_id=request_id,
            )

            # Phase 45: Milvus 검색 similarity 분포 로깅
            log_similarity_distribution(
                sources=sources,
                search_stage="milvus_search",
                query_preview=query,
                domain=domain,
            )

            # 결과가 있으면 Milvus 사용 성공
            if sources:
                # 컨텍스트 길이 제한 적용
                sources = self._truncate_context(sources)

                logger.info(
                    f"Milvus search returned {len(sources)} sources (retriever_used=MILVUS)"
                )

                # 디버그 로그: retrieval_top5
                if request_id:
                    self._log_retrieval_top5(request_id, sources)

                return sources, False, "MILVUS"

            # Milvus 결과 0건 → RAGFlow fallback
            logger.warning(
                f"Milvus search returned 0 results, falling back to RAGFlow"
            )

        except MilvusSearchError as e:
            logger.error(f"Milvus search failed: {e}, falling back to RAGFlow")
        except Exception as e:
            logger.error(f"Milvus unexpected error: {e}, falling back to RAGFlow")

        # RAGFlow fallback
        try:
            # Phase 44: 1차 검색 (top_k=5)
            sources = await self._ragflow.search_as_sources(
                query=query,
                domain=domain,
                user_role=req.user_role,
                department=req.department,
                top_k=DEFAULT_TOP_K,
            )

            logger.info(f"RAGFlow fallback 1st search returned {len(sources)} sources (top_k={DEFAULT_TOP_K})")

            # Phase 45: 1차 검색 similarity 분포 로깅
            log_similarity_distribution(
                sources=sources,
                search_stage="ragflow_fallback_1st",
                query_preview=query,
                domain=domain,
            )

            # Phase 44: 2nd-chance retrieval - 0건이면 top_k 올려서 재시도
            if not sources:
                logger.info(
                    f"2nd-chance retrieval: 0 results, retrying with top_k={RETRY_TOP_K}"
                )
                sources = await self._ragflow.search_as_sources(
                    query=query,
                    domain=domain,
                    user_role=req.user_role,
                    department=req.department,
                    top_k=RETRY_TOP_K,
                )
                logger.info(
                    f"RAGFlow fallback 2nd-chance search returned {len(sources)} sources (top_k={RETRY_TOP_K})"
                )

                # Phase 45: 2nd-chance 검색 similarity 분포 로깅
                log_similarity_distribution(
                    sources=sources,
                    search_stage="ragflow_fallback_2nd",
                    query_preview=query,
                    domain=domain,
                )

            # 컨텍스트 길이 제한 적용
            sources = self._truncate_context(sources)

            logger.info(
                f"RAGFlow fallback returned {len(sources)} sources (retriever_used=RAGFLOW_FALLBACK)"
            )

            # 디버그 로그: retrieval_top5
            if request_id:
                self._log_retrieval_top5(request_id, sources)

            return sources, False, "RAGFLOW_FALLBACK"

        except UpstreamServiceError as e:
            logger.error(f"RAGFlow fallback also failed: {e}")
            metrics.increment_error(LOG_TAG_RAG_ERROR)
            raise RagSearchUnavailableError(
                f"RAG 검색 서비스 장애 (Milvus + RAGFlow 모두 실패)"
            ) from e

        except Exception as e:
            logger.exception(f"RAGFlow fallback failed: {e}")
            metrics.increment_error(LOG_TAG_RAG_ERROR)
            raise RagSearchUnavailableError(
                f"RAG 검색 서비스 장애: {type(e).__name__}"
            ) from e

    async def _search_ragflow_only(
        self,
        query: str,
        domain: str,
        req: ChatRequest,
        request_id: Optional[str] = None,
    ) -> Tuple[List[ChatSource], bool, RetrieverUsed]:
        """
        RAGFlow만 사용하여 검색합니다.

        Phase 44: 2nd-chance retrieval 적용
        Phase 45: Similarity 분포 로깅

        Returns:
            Tuple[List[ChatSource], bool, RetrieverUsed]
        """
        settings = self._settings

        try:
            # Phase 44: 1차 검색 (top_k=5)
            sources = await self._ragflow.search_as_sources(
                query=query,
                domain=domain,
                user_role=req.user_role,
                department=req.department,
                top_k=DEFAULT_TOP_K,
            )

            logger.info(f"RAGFlow 1st search returned {len(sources)} sources (top_k={DEFAULT_TOP_K})")

            # Phase 45: 1차 검색 similarity 분포 로깅
            log_similarity_distribution(
                sources=sources,
                search_stage="1st_search",
                query_preview=query,
                domain=domain,
            )

            # Phase 44: 2nd-chance retrieval - 0건이면 top_k 올려서 재시도
            if not sources:
                logger.info(
                    f"2nd-chance retrieval: 0 results, retrying with top_k={RETRY_TOP_K}"
                )
                sources = await self._ragflow.search_as_sources(
                    query=query,
                    domain=domain,
                    user_role=req.user_role,
                    department=req.department,
                    top_k=RETRY_TOP_K,
                )
                logger.info(
                    f"RAGFlow 2nd-chance search returned {len(sources)} sources (top_k={RETRY_TOP_K})"
                )

                # Phase 45: 2nd-chance 검색 similarity 분포 로깅
                log_similarity_distribution(
                    sources=sources,
                    search_stage="2nd_chance",
                    query_preview=query,
                    domain=domain,
                )

                if not sources:
                    logger.warning(
                        f"RAGFlow 2nd-chance also returned no results for query: {query[:50]}..."
                    )

            # 컨텍스트 길이 제한 적용
            sources = self._truncate_context(sources)

            logger.info(
                f"RAGFlow search returned {len(sources)} sources (retriever_used=RAGFLOW)"
            )

            # 디버그 로그: retrieval_top5
            if request_id:
                self._log_retrieval_top5(request_id, sources)

            return sources, False, "RAGFLOW"

        except UpstreamServiceError as e:
            logger.error(f"RAGFlow search failed with UpstreamServiceError: {e}")
            metrics.increment_error(LOG_TAG_RAG_ERROR)
            raise RagSearchUnavailableError(
                f"RAG 검색 서비스 장애: {e.message}"
            ) from e

        except Exception as e:
            logger.exception(f"RAGFlow search failed: {e}")
            metrics.increment_error(LOG_TAG_RAG_ERROR)
            raise RagSearchUnavailableError(
                f"RAG 검색 서비스 장애: {type(e).__name__}"
            ) from e

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
