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

import json
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
# 실제 RAGFlow dataset ID 사용 (이전에 curl로 확인한 ID)
DOMAIN_TO_DATASET_ID: Dict[str, str] = {
    "POLICY": "6f3f9218e79011f0ad6f361530c2085d",
    "EDUCATION": "6f3f9218e79011f0ad6f361530c2085d",
    "HR": "6f3f9218e79011f0ad6f361530c2085d",
    "GENERAL": "6f3f9218e79011f0ad6f361530c2085d",
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
            if self._api_key:
                logger.info(f"RagflowClient initialized: base_url={self._base_url}, api_key={'*' * 10 + self._api_key[-4:] if len(self._api_key) > 4 else '***'}")
            else:
                logger.warning(f"RagflowClient initialized: base_url={self._base_url}, but RAGFLOW_API_KEY is not set!")
        else:
            logger.warning("RagflowClient: RAGFLOW_BASE_URL not configured")

    @property
    def is_configured(self) -> bool:
        """RAGFlow URL이 설정되었는지 확인."""
        return bool(self._base_url)

    def _get_headers(self, include_content_type: bool = True) -> Dict[str, str]:
        """API 요청 헤더 반환.
        
        Args:
            include_content_type: Content-Type 헤더 포함 여부 (multipart/form-data 전송 시 False)
        """
        headers = {
            "Accept": "application/json",
        }
        if include_content_type:
            headers["Content-Type"] = "application/json"
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
        
        logger.info(
            f"Uploading document to RAGFlow: dataset_id={dataset_id}, "
            f"file_name={file_name}, file_url={file_url[:100]}..."
        )

        try:
            # 파일을 다운로드하여 multipart/form-data로 전송
            client = get_async_http_client() if not self._external_client else self._external_client
            
            # 1. 파일 다운로드
            logger.debug(f"Downloading file from URL: {file_url[:100]}...")
            file_response = await client.get(file_url, timeout=self._timeout, follow_redirects=True)
            file_response.raise_for_status()
            file_content = file_response.content
            
            logger.debug(f"File downloaded: size={len(file_content)} bytes")
            
            # 2. MIME 타입 결정
            mime_type = "application/pdf"  # 기본값
            if file_name.lower().endswith(".pdf"):
                mime_type = "application/pdf"
            elif file_name.lower().endswith((".doc", ".docx")):
                mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            elif file_name.lower().endswith((".xls", ".xlsx")):
                mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif file_name.lower().endswith(".txt"):
                mime_type = "text/plain"
            elif file_name.lower().endswith(".md"):
                mime_type = "text/markdown"
            
            # 3. multipart/form-data로 파일 업로드
            headers = self._get_headers(include_content_type=False)  # multipart/form-data는 Content-Type 자동 설정
            
            # Authorization 헤더 확인 및 로깅
            if not headers.get("Authorization"):
                logger.error(f"RAGFLOW_API_KEY is not set! api_key={self._api_key[:10] if self._api_key else None}...")
                raise RagflowConnectionError("RAGFLOW_API_KEY not configured")
            
            logger.debug(f"Upload headers: Authorization={'Bearer ' + self._api_key[:10] + '...' if self._api_key else 'MISSING'}")
            
            files = {
                "file": (file_name, file_content, mime_type)  # 파일명, 내용, MIME 타입
            }
            
            if self._external_client:
                response = await self._external_client.post(
                    url,
                    files=files,
                    headers=headers,
                    timeout=self._timeout,
                )
            else:
                response = await client.post(
                    url,
                    files=files,
                    headers=headers,
                    timeout=self._timeout,
                )

            if response.status_code in (200, 201):
                result = response.json()
                
                # RAGFlow 에러 응답 체크 (HTTP 200이어도 code/message로 에러 표시)
                if result.get("code") != 0 or result.get("data") is False:
                    error_msg = result.get("message", "Unknown error")
                    logger.error(
                        f"RAGFlow upload error: code={result.get('code')}, "
                        f"message={error_msg}, response={result}"
                    )
                    raise RagflowDocumentError(f"RAGFlow error: {error_msg}")
                
                doc_data = result.get("data", result)
                
                # doc_data가 리스트인 경우 첫 번째 요소 사용
                if isinstance(doc_data, list):
                    if len(doc_data) > 0:
                        doc_data = doc_data[0]
                        logger.debug(f"RAGFlow returned list, using first element: {len(result.get('data', []))} items")
                    else:
                        logger.error(f"Invalid RAGFlow response: data is empty list")
                        raise RagflowDocumentError(
                            "Upload succeeded but no document info returned"
                        )
                
                # doc_data가 dict가 아니거나 id가 없으면 에러
                if not isinstance(doc_data, dict) or not doc_data.get("id"):
                    logger.error(f"Invalid RAGFlow response: data={doc_data}, type={type(doc_data)}")
                    raise RagflowDocumentError(
                        "Upload succeeded but no document info returned"
                    )
                
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

        POST /api/v1/datasets/{dataset_id}/chunks
        Body: {"document_ids": [...]}

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

        url = f"{self._base_url}/api/v1/datasets/{dataset_id}/chunks"
        headers = self._get_headers()
        payload = {"document_ids": document_ids}

        logger.info(f"Triggering parsing: dataset_id={dataset_id}, doc_ids={document_ids}")

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

            if response.status_code in (200, 202):
                result = response.json()
                logger.debug(f"Parsing trigger response: status={response.status_code}, result={result}")
                
                # RAGFlow 에러 응답 체크
                if result.get("code") != 0:
                    error_msg = result.get("message", "Unknown error")
                    logger.error(
                        f"RAGFlow parsing trigger error: code={result.get('code')}, "
                        f"message={error_msg}, response={result}"
                    )
                    raise RagflowDocumentError(f"RAGFlow parsing trigger error: {error_msg}")
                
                logger.info(f"Parsing triggered: dataset_id={dataset_id}, doc_ids={document_ids}, response_code={result.get('code', 'N/A')}")
                return result.get("data", result)
            else:
                error_msg = response.text[:200]
                logger.error(f"Parsing trigger failed: status={response.status_code}, error={error_msg}")
                raise RagflowDocumentError(f"Parsing trigger failed: {error_msg}")

        except RagflowError:
            raise
        except httpx.TimeoutException:
            raise RagflowConnectionError(f"Parsing trigger timeout")
        except httpx.RequestError as e:
            raise RagflowConnectionError(f"Network error: {str(e)[:200]}")

    async def get_document_status(
        self,
        dataset_id: str,
        document_id: str,
    ) -> Dict[str, Any]:
        """
        문서 파싱 상태를 조회합니다.

        GET /api/v1/datasets/{dataset_id}/documents (문서 리스트에서 특정 문서 찾기)
        Note: GET /api/v1/datasets/{dataset_id}/documents/{doc_id}는 PDF 파일을 반환하므로 사용하지 않음

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

        # 문서 리스트를 가져와서 특정 문서를 찾기
        url = f"{self._base_url}/api/v1/datasets/{dataset_id}/documents"
        headers = self._get_headers()

        try:
            if self._external_client:
                response = await self._external_client.get(
                    url,
                    headers=headers,
                    timeout=self._timeout,
                )
            else:
                client = get_async_http_client()
                response = await client.get(
                    url,
                    headers=headers,
                    timeout=self._timeout,
                )

            if response.status_code != 200:
                if response.status_code == 404:
                    raise RagflowDocumentError(f"Dataset or document not found: {document_id}")
                error_msg = response.text[:500]
                raise RagflowDocumentError(f"Status query failed: {error_msg}")

            # JSON 파싱 (에러 처리 포함)
            try:
                result = response.json()
            except (ValueError, UnicodeDecodeError) as e:
                # JSON 파싱 실패 시 content를 디코딩하여 재시도
                logger.warning(f"JSON parse error, attempting to decode with errors='replace': {e}")
                try:
                    content = response.content.decode("utf-8", errors="replace")
                    result = json.loads(content)
                except Exception as e2:
                    logger.error(f"Failed to parse response as JSON: {e2}, content_preview={response.content[:200]}")
                    raise RagflowDocumentError(f"Invalid JSON response: {str(e2)[:200]}")

            # RAGFlow 응답 형식 확인 및 문서 리스트 추출
            data = result.get("data", [])
            
            # data가 dict인 경우 (예: {"docs": [...]})
            if isinstance(data, dict):
                documents = data.get("docs", data.get("data", []))
                logger.debug(f"RAGFlow documents list response: data is dict, extracted docs list with {len(documents)} items")
            # data가 list인 경우
            elif isinstance(data, list):
                documents = data
                logger.debug(f"RAGFlow documents list response: data is list with {len(documents)} items")
            else:
                logger.error(f"Invalid RAGFlow response format: 'data' is not a list or dict, got {type(data)}, value={data}")
                raise RagflowDocumentError(
                    f"Invalid response format: 'data' is not a list or dict, got {type(data)}"
                )

            # 문서 리스트에서 특정 document_id 찾기
            target_doc = None
            for doc in documents:
                if not isinstance(doc, dict):
                    logger.warning(f"Document item is not a dict: {type(doc)}, skipping")
                    continue
                doc_id = doc.get("id") or doc.get("doc_id")
                if doc_id == document_id:
                    target_doc = doc
                    break

            if target_doc is None:
                available_ids = [doc.get("id") or doc.get("doc_id") for doc in documents if isinstance(doc, dict)]
                logger.error(
                    f"Document not found in list: document_id={document_id}, "
                    f"available_ids={available_ids[:10]}..."  # 처음 10개만 로깅
                )
                raise RagflowDocumentError(f"Document not found: {document_id}")

            logger.debug(f"Document status retrieved: doc_id={document_id}, run={target_doc.get('run')}")
            return target_doc

        except RagflowError:
            raise
        except httpx.TimeoutException:
            raise RagflowConnectionError(f"Status query timeout")
        except httpx.RequestError as e:
            raise RagflowConnectionError(f"Network error: {str(e)[:200]}")
        except Exception as e:
            logger.error(f"Unexpected error in get_document_status: {type(e).__name__}: {e}")
            raise RagflowDocumentError(f"Unexpected error: {str(e)[:200]}")

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
        headers = self._get_headers()

        try:
            if self._external_client:
                response = await self._external_client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self._timeout,
                )
            else:
                client = get_async_http_client()
                response = await client.get(
                    url,
                    params=params,
                    headers=headers,
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
