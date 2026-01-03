"""
RAGFlow 문서 처리 클라이언트 (Phase 51)

SourceSetOrchestrator에서 사용하는 RAGFlow 문서 처리 API 클라이언트입니다.
Mock RAGFlow 서버 또는 실제 RAGFlow 서버와 통신합니다.

사용처:
- SourceSetOrchestrator._process_document() 에서 사용

API:
- POST /api/v1/datasets/{dataset_id}/documents: 문서 업로드
- POST /api/v1/datasets/{dataset_id}/documents/{doc_id}/run: 파싱 트리거
- GET /api/v1/datasets/{dataset_id}/documents/{doc_id}: 문서 상태 조회
- GET /api/v1/datasets/{dataset_id}/documents/{doc_id}/chunks: 청크 조회
"""

from typing import Any, Dict, List, Optional

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Constants
# =============================================================================

DEFAULT_TIMEOUT = 30.0  # RAGFlow 호출 타임아웃 (초)

# 도메인 → RAGFlow dataset_id 매핑
DOMAIN_TO_DATASET_ID: Dict[str, str] = {
    "POLICY": "policy-dataset",
    "EDUCATION": "education-dataset",
    "HR": "hr-dataset",
    "GENERAL": "general-dataset",
}


# =============================================================================
# Exceptions
# =============================================================================


class RagflowError(Exception):
    """RAGFlow 클라이언트 기본 예외."""

    def __init__(self, message: str, error_code: str = "RAGFLOW_ERROR"):
        self.message = message
        self.error_code = error_code
        super().__init__(f"{error_code}: {message}")


class RagflowConnectionError(RagflowError):
    """RAGFlow 연결 실패 예외."""

    def __init__(self, message: str):
        super().__init__(message, "RAGFLOW_CONNECTION_ERROR")


class RagflowDocumentError(RagflowError):
    """RAGFlow 문서 처리 실패 예외."""

    def __init__(self, message: str):
        super().__init__(message, "RAGFLOW_DOCUMENT_ERROR")


# =============================================================================
# RAGFlow Document Processing Client
# =============================================================================


class RagflowClient:
    """
    RAGFlow 문서 처리 HTTP 클라이언트.

    SourceSetOrchestrator에서 사용하는 문서 업로드/파싱/청크 조회 API를 제공합니다.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        RagflowClient 초기화.

        Args:
            base_url: RAGFlow 서비스 URL. None이면 설정에서 가져옴.
            api_key: RAGFlow API 키. None이면 설정에서 가져옴.
            timeout: HTTP 요청 타임아웃 (초).
            client: 커스텀 httpx 클라이언트 (테스트용)
        """
        settings = get_settings()
        self._base_url = (base_url or settings.ragflow_base_url or "").rstrip("/")
        self._api_key = api_key or settings.RAGFLOW_API_KEY
        self._timeout = timeout
        self._external_client = client

        if self._base_url:
            logger.info(f"RagflowClient initialized: base_url={self._base_url}")
        else:
            logger.warning("RagflowClient: RAGFLOW_BASE_URL not configured")

    @property
    def is_configured(self) -> bool:
        """RAGFlow URL이 설정되었는지 확인."""
        return bool(self._base_url)

    def _get_headers(self) -> Dict[str, str]:
        """API 요청 헤더 반환."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _dataset_to_kb_id(self, domain: Optional[str]) -> Optional[str]:
        """도메인을 RAGFlow dataset_id로 변환합니다.

        Args:
            domain: 도메인 (예: "POLICY", "EDUCATION")

        Returns:
            dataset_id 또는 None (매핑 없음)
        """
        if not domain:
            return None
        return DOMAIN_TO_DATASET_ID.get(domain.upper())

    async def upload_document(
        self,
        dataset_id: str,
        file_url: str,
        file_name: str,
    ) -> Dict[str, Any]:
        """
        RAGFlow에 문서를 업로드합니다.

        POST /api/v1/datasets/{dataset_id}/documents

        Args:
            dataset_id: RAGFlow dataset ID
            file_url: 문서 파일 URL (S3 등)
            file_name: 파일명

        Returns:
            dict: {"id": doc_id, "name": file_name, ...}

        Raises:
            RagflowConnectionError: 연결 실패
            RagflowDocumentError: 업로드 실패
        """
        if not self._base_url:
            raise RagflowConnectionError("RAGFLOW_BASE_URL not configured")

        url = f"{self._base_url}/api/v1/datasets/{dataset_id}/documents"
        headers = self._get_headers()

        # form-data로 전송
        data = {
            "file_url": file_url,
            "file_name": file_name,
        }

        logger.info(
            f"Uploading document to RAGFlow: dataset_id={dataset_id}, "
            f"file_name={file_name}"
        )

        try:
            if self._external_client:
                response = await self._external_client.post(
                    url,
                    data=data,
                    timeout=self._timeout,
                )
            else:
                client = get_async_http_client()
                response = await client.post(
                    url,
                    data=data,
                    timeout=self._timeout,
                )

            if response.status_code in (200, 201):
                result = response.json()
                doc_data = result.get("data", result)
                logger.info(f"Document uploaded: doc_id={doc_data.get('id')}")
                return doc_data

            error_msg = response.text[:200]
            logger.error(f"Upload failed: status={response.status_code}, {error_msg}")
            raise RagflowDocumentError(f"Upload failed: {error_msg}")

        except RagflowError:
            raise
        except httpx.TimeoutException:
            raise RagflowConnectionError(f"Upload timeout after {self._timeout}s")
        except httpx.RequestError as e:
            raise RagflowConnectionError(f"Network error: {str(e)[:200]}")
        except Exception as e:
            raise RagflowDocumentError(f"Unexpected error: {str(e)[:200]}")

    async def trigger_parsing(
        self,
        dataset_id: str,
        document_ids: List[str],
    ) -> Dict[str, Any]:
        """
        문서 파싱을 트리거합니다.

        POST /api/v1/datasets/{dataset_id}/documents/{doc_id}/run

        Args:
            dataset_id: RAGFlow dataset ID
            document_ids: 파싱할 문서 ID 리스트

        Returns:
            dict: 파싱 시작 결과

        Raises:
            RagflowConnectionError: 연결 실패
            RagflowDocumentError: 파싱 트리거 실패
        """
        if not self._base_url:
            raise RagflowConnectionError("RAGFLOW_BASE_URL not configured")

        results = []
        for doc_id in document_ids:
            url = f"{self._base_url}/api/v1/datasets/{dataset_id}/documents/{doc_id}/run"

            logger.info(f"Triggering parsing: dataset_id={dataset_id}, doc_id={doc_id}")

            try:
                if self._external_client:
                    response = await self._external_client.post(
                        url,
                        timeout=self._timeout,
                    )
                else:
                    client = get_async_http_client()
                    response = await client.post(
                        url,
                        timeout=self._timeout,
                    )

                if response.status_code in (200, 202):
                    result = response.json()
                    results.append(result.get("data", result))
                    logger.info(f"Parsing triggered: doc_id={doc_id}")
                else:
                    error_msg = response.text[:200]
                    logger.error(f"Parsing trigger failed: {error_msg}")
                    raise RagflowDocumentError(f"Parsing trigger failed: {error_msg}")

            except RagflowError:
                raise
            except httpx.TimeoutException:
                raise RagflowConnectionError(f"Parsing trigger timeout")
            except httpx.RequestError as e:
                raise RagflowConnectionError(f"Network error: {str(e)[:200]}")

        return {"triggered": len(results), "results": results}

    async def get_document_status(
        self,
        dataset_id: str,
        document_id: str,
    ) -> Dict[str, Any]:
        """
        문서 파싱 상태를 조회합니다.

        GET /api/v1/datasets/{dataset_id}/documents/{doc_id}

        Args:
            dataset_id: RAGFlow dataset ID
            document_id: 문서 ID

        Returns:
            dict: {"run": "DONE", "progress": 1.0, "chunk_count": 5, ...}

        Raises:
            RagflowConnectionError: 연결 실패
            RagflowDocumentError: 상태 조회 실패
        """
        if not self._base_url:
            raise RagflowConnectionError("RAGFLOW_BASE_URL not configured")

        url = f"{self._base_url}/api/v1/datasets/{dataset_id}/documents/{document_id}"

        try:
            if self._external_client:
                response = await self._external_client.get(
                    url,
                    timeout=self._timeout,
                )
            else:
                client = get_async_http_client()
                response = await client.get(
                    url,
                    timeout=self._timeout,
                )

            if response.status_code == 200:
                result = response.json()
                return result.get("data", result)

            if response.status_code == 404:
                raise RagflowDocumentError(f"Document not found: {document_id}")

            error_msg = response.text[:200]
            raise RagflowDocumentError(f"Status query failed: {error_msg}")

        except RagflowError:
            raise
        except httpx.TimeoutException:
            raise RagflowConnectionError(f"Status query timeout")
        except httpx.RequestError as e:
            raise RagflowConnectionError(f"Network error: {str(e)[:200]}")

    async def get_document_chunks(
        self,
        dataset_id: str,
        document_id: str,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """
        문서 청크를 조회합니다.

        GET /api/v1/datasets/{dataset_id}/documents/{doc_id}/chunks

        Args:
            dataset_id: RAGFlow dataset ID
            document_id: 문서 ID
            page: 페이지 번호 (1부터 시작)
            page_size: 페이지당 청크 수

        Returns:
            dict: {"chunks": [...], "total": N}

        Raises:
            RagflowConnectionError: 연결 실패
            RagflowDocumentError: 청크 조회 실패
        """
        if not self._base_url:
            raise RagflowConnectionError("RAGFLOW_BASE_URL not configured")

        url = f"{self._base_url}/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks"
        params = {"page": page, "page_size": page_size}

        try:
            if self._external_client:
                response = await self._external_client.get(
                    url,
                    params=params,
                    timeout=self._timeout,
                )
            else:
                client = get_async_http_client()
                response = await client.get(
                    url,
                    params=params,
                    timeout=self._timeout,
                )

            if response.status_code == 200:
                result = response.json()
                data = result.get("data", result)
                logger.info(
                    f"Chunks fetched: doc_id={document_id}, "
                    f"page={page}, count={len(data.get('chunks', []))}"
                )
                return data

            if response.status_code == 404:
                raise RagflowDocumentError(f"Document not found: {document_id}")

            error_msg = response.text[:200]
            raise RagflowDocumentError(f"Chunks query failed: {error_msg}")

        except RagflowError:
            raise
        except httpx.TimeoutException:
            raise RagflowConnectionError(f"Chunks query timeout")
        except httpx.RequestError as e:
            raise RagflowConnectionError(f"Network error: {str(e)[:200]}")


# =============================================================================
# Singleton Instance
# =============================================================================

_client: Optional[RagflowClient] = None


def get_ragflow_client() -> RagflowClient:
    """RagflowClient 싱글톤 인스턴스 반환."""
    global _client
    if _client is None:
        _client = RagflowClient()
    return _client


def clear_ragflow_client() -> None:
    """RagflowClient 싱글톤 초기화 (테스트용)."""
    global _client
    _client = None
