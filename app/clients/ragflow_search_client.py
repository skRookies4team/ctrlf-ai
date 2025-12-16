"""
RAGFlow Search Client (Phase 19-AI-1)

/v1/chunk/search 엔드포인트를 호출하는 전용 검색 클라이언트입니다.

기존 ragflow_client.py와 분리된 모듈로,
- 에러 시 빈 결과 대신 명확한 예외 발생
- domain → kb_id 매핑 필수 (매핑 없으면 실패)
- 원시 결과 반환 (RagDocument 변환 없음)

사용 방법:
    from app.clients.ragflow_search_client import RagflowSearchClient, RagflowSearchError

    client = RagflowSearchClient()
    try:
        results = await client.search_chunks(
            query="연차휴가 이월 규정",
            dataset="POLICY",
            top_k=5
        )
    except RagflowSearchError as e:
        logger.error(f"Search failed: {e}")
"""

from typing import Any, Dict, List, Optional

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# 예외 클래스
# =============================================================================


class RagflowSearchError(Exception):
    """RAGFlow 검색 실패 예외 (Phase 19)."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.append(f"status={self.status_code}")
        return " | ".join(parts)


class RagflowConfigError(RagflowSearchError):
    """RAGFlow 설정 오류 예외 (매핑 누락 등)."""
    pass


# =============================================================================
# RagflowSearchClient
# =============================================================================


class RagflowSearchClient:
    """
    RAGFlow /v1/chunk/search 전용 검색 클라이언트.

    Phase 19-AI-1 요구사항:
    - 에러 시 빈 결과 대신 명확한 예외 발생
    - domain → kb_id 매핑 필수 (매핑 없으면 RagflowConfigError)
    - 원시 dict 결과 반환 (RagDocument 변환 없음)

    Attributes:
        _base_url: RAGFlow 서비스 기본 URL
        _api_key: RAGFlow API Key
        _client: httpx.AsyncClient 인스턴스
        _timeout: HTTP 요청 타임아웃 (초)
        _kb_mapping: domain → kb_id 매핑 딕셔너리

    Example:
        client = RagflowSearchClient()
        results = await client.search_chunks("연차 규정", "POLICY", top_k=5)
        # results: [{"id": "...", "content": "...", "similarity": 0.9}, ...]
    """

    DEFAULT_TIMEOUT = 5.0  # 5초

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        RagflowSearchClient 초기화.

        Args:
            base_url: RAGFlow 서비스 URL. None이면 settings에서 로드.
            api_key: RAGFlow API Key. None이면 settings에서 로드.
            timeout: HTTP 요청 타임아웃 (초). 기본값 5초.
            client: httpx.AsyncClient 인스턴스. None이면 공용 클라이언트 사용.

        Raises:
            RagflowConfigError: base_url이 설정되지 않은 경우
        """
        settings = get_settings()

        self._base_url = (base_url or settings.ragflow_base_url or "").rstrip("/")
        self._api_key = api_key or settings.RAGFLOW_API_KEY
        self._timeout = timeout
        self._client = client or get_async_http_client()

        # KB_ID 매핑 로드
        self._kb_mapping = self._load_kb_mapping(settings)

        if not self._base_url:
            raise RagflowConfigError(
                "RAGFLOW_BASE_URL is not configured. "
                "Set RAGFLOW_BASE_URL or RAGFLOW_BASE_URL_REAL/MOCK in .env"
            )

        logger.info(
            f"RagflowSearchClient initialized: base_url={self._base_url}, "
            f"timeout={self._timeout}s, kb_mappings={list(self._kb_mapping.keys())}"
        )

    def _load_kb_mapping(self, settings) -> Dict[str, str]:
        """
        settings에서 domain → kb_id 매핑을 로드합니다.

        환경변수에서 로드하는 방법:
        - RAGFLOW_DATASET_MAPPING: "policy:kb_id1,training:kb_id2" 형식
        - RAGFLOW_KB_ID_POLICY, RAGFLOW_KB_ID_TRAINING 등 개별 변수

        Returns:
            Dict[str, str]: domain(대문자) → kb_id 매핑
        """
        mapping: Dict[str, str] = {}

        # 1. RAGFLOW_DATASET_MAPPING에서 로드 (기존 방식)
        if settings.ragflow_dataset_to_kb_mapping:
            for key, value in settings.ragflow_dataset_to_kb_mapping.items():
                mapping[key.upper()] = value

        # 2. 개별 RAGFLOW_KB_ID_* 환경변수에서 로드
        kb_id_vars = {
            "POLICY": getattr(settings, "RAGFLOW_KB_ID_POLICY", None),
            "TRAINING": getattr(settings, "RAGFLOW_KB_ID_TRAINING", None),
            "SECURITY": getattr(settings, "RAGFLOW_KB_ID_SECURITY", None),
            "INCIDENT": getattr(settings, "RAGFLOW_KB_ID_INCIDENT", None),
            "EDUCATION": getattr(settings, "RAGFLOW_KB_ID_EDUCATION", None),
        }

        for domain, kb_id in kb_id_vars.items():
            if kb_id:
                mapping[domain] = kb_id

        return mapping

    def _get_kb_id(self, dataset: str) -> str:
        """
        dataset(domain)을 kb_id로 변환합니다.

        Args:
            dataset: 도메인 이름 (예: "POLICY", "policy", "INCIDENT")

        Returns:
            str: 해당 도메인의 kb_id

        Raises:
            RagflowConfigError: 매핑이 없는 경우 (조용히 넘어가지 않음)
        """
        key = dataset.upper()

        if key not in self._kb_mapping:
            available = list(self._kb_mapping.keys()) if self._kb_mapping else []
            raise RagflowConfigError(
                f"No kb_id mapping for dataset '{dataset}'. "
                f"Available mappings: {available}. "
                f"Set RAGFLOW_DATASET_MAPPING or RAGFLOW_KB_ID_{key} in .env"
            )

        return self._kb_mapping[key]

    def _get_auth_headers(self) -> Dict[str, str]:
        """인증 헤더를 반환합니다."""
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def search_chunks(
        self,
        query: str,
        dataset: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        RAGFlow /v1/chunk/search 엔드포인트를 호출합니다.

        Args:
            query: 검색 쿼리 텍스트
            dataset: 검색할 도메인 (예: "POLICY", "TRAINING")
            top_k: 반환할 최대 결과 수 (기본값: 5)

        Returns:
            List[Dict[str, Any]]: 검색 결과 원시 리스트
                각 항목은 RAGFlow 응답의 results 배열 요소 그대로

        Raises:
            RagflowConfigError: dataset에 대한 kb_id 매핑이 없는 경우
            RagflowSearchError: HTTP 요청 실패 (4xx/5xx)
            RagflowSearchError: 타임아웃
            RagflowSearchError: 네트워크 오류

        Example:
            results = await client.search_chunks("연차 규정", "POLICY", top_k=5)
            # [
            #     {"id": "chunk-001", "content": "...", "similarity": 0.92},
            #     {"id": "chunk-002", "content": "...", "similarity": 0.88},
            #     ...
            # ]
        """
        # 1. dataset → kb_id 변환 (매핑 없으면 예외)
        kb_id = self._get_kb_id(dataset)

        # 2. 요청 구성
        url = f"{self._base_url}/v1/chunk/search"
        payload: Dict[str, Any] = {
            "query": query,
            "dataset": kb_id,  # kb_id를 dataset으로 전달
            "top_k": top_k,
        }

        logger.info(
            f"RagflowSearchClient.search_chunks: query='{query[:50]}...', "
            f"dataset={dataset} (kb_id={kb_id}), top_k={top_k}"
        )

        # 3. HTTP 요청
        try:
            response = await self._client.post(
                url,
                headers=self._get_auth_headers(),
                json=payload,
                timeout=self._timeout,
            )

            # 4xx/5xx 에러 처리
            if response.status_code >= 400:
                response_text = response.text[:500] if response.text else ""
                logger.error(
                    f"RAGFlow search HTTP error: status={response.status_code}, "
                    f"body={response_text}"
                )
                raise RagflowSearchError(
                    f"RAGFlow search failed: HTTP {response.status_code}",
                    status_code=response.status_code,
                    response_body=response_text,
                )

            # 응답 파싱
            data = response.json()

            # data["data"]["results"] 추출
            results = data.get("data", {}).get("results", [])

            logger.info(
                f"RAGFlow search returned {len(results)} results "
                f"for query='{query[:30]}...'"
            )

            return results

        except httpx.TimeoutException as e:
            logger.error(f"RAGFlow search timeout after {self._timeout}s")
            raise RagflowSearchError(
                f"RAGFlow search timeout after {self._timeout}s",
            ) from e

        except httpx.RequestError as e:
            logger.error(f"RAGFlow search request error: {e}")
            raise RagflowSearchError(
                f"RAGFlow search request error: {type(e).__name__}: {str(e)}",
            ) from e

        except RagflowSearchError:
            # 이미 래핑된 예외는 그대로 전달
            raise

        except Exception as e:
            logger.exception("RAGFlow search unexpected error")
            raise RagflowSearchError(
                f"RAGFlow search unexpected error: {type(e).__name__}: {str(e)}",
            ) from e

    async def health_check(self) -> bool:
        """
        RAGFlow 서비스 상태를 확인합니다.

        Returns:
            bool: 서비스 정상이면 True
        """
        try:
            url = f"{self._base_url}/health"
            response = await self._client.get(url, timeout=self._timeout)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"RAGFlow health check failed: {e}")
            return False
