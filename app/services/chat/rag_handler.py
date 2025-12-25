"""
RAG 검색 핸들러 (RAG Search Handler)

ChatService에서 사용하는 RAG 검색 로직을 담당합니다.

Phase 2 리팩토링:
- ChatService._perform_rag_search → RagHandler.perform_search
- ChatService._perform_rag_search_with_fallback → RagHandler.perform_search_with_fallback

A안 확정 (Phase 42):
- RAGFlow 단일 검색 엔진으로 확정
- Milvus 직접 검색 분기 제거
- RAGFlow 장애 시 503 반환 (fallback 없음)

Phase 44: 2nd-chance retrieval & Query Normalization
- 1차 검색 결과 0건 시 top_k 올려서 재시도 (5 → 15)
- 검색 전 마스킹 토큰 제거 ([PERSON], [PHONE] 등)
- 과도한 공백/특수문자 정규화

Phase 45: Similarity 분포 로깅 (디버깅/진단용)
- 검색 결과의 similarity 점수 분포 로깅 (min/max/avg)
- 0건 결과 시 원인 분석을 위한 상세 로깅
"""

import re
from typing import List, Optional, Tuple

from app.clients.ragflow_client import RagflowClient
from app.core.exceptions import UpstreamServiceError
from app.core.logging import get_logger
from app.core.metrics import (
    LOG_TAG_RAG_ERROR,
    metrics,
)
from app.models.chat import ChatRequest, ChatSource
from app.utils.debug_log import dbg_final_query, dbg_retrieval_top5

logger = get_logger(__name__)

# Phase 44: 검색 설정 상수
DEFAULT_TOP_K = 5
RETRY_TOP_K = 15  # 2nd-chance retrieval에서 사용할 top_k

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
        logger.info(
            f"[Similarity] {search_stage}: 0 results | "
            f"domain={domain} | query='{query_preview[:50]}...'"
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


class RagSearchUnavailableError(Exception):
    """RAGFlow 검색 서비스 사용 불가 예외.

    A안 확정에 따라 RAGFlow 장애 시 503 반환을 위한 예외.
    """
    def __init__(self, message: str = "RAG 검색 서비스를 사용할 수 없습니다."):
        self.message = message
        super().__init__(self.message)


class RagHandler:
    """
    RAG 검색을 처리하는 핸들러 클래스.

    A안 확정 (Phase 42):
    - RAGFlow만 사용하여 검색을 수행합니다.
    - RAGFlow 장애 시 RagSearchUnavailableError를 발생시킵니다.

    Attributes:
        _ragflow: RAGFlow 검색 클라이언트
    """

    def __init__(
        self,
        ragflow_client: RagflowClient,
    ) -> None:
        """
        RagHandler 초기화.

        Args:
            ragflow_client: RAGFlow 검색 클라이언트
        """
        self._ragflow = ragflow_client

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
            RagSearchUnavailableError: RAGFlow 장애 시
        """
        try:
            sources = await self._ragflow.search_as_sources(
                query=query,
                domain=domain,
                user_role=req.user_role,
                department=req.department,
                top_k=5,
            )
            logger.info(f"RAGFlow search returned {len(sources)} sources")

            if not sources:
                logger.warning(
                    f"RAGFlow search returned no results for query: {query[:50]}..."
                )

            return sources

        except Exception as e:
            logger.exception(f"RAGFlow search failed: {e}")
            metrics.increment_error(LOG_TAG_RAG_ERROR)
            raise RagSearchUnavailableError(
                f"RAG 검색 서비스 장애: {type(e).__name__}"
            ) from e

    async def perform_search_with_fallback(
        self,
        query: str,
        domain: str,
        req: ChatRequest,
        request_id: Optional[str] = None,
    ) -> Tuple[List[ChatSource], bool]:
        """
        RAG 검색을 수행하고 실패 여부를 함께 반환합니다.

        A안 확정 (Phase 42):
        - RAGFlow만 사용 (Milvus 분기 제거)
        - RAGFlow 장애 시 RagSearchUnavailableError 발생 (fallback 없음)

        Phase 44: 2nd-chance retrieval & Query Normalization
        - 검색 전 쿼리 정규화 (마스킹 토큰 제거)
        - 1차 검색 결과 0건 → top_k 올려서 재시도 (5 → 15)

        Args:
            query: 검색 쿼리 (마스킹된 상태)
            domain: 도메인
            req: 원본 요청
            request_id: 디버그용 요청 ID

        Returns:
            Tuple[List[ChatSource], bool]: (검색 결과, 실패 여부)
            - 실패 여부: 0건도 정상(False), 예외 발생 시에만 RagSearchUnavailableError raise

        Raises:
            RagSearchUnavailableError: RAGFlow 장애 시 (503 반환용)
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

        try:
            # Phase 44: 1차 검색 (top_k=5)
            sources = await self._ragflow.search_as_sources(
                query=normalized_query,
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
                query_preview=normalized_query,
                domain=domain,
            )

            # Phase 44: 2nd-chance retrieval - 0건이면 top_k 올려서 재시도
            if not sources:
                logger.info(
                    f"2nd-chance retrieval: 0 results, retrying with top_k={RETRY_TOP_K}"
                )
                sources = await self._ragflow.search_as_sources(
                    query=normalized_query,
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
                    query_preview=normalized_query,
                    domain=domain,
                )

                if not sources:
                    logger.warning(
                        f"RAGFlow 2nd-chance also returned no results for query: {normalized_query[:50]}..."
                    )

            # 디버그 로그: retrieval_top5
            if request_id:
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
            # A안: RAGFlow 장애 시 503 반환 (fallback 없음)
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
