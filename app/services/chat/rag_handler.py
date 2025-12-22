"""
RAG 검색 핸들러 (RAG Search Handler)

ChatService에서 사용하는 RAG 검색 로직을 담당합니다.

Phase 2 리팩토링:
- ChatService._perform_rag_search → RagHandler.perform_search
- ChatService._perform_rag_search_with_fallback → RagHandler.perform_search_with_fallback

지원 백엔드:
- RAGFlow: 기본 RAG 검색 클라이언트
- Milvus: MILVUS_ENABLED=True 시 벡터 검색 클라이언트 (Phase 24)
"""

from typing import List, Optional, Tuple

from app.clients.milvus_client import MilvusSearchClient
from app.clients.ragflow_client import RagflowClient
from app.core.exceptions import UpstreamServiceError
from app.core.logging import get_logger
from app.core.metrics import (
    LOG_TAG_RAG_ERROR,
    LOG_TAG_RAG_FALLBACK,
    metrics,
)
from app.models.chat import ChatRequest, ChatSource
from app.utils.debug_log import dbg_final_query, dbg_retrieval_top5

logger = get_logger(__name__)


class RagHandler:
    """
    RAG 검색을 처리하는 핸들러 클래스.

    RAGFlow 또는 Milvus 클라이언트를 사용하여 검색을 수행합니다.
    Phase 24에서 Milvus 지원이 추가되었습니다.

    Attributes:
        _ragflow: RAGFlow 검색 클라이언트
        _milvus: Milvus 검색 클라이언트 (선택적)
        _milvus_enabled: Milvus 사용 여부
    """

    def __init__(
        self,
        ragflow_client: RagflowClient,
        milvus_client: Optional[MilvusSearchClient] = None,
        milvus_enabled: bool = False,
    ) -> None:
        """
        RagHandler 초기화.

        Args:
            ragflow_client: RAGFlow 검색 클라이언트
            milvus_client: Milvus 검색 클라이언트 (선택적)
            milvus_enabled: Milvus 사용 여부
        """
        self._ragflow = ragflow_client
        self._milvus = milvus_client
        self._milvus_enabled = milvus_enabled

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

    async def perform_search_with_fallback(
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
                logger.info("Using Milvus for vector search (Phase 24)")
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

            search_backend = (
                "Milvus" if (self._milvus_enabled and self._milvus) else "RAGFlow"
            )
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
