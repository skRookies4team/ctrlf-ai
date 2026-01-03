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
        internal_token: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        RagflowClient 초기화.

        Args:
            base_url: RAGFlow 서비스 URL. None이면 설정에서 가져옴.
            api_key: RAGFlow API 키. None이면 설정에서 가져옴.
            internal_token: 내부 API 인증 토큰 (X-Internal-Token). None이면 설정에서 가져옴.
            timeout: HTTP 요청 타임아웃 (초).
            client: 커스텀 httpx 클라이언트 (테스트용)
        """
        settings = get_settings()
        self._base_url = (base_url or settings.ragflow_base_url or "").rstrip("/")
        self._api_key = api_key or settings.RAGFLOW_API_KEY
        # RAGFlow 내부 API용 토큰: internal_token이 명시적으로 제공되면 사용,
        # 그렇지 않으면 RAGFLOW_API_KEY 사용 (RAGFlow 서버는 AI_TO_RAGFLOW_TOKEN=${RAGFLOW_API_KEY}로 설정됨)
        # BACKEND_INTERNAL_TOKEN은 백엔드 서버와의 통신용이므로 사용하지 않음
        self._internal_token = internal_token or settings.RAGFLOW_API_KEY
        self._timeout = timeout
        self._external_client = client

        if self._base_url:
            if self._api_key:
                logger.info(f"RagflowClient initialized: base_url={self._base_url}, api_key={'*' * 10 + self._api_key[-4:] if len(self._api_key) > 4 else '***'}")
            else:
                logger.warning(f"RagflowClient initialized: base_url={self._base_url}, but RAGFLOW_API_KEY is not set!")
            if self._internal_token:
                logger.info(f"RagflowClient internal_token configured: {'*' * 10 + self._internal_token[-4:] if len(self._internal_token) > 4 else '***'}")
            else:
                logger.warning("RagflowClient: internal_token not configured (BACKEND_INTERNAL_TOKEN or RAGFLOW_API_KEY)")
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

    def _get_internal_headers(self) -> Dict[str, str]:
        """내부 API 요청 헤더 반환 (X-Internal-Token 사용)."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._internal_token:
            headers["X-Internal-Token"] = self._internal_token
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

    async def ingest_document(
        self,
        dataset_id: str,
        doc_id: str,
        file_url: str,
        version: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        RAGFlow에 문서를 ingest합니다 (업로드 + 파싱 통합).

        POST /internal/ragflow/ingest

        Args:
            dataset_id: RAGFlow dataset ID 또는 이름
            doc_id: 문서 ID
            file_url: 문서 파일 URL (S3 presigned URL 등)
            version: 문서 버전 (선택)
            meta: 추가 메타데이터 (선택)

        Returns:
            dict: {"received": True, "ingestId": "...", "status": "QUEUED"}

        Raises:
            RagflowConnectionError: 연결 실패
            RagflowDocumentError: ingest 실패
        """
        if not self._base_url:
            raise RagflowConnectionError("RAGFLOW_BASE_URL not configured")

        if not self._internal_token:
            raise RagflowConnectionError("RAGFLOW_API_KEY not configured (required for internal ingest API)")

        url = f"{self._base_url}/v1/internal_ragflow/internal/ragflow/ingest"
        headers = self._get_internal_headers()
        
        # 디버그: 전송되는 헤더 확인
        masked_token = f"{'*' * 10}{self._internal_token[-4:]}" if self._internal_token and len(self._internal_token) > 4 else "***"
        logger.debug(f"Ingest request headers: X-Internal-Token={masked_token}")

        payload = {
            "datasetId": dataset_id,
            "docId": doc_id,
            "fileUrl": file_url,
            "replace": True,
        }
        if version is not None:
            payload["version"] = version
        if meta:
            payload["meta"] = meta

        masked_token = f"{'*' * 10}{self._internal_token[-4:]}" if self._internal_token and len(self._internal_token) > 4 else "NOT_SET"
        logger.info(
            f"Ingesting document to RAGFlow: dataset_id={dataset_id}, "
            f"doc_id={doc_id}, file_url={file_url[:100]}..., "
            f"internal_token={masked_token}"
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
                result = response.json()
                logger.info(f"Document ingest accepted: doc_id={doc_id}, ingest_id={result.get('ingestId')}")
                return result
            elif response.status_code == 401:
                error_detail = response.text[:500] if response.text else "No error message"
                masked_token = f"{'*' * 10}{self._internal_token[-4:]}" if self._internal_token and len(self._internal_token) > 4 else "NOT_SET"
                logger.error(
                    f"Ingest unauthorized: status=401, "
                    f"internal_token={masked_token}, "
                    f"url={url}, "
                    f"error_detail={error_detail}"
                )
                raise RagflowDocumentError("Unauthorized - invalid or missing internal token")
            elif response.status_code == 400:
                error_msg = response.text[:200]
                logger.error(f"Ingest failed: status={response.status_code}, error={error_msg}")
                raise RagflowDocumentError(f"Ingest failed: {error_msg}")
            else:
                error_msg = response.text[:200]
                logger.error(f"Ingest failed: status={response.status_code}, error={error_msg}")
                raise RagflowDocumentError(f"Ingest failed: {error_msg}")

        except RagflowError:
            raise
        except httpx.TimeoutException:
            raise RagflowConnectionError(f"Ingest timeout after {self._timeout}s")
        except httpx.RequestError as e:
            raise RagflowConnectionError(f"Network error: {str(e)[:200]}")
        except Exception as e:
            raise RagflowDocumentError(f"Unexpected error: {str(e)[:200]}")

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
        headers = self._get_headers()
        for doc_id in document_ids:
            url = f"{self._base_url}/api/v1/datasets/{dataset_id}/documents/{doc_id}/run"

            logger.info(f"Triggering parsing: dataset_id={dataset_id}, doc_id={doc_id}")

            try:
                if self._external_client:
                    response = await self._external_client.post(
                        url,
                        headers=headers,
                        timeout=self._timeout,
                    )
                else:
                    client = get_async_http_client()
                    response = await client.post(
                        url,
                        headers=headers,
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
                    
                    results.append(result.get("data", result))
                    logger.info(f"Parsing triggered: doc_id={doc_id}, response_code={result.get('code', 'N/A')}")
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

        return {"triggered": len(results), "results": results}

    async def find_document_by_doc_id(
        self,
        dataset_id: str,
        doc_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        문서 리스트에서 docId로 문서를 찾습니다.

        GET /api/v1/datasets/{dataset_id}/documents

        Args:
            dataset_id: RAGFlow dataset ID
            doc_id: 문서 docId (사용자 정의 ID)

        Returns:
            dict: 문서 정보 또는 None (찾지 못한 경우)
        """
        if not self._base_url:
            raise RagflowConnectionError("RAGFLOW_BASE_URL not configured")

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

            if response.status_code == 200:
                result = response.json()
                data = result.get("data", [])
                
                # data가 dict인 경우 (예: {"docs": [...]})
                if isinstance(data, dict):
                    documents = data.get("docs", data.get("data", []))
                elif isinstance(data, list):
                    documents = data
                else:
                    return None

                # docId로 문서 찾기
                logger.debug(f"Searching for doc_id={doc_id} in {len(documents)} documents")
                # 디버그: 처음 몇 개 문서의 구조 확인
                if documents:
                    sample_doc = documents[0] if isinstance(documents[0], dict) else {}
                    logger.debug(
                        f"Sample document structure: keys={list(sample_doc.keys())}, "
                        f"meta={sample_doc.get('meta', {})}, "
                        f"meta_fields={sample_doc.get('meta_fields', {})}, "
                        f"name={sample_doc.get('name', '')}, "
                        f"location={sample_doc.get('location', '')}"
                    )
                
                # main.py는 display_name으로 doc_id를 저장하므로, name 필드와 정확히 일치해야 함
                for doc in documents:
                    if not isinstance(doc, dict):
                        continue
                    
                    # 1. name 필드가 doc_id와 정확히 일치하는지 확인 (가장 정확)
                    # main.py에서 display_name=effective_doc_id로 저장하므로
                    doc_name = doc.get("name", "")
                    if doc_name == doc_id:
                        logger.debug(f"Found document by exact name match: doc_id={doc_id}, name={doc_name}, ragflow_id={doc.get('id')}")
                        return doc
                    
                    # 2. meta.docId 확인
                    meta = doc.get("meta", {})
                    if isinstance(meta, dict) and meta.get("docId") == doc_id:
                        logger.debug(f"Found document by meta.docId: doc_id={doc_id}, ragflow_id={doc.get('id')}")
                        return doc
                    
                    # 3. meta_fields에서 docId 확인
                    meta_fields = doc.get("meta_fields", {})
                    if isinstance(meta_fields, dict):
                        if meta_fields.get("docId") == doc_id:
                            logger.debug(f"Found document by meta_fields.docId: doc_id={doc_id}, ragflow_id={doc.get('id')}")
                            return doc
                    elif isinstance(meta_fields, str):
                        try:
                            meta_fields_dict = json.loads(meta_fields)
                            if isinstance(meta_fields_dict, dict) and meta_fields_dict.get("docId") == doc_id:
                                logger.debug(f"Found document by meta_fields (JSON).docId: doc_id={doc_id}, ragflow_id={doc.get('id')}")
                                return doc
                        except:
                            pass
                    
                    # 4. name 필드에 docId가 포함되어 있는지 확인 (fallback)
                    if doc_id in doc_name:
                        logger.debug(f"Found document by name contains: doc_id={doc_id}, name={doc_name}, ragflow_id={doc.get('id')}")
                        return doc
                    
                    # 5. location 필드에서 docId 확인
                    doc_location = doc.get("location", "")
                    if doc_id in doc_location:
                        logger.debug(f"Found document by location: doc_id={doc_id}, location={doc_location}, ragflow_id={doc.get('id')}")
                        return doc
                    
                    # 6. id 필드가 doc_id와 일치하는지 확인 (혹시 모를 경우)
                    if doc.get("id") == doc_id:
                        logger.debug(f"Found document by id: doc_id={doc_id}, ragflow_id={doc.get('id')}")
                        return doc

                # 디버그: 실제 문서들의 name과 location 값 확인
                sample_names = [doc.get("name", "") for doc in documents[:5] if isinstance(doc, dict)]
                logger.debug(f"Document not found: doc_id={doc_id}, checked {len(documents)} documents, sample names={sample_names}")
                
                # 추가: 최근 생성된 문서 중에서 찾기 (ingest 직후에는 아직 name이 설정되지 않았을 수 있음)
                # create_time 기준으로 정렬하여 최신 문서 확인
                if documents:
                    try:
                        import time
                        current_time_ms = int(time.time() * 1000)  # 현재 시간 (밀리초)
                        
                        # create_time이 있는 문서들을 시간순으로 정렬
                        sorted_docs = sorted(
                            [d for d in documents if isinstance(d, dict) and d.get("create_time")],
                            key=lambda x: int(x.get("create_time", 0)),
                            reverse=True
                        )
                        if sorted_docs:
                            latest_doc = sorted_docs[0]
                            latest_create_time = int(latest_doc.get("create_time", 0))
                            time_diff_seconds = (current_time_ms - latest_create_time) / 1000
                            
                            logger.debug(
                                f"Latest document: name={latest_doc.get('name')}, "
                                f"create_time={latest_create_time}, "
                                f"id={latest_doc.get('id')}, "
                                f"time_diff={time_diff_seconds:.1f}s"
                            )
                            
                            # 최근 60초 이내에 생성된 문서이고, 아직 처리 중이거나 name이 설정되지 않았을 수 있음
                            # ingest 직후에는 문서가 리스트에 나타나지 않을 수 있으므로, 
                            # 최신 문서가 ingest 이후에 생성된 것인지 확인
                            if time_diff_seconds < 60:
                                logger.debug(
                                    f"Found recent document (created {time_diff_seconds:.1f}s ago), "
                                    f"but name doesn't match doc_id. "
                                    f"This might be the ingested document before name is set."
                                )
                                # name이 아직 설정되지 않았을 수 있으므로, 최신 문서를 반환
                                # (나중에 name이 업데이트되면 정확히 매칭됨)
                                if not latest_doc.get("name") or latest_doc.get("name") == "":
                                    logger.info(
                                        f"Returning latest document with empty name: "
                                        f"id={latest_doc.get('id')}, create_time={latest_create_time}"
                                    )
                                    return latest_doc
                    except Exception as e:
                        logger.debug(f"Error sorting documents by time: {e}")
                
                return None
            else:
                logger.warning(f"Failed to get document list: status={response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Error finding document by doc_id: {e}")
            return None

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
