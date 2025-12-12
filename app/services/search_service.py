"""
검색 서비스 모듈 (Phase 18)

AI Gateway 표준 RAG 검색 API의 비즈니스 로직을 처리합니다.
RAGFlow의 /v1/chunk/search 엔드포인트와 연동하여 문서를 검색합니다.

주요 기능:
- dataset 슬러그 → kb_id 변환
- RAGFlow /v1/chunk/search 호출
- 응답 정규화 및 에러 처리

사용 예시:
    from app.services.search_service import SearchService

    service = SearchService()
    response = await service.search(request)
"""

from typing import List, Optional

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.search import SearchRequest, SearchResponse, SearchResultItem

logger = get_logger(__name__)


class DatasetNotFoundError(Exception):
    """데이터셋 슬러그가 매핑에 없을 때 발생하는 예외."""

    def __init__(self, dataset: str, available_datasets: List[str]):
        self.dataset = dataset
        self.available_datasets = available_datasets
        super().__init__(
            f"Dataset '{dataset}' not found. Available: {', '.join(available_datasets)}"
        )


class SearchService:
    """
    RAG 검색 서비스

    AI Gateway 표준 검색 API를 처리하고, RAGFlow와 연동합니다.

    Attributes:
        _base_url: RAGFlow 서비스 기본 URL
        _timeout: HTTP 요청 타임아웃 (초)
        _dataset_mapping: dataset 슬러그 → kb_id 매핑
        _client: httpx.AsyncClient 인스턴스

    Example:
        service = SearchService()
        response = await service.search(SearchRequest(
            query="연차휴가 규정",
            top_k=5,
            dataset="policy"
        ))
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        dataset_mapping: Optional[dict[str, str]] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        SearchService 초기화

        Args:
            base_url: RAGFlow 서비스 URL. None이면 settings에서 로드.
            timeout: HTTP 요청 타임아웃 (초). None이면 settings에서 로드.
            dataset_mapping: dataset → kb_id 매핑. None이면 settings에서 로드.
            client: httpx.AsyncClient. None이면 공용 클라이언트 사용.
        """
        settings = get_settings()

        self._base_url = base_url or settings.ragflow_base_url
        self._timeout = timeout or settings.RAGFLOW_TIMEOUT_SEC
        self._dataset_mapping = dataset_mapping or settings.ragflow_dataset_to_kb_mapping
        self._client = client or get_async_http_client()

        if not self._base_url:
            logger.warning(
                "RAGFlow URL is not configured. "
                "Search API calls will return empty results."
            )

    def get_kb_id(self, dataset: str) -> str:
        """
        dataset 슬러그를 kb_id로 변환합니다.

        Args:
            dataset: 데이터셋 슬러그 (예: "policy", "training")

        Returns:
            str: 해당 슬러그의 kb_id

        Raises:
            DatasetNotFoundError: 매핑에 없는 슬러그인 경우
        """
        dataset_lower = dataset.lower().strip()

        if dataset_lower not in self._dataset_mapping:
            raise DatasetNotFoundError(
                dataset=dataset,
                available_datasets=list(self._dataset_mapping.keys()),
            )

        return self._dataset_mapping[dataset_lower]

    async def search(self, request: SearchRequest) -> SearchResponse:
        """
        RAGFlow에서 문서를 검색합니다.

        Args:
            request: 검색 요청 DTO

        Returns:
            SearchResponse: 검색 결과

        Note:
            - RAGFlow 미설정 시 빈 결과 반환
            - HTTP 에러 발생 시 빈 결과 반환하고 로그 기록
        """
        # RAGFlow 미설정 시 빈 결과 반환
        if not self._base_url:
            logger.warning("RAGFlow search skipped: base_url not configured")
            return SearchResponse(results=[])

        # dataset → kb_id 변환
        kb_id = self.get_kb_id(request.dataset)

        # RAGFlow /v1/chunk/search 호출
        url = f"{self._base_url}/v1/chunk/search"
        payload = {
            "query": request.query,
            "top_k": request.top_k,
            "dataset": kb_id,  # 실제 kb_id 전송
        }

        logger.info(
            f"Searching RAGFlow: query='{request.query[:50]}...', "
            f"dataset={request.dataset} (kb_id={kb_id}), top_k={request.top_k}"
        )

        try:
            response = await self._client.post(
                url,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()

            # 응답 파싱
            results = self._parse_results(data, request.dataset)
            logger.info(f"RAGFlow search returned {len(results)} results")

            return SearchResponse(results=results)

        except httpx.TimeoutException:
            logger.error(f"RAGFlow search timeout after {self._timeout}s")
            return SearchResponse(results=[])

        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAGFlow search HTTP error: status={e.response.status_code}, "
                f"detail={e.response.text[:200] if e.response.text else 'N/A'}"
            )
            return SearchResponse(results=[])

        except httpx.RequestError as e:
            logger.error(f"RAGFlow search request error: {e}")
            return SearchResponse(results=[])

        except Exception as e:
            logger.exception("RAGFlow search unexpected error")
            return SearchResponse(results=[])

    def _parse_results(
        self, data: dict, dataset_slug: str
    ) -> List[SearchResultItem]:
        """
        RAGFlow 응답을 SearchResultItem 리스트로 변환합니다.

        Args:
            data: RAGFlow JSON 응답
            dataset_slug: 요청에 사용된 dataset 슬러그

        Returns:
            List[SearchResultItem]: 파싱된 결과 리스트
        """
        results: List[SearchResultItem] = []
        items = data.get("results", [])

        for item in items:
            try:
                result = SearchResultItem(
                    doc_id=item.get("doc_id") or item.get("chunk_id", "unknown"),
                    title=item.get("title") or item.get("doc_name", "Untitled"),
                    page=item.get("page") or item.get("page_num"),
                    score=item.get("score") or item.get("similarity", 0.0),
                    snippet=item.get("snippet") or item.get("content"),
                    dataset=dataset_slug,
                    source="ragflow",
                )
                results.append(result)
            except Exception as e:
                logger.warning(f"Failed to parse search result item: {e}")
                continue

        return results
