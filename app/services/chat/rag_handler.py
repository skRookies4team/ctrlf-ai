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
"""

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
        # 디버그 로그: final_query
        if request_id:
            dbg_final_query(
                request_id=request_id,
                original_query=query,
                rewritten_query=None,
                keywords=None,
            )

        try:
            # RAGFlow 검색 (단일 경로)
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
