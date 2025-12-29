"""
RAGFlow Ingest 클라이언트 (POLICY 문서 Ingest)

AI 서버에서 RAGFlow로 문서 ingest를 요청하는 HTTP 클라이언트입니다.

사용처:
- POST /internal/ai/rag-documents/ingest 라우트에서 호출

API:
- POST {RAGFLOW_BASE_URL}/internal/ragflow/ingest
"""

from typing import Any, Dict, Optional

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Constants
# =============================================================================

DEFAULT_TIMEOUT = 10.0  # RAGFlow 호출 타임아웃 (초)


# =============================================================================
# Exceptions
# =============================================================================


class RAGFlowIngestError(Exception):
    """RAGFlow ingest 실패 예외."""

    def __init__(
        self,
        status_code: int,
        message: str,
        error_code: str = "RAGFLOW_INGEST_ERROR",
    ):
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        super().__init__(f"{error_code}: {message} (status={status_code})")


class RAGFlowUnavailableError(RAGFlowIngestError):
    """RAGFlow 서비스 불가 예외."""

    def __init__(self, message: str):
        super().__init__(
            status_code=502,
            message=message,
            error_code="RAGFLOW_UNAVAILABLE",
        )


# =============================================================================
# RAGFlow Ingest Client
# =============================================================================


class RAGFlowIngestClient:
    """
    RAGFlow Ingest HTTP 클라이언트.

    AI → RAGFlow ingest 요청을 처리합니다.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        internal_token: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        RAGFlowIngestClient 초기화.

        Args:
            base_url: RAGFlow 서비스 URL. None이면 설정에서 가져옴.
            internal_token: 내부 API 인증 토큰 (X-Internal-Token). None이면 설정에서 가져옴.
            timeout: HTTP 요청 타임아웃 (초).
            client: 커스텀 httpx 클라이언트 (테스트용)
        """
        settings = get_settings()
        self._base_url = (base_url or settings.ragflow_base_url or "").rstrip("/")
        self._internal_token = internal_token or settings.BACKEND_INTERNAL_TOKEN
        self._timeout = timeout
        self._external_client = client

    @property
    def is_configured(self) -> bool:
        """RAGFlow URL이 설정되었는지 확인."""
        return bool(self._base_url)

    def _get_headers(self) -> Dict[str, str]:
        """X-Internal-Token 헤더 반환."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._internal_token:
            headers["X-Internal-Token"] = self._internal_token
        return headers

    async def ingest(
        self,
        dataset_id: str,
        doc_id: str,
        version: int,
        file_url: str,
        rag_document_pk: str,
        domain: str,
        trace_id: str,
        request_id: str,
    ) -> Dict[str, Any]:
        """
        RAGFlow에 문서 ingest를 요청합니다.

        POST {RAGFLOW_BASE_URL}/internal/ragflow/ingest

        Args:
            dataset_id: RAGFlow dataset 이름 (예: "사내규정")
            doc_id: 문서 ID (예: "POL-EDU-015")
            version: 문서 버전
            file_url: 문서 파일 URL (S3 등)
            rag_document_pk: RAG 문서 PK (UUID)
            domain: 도메인 (예: "POLICY")
            trace_id: 추적 ID
            request_id: 요청 ID

        Returns:
            dict: RAGFlow 응답

        Raises:
            RAGFlowUnavailableError: RAGFlow 서비스 연결 실패
            RAGFlowIngestError: ingest 요청 실패
        """
        if not self._base_url:
            raise RAGFlowUnavailableError("RAGFLOW_BASE_URL not configured")

        url = f"{self._base_url}/internal/ragflow/ingest"
        headers = self._get_headers()

        payload = {
            "datasetId": dataset_id,
            "docId": doc_id,
            "version": version,
            "fileUrl": file_url,
            "replace": True,
            "meta": {
                "ragDocumentPk": rag_document_pk,
                "domain": domain,
                "traceId": trace_id,
                "requestId": request_id,
            },
        }

        logger.info(
            f"Sending RAGFlow ingest request: doc_id={doc_id}, version={version}, "
            f"dataset_id={dataset_id}, trace_id={trace_id}"
        )

        try:
            if self._external_client:
                response = await self._external_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )
            else:
                client = get_async_http_client()
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )

            if response.status_code == 202:
                logger.info(
                    f"RAGFlow ingest accepted: doc_id={doc_id}, version={version}"
                )
                try:
                    return response.json()
                except Exception:
                    return {"accepted": True}

            elif response.status_code == 401:
                raise RAGFlowIngestError(
                    status_code=401,
                    message="Unauthorized - invalid or missing token",
                    error_code="RAGFLOW_UNAUTHORIZED",
                )
            elif response.status_code == 400:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("message", response.text[:200])
                except Exception:
                    error_msg = response.text[:200]
                raise RAGFlowIngestError(
                    status_code=400,
                    message=f"Bad request: {error_msg}",
                    error_code="RAGFLOW_BAD_REQUEST",
                )
            elif response.status_code >= 500:
                raise RAGFlowUnavailableError(
                    f"RAGFlow server error: {response.status_code}"
                )
            else:
                raise RAGFlowIngestError(
                    status_code=response.status_code,
                    message=f"Unexpected status: {response.text[:200]}",
                    error_code="RAGFLOW_INGEST_FAILED",
                )

        except RAGFlowIngestError:
            raise
        except httpx.TimeoutException:
            logger.error(f"RAGFlow ingest timeout: doc_id={doc_id}")
            raise RAGFlowUnavailableError(f"Timeout after {self._timeout}s")
        except httpx.RequestError as e:
            logger.error(f"RAGFlow ingest network error: doc_id={doc_id}, error={e}")
            raise RAGFlowUnavailableError(f"Network error: {str(e)[:200]}")
        except Exception as e:
            logger.error(f"RAGFlow ingest unexpected error: doc_id={doc_id}, error={e}")
            raise RAGFlowIngestError(
                status_code=0,
                message=f"Unexpected error: {str(e)[:200]}",
                error_code="RAGFLOW_INGEST_FAILED",
            )


# =============================================================================
# Singleton Instance
# =============================================================================

_client: Optional[RAGFlowIngestClient] = None


def get_ragflow_ingest_client() -> RAGFlowIngestClient:
    """RAGFlowIngestClient 싱글톤 인스턴스 반환."""
    global _client
    if _client is None:
        _client = RAGFlowIngestClient()
    return _client


def clear_ragflow_ingest_client() -> None:
    """RAGFlowIngestClient 싱글톤 초기화 (테스트용)."""
    global _client
    _client = None
