"""
통합 백엔드 API 클라이언트 (Unified Backend Client)

ctrlf-back (Spring 백엔드)와의 모든 HTTP 통신을 담당합니다.

기능:
1. AI 로그 전송 (send_ai_log)
2. 헬스체크 (health_check)
3. 비즈니스 데이터 조회 - 교육 현황, 사고 통계 등
4. 콜백 알림 - 스크립트/영상 생성 완료
5. render-spec 조회

인증:
- 일반 API: Authorization: Bearer 헤더
- 내부 API: X-Internal-Token 헤더

Usage:
    from app.clients.backend_client import get_backend_client

    client = get_backend_client()

    # AI 로그 전송
    await client.send_ai_log(log_entry)

    # 비즈니스 데이터 조회
    await client.get_employee_edu_status(user_id="user-123")

    # 콜백 알림
    await client.notify_script_complete(material_id, script_id, script, version)

    # render-spec 조회
    await client.get_render_spec(script_id)

Environment Variables:
    BACKEND_BASE_URL: 백엔드 서비스 URL
    BACKEND_API_TOKEN: API 인증 토큰 (Authorization: Bearer)
    BACKEND_INTERNAL_TOKEN: 내부 API 인증 토큰 (X-Internal-Token)
    BACKEND_TIMEOUT_SEC: API 타임아웃 (초)
"""

import time
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.exceptions import ErrorType, ServiceType, UpstreamServiceError
from app.core.logging import get_logger
from app.core.retry import BACKEND_RETRY_CONFIG, DEFAULT_BACKEND_TIMEOUT, retry_async_operation
from app.models.ai_log import AILogEntry, AILogResponse, to_backend_log_payload

logger = get_logger(__name__)


# =============================================================================
# API 엔드포인트 경로 상수
# =============================================================================

# AI 로그
BACKEND_AI_LOG_PATH = "/api/ai-logs"

# 교육(EDU) 도메인
BACKEND_EDU_STATUS_PATH = "/api/edu/status"
BACKEND_EDU_STATS_PATH = "/api/edu/stats"

# 사고/위반(INCIDENT) 도메인
BACKEND_INCIDENT_OVERVIEW_PATH = "/api/incidents/overview"
BACKEND_INCIDENT_DETAIL_PATH = "/api/incidents/{incident_id}"
BACKEND_REPORT_GUIDE_PATH = "/api/incidents/report-guide"

# 콜백
BACKEND_SCRIPT_COMPLETE_PATH = "/video/script/complete"
BACKEND_JOB_COMPLETE_PATH = "/video/job/{job_id}/complete"

# render-spec
BACKEND_RENDER_SPEC_PATH = "/internal/scripts/{script_id}/render-spec"

# SourceSet 관련 (FastAPI → Spring)
BACKEND_SOURCE_SET_DOCUMENTS_PATH = "/internal/source-sets/{source_set_id}/documents"
BACKEND_SOURCE_SET_COMPLETE_PATH = "/internal/callbacks/source-sets/{source_set_id}/complete"
BACKEND_CHUNK_BULK_UPSERT_PATH = "/internal/rag/documents/{document_id}/chunks:bulk"
BACKEND_FAIL_CHUNK_BULK_UPSERT_PATH = "/internal/rag/documents/{document_id}/fail-chunks:bulk"

# RAG 문서 상태 업데이트 (POLICY ingest 콜백 → Backend)
BACKEND_RAG_DOCUMENT_STATUS_PATH = "/internal/rag/documents/{rag_document_pk}/status"


# =============================================================================
# Request/Response Models
# =============================================================================


class ScriptCompleteRequest(BaseModel):
    """스크립트 생성 완료 콜백 요청."""
    materialId: str
    scriptId: str
    script: str
    version: int


class ScriptCompleteResponse(BaseModel):
    """스크립트 생성 완료 콜백 응답."""
    success: bool = True
    message: Optional[str] = None


class JobCompleteRequest(BaseModel):
    """영상 생성 완료 콜백 요청."""
    jobId: str
    videoUrl: str
    duration: int
    status: str


class JobCompleteResponse(BaseModel):
    """영상 생성 완료 콜백 응답."""
    saved: bool = True


class BackendDataResponse:
    """백엔드 데이터 조회 응답."""

    def __init__(
        self,
        success: bool,
        data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ):
        self.success = success
        self.data = data or {}
        self.error_message = error_message


# =============================================================================
# Exceptions
# =============================================================================


class BackendClientError(Exception):
    """백엔드 클라이언트 기본 예외."""

    def __init__(
        self,
        endpoint: str,
        status_code: int,
        message: str,
        error_code: str = "BACKEND_ERROR",
    ):
        self.endpoint = endpoint
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        super().__init__(
            f"{error_code}: {message} (endpoint={endpoint}, status={status_code})"
        )


class CallbackError(BackendClientError):
    """콜백 실패 예외."""
    pass


class ScriptCompleteCallbackError(CallbackError):
    """스크립트 완료 콜백 실패 예외."""

    def __init__(
        self,
        status_code: int,
        message: str,
        error_code: str = "SCRIPT_COMPLETE_CALLBACK_FAILED",
    ):
        super().__init__(
            endpoint=BACKEND_SCRIPT_COMPLETE_PATH,
            status_code=status_code,
            message=message,
            error_code=error_code,
        )


class JobCompleteCallbackError(CallbackError):
    """영상 생성 완료 콜백 실패 예외."""

    def __init__(
        self,
        job_id: str,
        status_code: int,
        message: str,
        error_code: str = "JOB_COMPLETE_CALLBACK_FAILED",
    ):
        super().__init__(
            endpoint=f"/video/job/{job_id}/complete",
            status_code=status_code,
            message=message,
            error_code=error_code,
        )
        self.job_id = job_id


class ScriptFetchError(BackendClientError):
    """스크립트 조회 실패 예외."""

    def __init__(
        self,
        script_id: str,
        status_code: int,
        message: str,
        error_code: str = "SCRIPT_FETCH_FAILED",
    ):
        super().__init__(
            endpoint=f"/internal/scripts/{script_id}/render-spec",
            status_code=status_code,
            message=message,
            error_code=error_code,
        )
        self.script_id = script_id


class EmptyRenderSpecError(Exception):
    """빈 render-spec 예외."""

    def __init__(self, script_id: str):
        self.script_id = script_id
        self.error_code = "EMPTY_RENDER_SPEC"
        super().__init__(f"EMPTY_RENDER_SPEC: No scenes in render-spec (script_id={script_id})")


class SourceSetDocumentsFetchError(BackendClientError):
    """소스셋 문서 목록 조회 실패 예외."""

    def __init__(
        self,
        source_set_id: str,
        status_code: int,
        message: str,
        error_code: str = "SOURCE_SET_DOCS_FETCH_FAILED",
    ):
        super().__init__(
            endpoint=f"/internal/source-sets/{source_set_id}/documents",
            status_code=status_code,
            message=message,
            error_code=error_code,
        )
        self.source_set_id = source_set_id


class SourceSetCompleteCallbackError(CallbackError):
    """소스셋 완료 콜백 실패 예외."""

    def __init__(
        self,
        source_set_id: str,
        status_code: int,
        message: str,
        error_code: str = "SOURCE_SET_COMPLETE_CALLBACK_FAILED",
    ):
        super().__init__(
            endpoint=f"/internal/callbacks/source-sets/{source_set_id}/complete",
            status_code=status_code,
            message=message,
            error_code=error_code,
        )
        self.source_set_id = source_set_id


class ChunkBulkUpsertError(BackendClientError):
    """청크 벌크 업서트 실패 예외."""

    def __init__(
        self,
        document_id: str,
        status_code: int,
        message: str,
        error_code: str = "CHUNK_BULK_UPSERT_FAILED",
    ):
        super().__init__(
            endpoint=f"/internal/rag/documents/{document_id}/chunks:bulk",
            status_code=status_code,
            message=message,
            error_code=error_code,
        )
        self.document_id = document_id


class FailChunkBulkUpsertError(BackendClientError):
    """실패 청크 벌크 업서트 실패 예외."""

    def __init__(
        self,
        document_id: str,
        status_code: int,
        message: str,
        error_code: str = "FAIL_CHUNK_BULK_UPSERT_FAILED",
    ):
        super().__init__(
            endpoint=f"/internal/rag/documents/{document_id}/fail-chunks:bulk",
            status_code=status_code,
            message=message,
            error_code=error_code,
        )
        self.document_id = document_id


class RAGDocumentStatusUpdateError(BackendClientError):
    """RAG 문서 상태 업데이트 실패 예외."""

    def __init__(
        self,
        rag_document_pk: str,
        status_code: int,
        message: str,
        error_code: str = "RAG_DOCUMENT_STATUS_UPDATE_FAILED",
    ):
        super().__init__(
            endpoint=f"/internal/rag/documents/{rag_document_pk}/status",
            status_code=status_code,
            message=message,
            error_code=error_code,
        )
        self.rag_document_pk = rag_document_pk


# =============================================================================
# Unified Backend Client
# =============================================================================


class BackendClient:
    """
    통합 백엔드 API 클라이언트.

    ctrlf-back (Spring 백엔드)와의 모든 HTTP 통신을 담당합니다.

    Attributes:
        _base_url: 백엔드 서비스 base URL
        _api_token: API 인증 토큰 (Authorization: Bearer)
        _internal_token: 내부 API 인증 토큰 (X-Internal-Token)
        _timeout: HTTP 요청 타임아웃 (초)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        internal_token: Optional[str] = None,
        timeout: float = DEFAULT_BACKEND_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
        token: Optional[str] = None,  # 하위 호환성: internal_token 별칭
    ) -> None:
        """
        BackendClient 초기화.

        Args:
            base_url: 백엔드 서비스 URL. None이면 설정에서 가져옴.
            api_token: API 인증 토큰 (Bearer). None이면 설정에서 가져옴.
            internal_token: 내부 API 인증 토큰 (X-Internal-Token). None이면 설정에서 가져옴.
            timeout: HTTP 요청 타임아웃 (초).
            client: 커스텀 httpx 클라이언트 (테스트용)
            token: 하위 호환성을 위한 internal_token 별칭 (deprecated)
        """
        settings = get_settings()
        self._base_url = (base_url or settings.backend_base_url or "").rstrip("/")
        self._api_token = api_token if api_token is not None else settings.BACKEND_API_TOKEN
        # token은 internal_token의 별칭 (하위 호환성)
        effective_internal_token = internal_token or token
        self._internal_token = effective_internal_token or settings.BACKEND_INTERNAL_TOKEN
        self._timeout = timeout or settings.BACKEND_TIMEOUT_SEC
        self._external_client = client
        self._last_latency_ms: Optional[int] = None

    @property
    def is_configured(self) -> bool:
        """백엔드 URL이 설정되었는지 확인."""
        return bool(self._base_url)

    def _get_bearer_headers(self) -> Dict[str, str]:
        """Authorization: Bearer 헤더 반환 (일반 API용)."""
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    def _get_internal_headers(self) -> Dict[str, str]:
        """X-Internal-Token 헤더 반환 (내부 API용)."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._internal_token:
            headers["X-Internal-Token"] = self._internal_token
        return headers

    def get_last_latency_ms(self) -> Optional[int]:
        """마지막 요청의 latency를 반환합니다."""
        return self._last_latency_ms

    # =========================================================================
    # AI 로그 & 헬스체크
    # =========================================================================

    async def send_ai_log(self, log_entry: AILogEntry) -> AILogResponse:
        """
        AI 로그를 백엔드로 전송합니다.

        Args:
            log_entry: 전송할 AI 로그 엔트리

        Returns:
            AILogResponse: 백엔드 응답
        """
        if not self._base_url:
            logger.debug("Backend URL not configured, returning mock response")
            return AILogResponse(
                success=True,
                log_id=None,
                message="Backend not configured (local only)",
            )

        endpoint = f"{self._base_url}{BACKEND_AI_LOG_PATH}"

        try:
            client = get_async_http_client()
            payload = to_backend_log_payload(log_entry)
            headers = self._get_bearer_headers()

            response = await client.post(
                endpoint,
                json=payload,
                headers=headers if headers else None,
                timeout=self._timeout,
            )

            if response.status_code in (200, 201):
                try:
                    data = response.json()
                    return AILogResponse(
                        success=data.get("success", True),
                        log_id=data.get("id") or data.get("log_id") or data.get("logId"),
                        message=data.get("message", "Log saved successfully"),
                    )
                except Exception:
                    return AILogResponse(
                        success=True,
                        log_id=None,
                        message="Log saved (no response body)",
                    )
            else:
                logger.warning(
                    f"Backend AI log failed: status={response.status_code}, "
                    f"body={response.text[:200]}"
                )
                return AILogResponse(
                    success=False,
                    log_id=None,
                    message=f"HTTP {response.status_code}: {response.text[:100]}",
                )

        except Exception as e:
            logger.warning(f"Backend AI log error: {e}")
            return AILogResponse(
                success=False,
                log_id=None,
                message=str(e),
            )

    async def health_check(self) -> bool:
        """백엔드 서비스 상태를 확인합니다."""
        if not self._base_url:
            return False

        try:
            client = get_async_http_client()
            response = await client.get(
                f"{self._base_url}/actuator/health",
                timeout=self._timeout,
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Backend health check failed: {e}")
            return False

    # =========================================================================
    # 비즈니스 데이터 조회 (교육, 사고)
    # =========================================================================

    async def get_employee_edu_status(
        self,
        user_id: str,
        year: Optional[int] = None,
    ) -> BackendDataResponse:
        """직원 본인의 교육 수료 현황/기한을 조회합니다."""
        if not self.is_configured:
            logger.debug("Backend URL not configured, returning mock edu status")
            return self._mock_employee_edu_status(user_id)

        endpoint = f"{self._base_url}{BACKEND_EDU_STATUS_PATH}"
        params: Dict[str, Any] = {"userId": user_id}
        if year:
            params["year"] = year

        return await self._get_request(endpoint, params)

    async def get_department_edu_stats(
        self,
        department_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> BackendDataResponse:
        """관리자용 부서별/전체 교육 통계를 조회합니다."""
        if not self.is_configured:
            logger.debug("Backend URL not configured, returning mock dept edu stats")
            return self._mock_department_edu_stats(department_id)

        endpoint = f"{self._base_url}{BACKEND_EDU_STATS_PATH}"
        params: Dict[str, Any] = {}
        if department_id:
            params["departmentId"] = department_id
        if filters:
            params.update(filters)

        return await self._get_request(endpoint, params)

    async def get_incident_overview(
        self,
        filters: Optional[Dict[str, Any]] = None,
    ) -> BackendDataResponse:
        """사고/위반 요약 통계를 조회합니다."""
        if not self.is_configured:
            logger.debug("Backend URL not configured, returning mock incident overview")
            return self._mock_incident_overview(filters)

        endpoint = f"{self._base_url}{BACKEND_INCIDENT_OVERVIEW_PATH}"
        params = filters or {}

        return await self._get_request(endpoint, params)

    async def get_incident_detail(
        self,
        incident_id: str,
    ) -> BackendDataResponse:
        """특정 사건 상세 요약을 조회합니다."""
        if not self.is_configured:
            logger.debug("Backend URL not configured, returning mock incident detail")
            return self._mock_incident_detail(incident_id)

        endpoint_path = BACKEND_INCIDENT_DETAIL_PATH.format(incident_id=incident_id)
        endpoint = f"{self._base_url}{endpoint_path}"

        return await self._get_request(endpoint, {})

    async def get_report_guide(
        self,
        incident_type: Optional[str] = None,
    ) -> BackendDataResponse:
        """신고 플로우 안내 정보를 조회합니다."""
        if not self.is_configured:
            logger.debug("Backend URL not configured, returning mock report guide")
            return self._mock_report_guide(incident_type)

        endpoint = f"{self._base_url}{BACKEND_REPORT_GUIDE_PATH}"
        params: Dict[str, Any] = {}
        if incident_type:
            params["type"] = incident_type

        return await self._get_request(endpoint, params)

    async def _get_request(
        self,
        endpoint: str,
        params: Dict[str, Any],
        raise_on_error: bool = False,
    ) -> BackendDataResponse:
        """GET 요청을 수행합니다."""
        start_time = time.perf_counter()

        try:
            client = get_async_http_client()

            response = await retry_async_operation(
                client.get,
                endpoint,
                params=params if params else None,
                headers=self._get_bearer_headers(),
                timeout=self._timeout,
                config=BACKEND_RETRY_CONFIG,
                operation_name="backend_data_request",
            )

            self._last_latency_ms = int((time.perf_counter() - start_time) * 1000)

            if response.status_code == 200:
                try:
                    data = response.json()
                    return BackendDataResponse(success=True, data=data)
                except Exception:
                    error_msg = "Invalid JSON response from backend"
                    if raise_on_error:
                        raise UpstreamServiceError(
                            service=ServiceType.BACKEND,
                            error_type=ErrorType.UPSTREAM_ERROR,
                            message=error_msg,
                        )
                    return BackendDataResponse(success=False, error_message=error_msg)
            else:
                logger.warning(
                    f"Backend data request failed: status={response.status_code}, "
                    f"endpoint={endpoint}"
                )
                error_msg = f"HTTP {response.status_code}"
                if raise_on_error:
                    raise UpstreamServiceError(
                        service=ServiceType.BACKEND,
                        error_type=ErrorType.UPSTREAM_ERROR,
                        message=error_msg,
                        status_code=response.status_code,
                    )
                return BackendDataResponse(success=False, error_message=error_msg)

        except UpstreamServiceError:
            self._last_latency_ms = int((time.perf_counter() - start_time) * 1000)
            raise

        except httpx.TimeoutException as e:
            self._last_latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.warning(f"Backend data request timeout after {self._timeout}s")
            if raise_on_error:
                raise UpstreamServiceError(
                    service=ServiceType.BACKEND,
                    error_type=ErrorType.UPSTREAM_TIMEOUT,
                    message=f"Backend timeout after {self._timeout}s",
                    is_timeout=True,
                    original_error=e,
                )
            return BackendDataResponse(
                success=False,
                error_message=f"Timeout after {self._timeout}s",
            )

        except Exception as e:
            self._last_latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.warning(f"Backend data request error: {e}")
            if raise_on_error:
                raise UpstreamServiceError(
                    service=ServiceType.BACKEND,
                    error_type=ErrorType.UPSTREAM_ERROR,
                    message=f"Backend error: {type(e).__name__}",
                    original_error=e,
                )
            return BackendDataResponse(success=False, error_message=str(e))

    # =========================================================================
    # 콜백 알림 (스크립트/영상 완료)
    # =========================================================================

    async def notify_script_complete(
        self,
        material_id: str,
        script_id: str,
        script: str,
        version: int = 1,
    ) -> ScriptCompleteResponse:
        """스크립트 생성 완료를 백엔드에 알립니다."""
        if not self._base_url:
            logger.warning(
                "BACKEND_BASE_URL not configured, skipping script complete callback"
            )
            return ScriptCompleteResponse(
                success=False,
                message="BACKEND_BASE_URL not configured",
            )

        url = f"{self._base_url}{BACKEND_SCRIPT_COMPLETE_PATH}"
        headers = self._get_internal_headers()

        request_body = ScriptCompleteRequest(
            materialId=material_id,
            scriptId=script_id,
            script=script,
            version=version,
        )

        logger.info(
            f"Sending script complete callback: "
            f"material_id={material_id}, script_id={script_id}, version={version}"
        )

        try:
            if self._external_client:
                response = await self._external_client.post(
                    url,
                    headers=headers,
                    json=request_body.model_dump(),
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=request_body.model_dump(),
                        timeout=self._timeout,
                    )

            if response.status_code == 401:
                raise ScriptCompleteCallbackError(
                    status_code=401,
                    message="Unauthorized - invalid or missing token",
                    error_code="CALLBACK_UNAUTHORIZED",
                )
            elif response.status_code == 403:
                raise ScriptCompleteCallbackError(
                    status_code=403,
                    message="Forbidden",
                    error_code="CALLBACK_FORBIDDEN",
                )
            elif response.status_code == 404:
                raise ScriptCompleteCallbackError(
                    status_code=404,
                    message="Material or script not found on backend",
                    error_code="CALLBACK_NOT_FOUND",
                )
            elif response.status_code >= 500:
                raise ScriptCompleteCallbackError(
                    status_code=response.status_code,
                    message=f"Backend server error: {response.text[:200]}",
                    error_code="CALLBACK_SERVER_ERROR",
                )
            elif response.status_code not in (200, 201, 204):
                raise ScriptCompleteCallbackError(
                    status_code=response.status_code,
                    message=f"Unexpected status: {response.text[:200]}",
                    error_code="CALLBACK_FAILED",
                )

            logger.info(
                f"Script complete callback succeeded: "
                f"material_id={material_id}, script_id={script_id}"
            )

            if response.status_code == 204 or not response.text.strip():
                return ScriptCompleteResponse(success=True)

            try:
                data = response.json()
                return ScriptCompleteResponse(**data) if data else ScriptCompleteResponse(success=True)
            except Exception:
                return ScriptCompleteResponse(success=True)

        except ScriptCompleteCallbackError:
            raise
        except httpx.TimeoutException as e:
            logger.error(
                f"Script complete callback timeout: "
                f"material_id={material_id}, error={e}"
            )
            raise ScriptCompleteCallbackError(
                status_code=0,
                message=f"Timeout after {self._timeout}s",
                error_code="CALLBACK_TIMEOUT",
            )
        except httpx.RequestError as e:
            logger.error(
                f"Script complete callback network error: "
                f"material_id={material_id}, error={e}"
            )
            raise ScriptCompleteCallbackError(
                status_code=0,
                message=f"Network error: {str(e)[:200]}",
                error_code="CALLBACK_NETWORK_ERROR",
            )
        except Exception as e:
            logger.error(
                f"Script complete callback unexpected error: "
                f"material_id={material_id}, error={e}"
            )
            raise ScriptCompleteCallbackError(
                status_code=0,
                message=f"Unexpected error: {str(e)[:200]}",
                error_code="CALLBACK_FAILED",
            )

    async def notify_job_complete(
        self,
        job_id: str,
        video_url: str,
        duration: int,
        status: str = "COMPLETED",
    ) -> JobCompleteResponse:
        """영상 생성 완료를 백엔드에 알립니다."""
        if not self._base_url:
            logger.warning(
                "BACKEND_BASE_URL not configured, skipping job complete callback"
            )
            return JobCompleteResponse(saved=False)

        url = f"{self._base_url}/video/job/{job_id}/complete"
        headers = self._get_internal_headers()

        request_body = JobCompleteRequest(
            jobId=job_id,
            videoUrl=video_url,
            duration=duration,
            status=status,
        )

        logger.info(
            f"Sending job complete callback: "
            f"job_id={job_id}, status={status}, duration={duration}"
        )

        try:
            if self._external_client:
                response = await self._external_client.post(
                    url,
                    headers=headers,
                    json=request_body.model_dump(),
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=request_body.model_dump(),
                        timeout=self._timeout,
                    )

            if response.status_code == 401:
                raise JobCompleteCallbackError(
                    job_id=job_id,
                    status_code=401,
                    message="Unauthorized - invalid or missing token",
                    error_code="CALLBACK_UNAUTHORIZED",
                )
            elif response.status_code == 403:
                raise JobCompleteCallbackError(
                    job_id=job_id,
                    status_code=403,
                    message="Forbidden",
                    error_code="CALLBACK_FORBIDDEN",
                )
            elif response.status_code == 404:
                raise JobCompleteCallbackError(
                    job_id=job_id,
                    status_code=404,
                    message="Job not found on backend",
                    error_code="CALLBACK_NOT_FOUND",
                )
            elif response.status_code >= 500:
                raise JobCompleteCallbackError(
                    job_id=job_id,
                    status_code=response.status_code,
                    message=f"Backend server error: {response.text[:200]}",
                    error_code="CALLBACK_SERVER_ERROR",
                )
            elif response.status_code not in (200, 201, 204):
                raise JobCompleteCallbackError(
                    job_id=job_id,
                    status_code=response.status_code,
                    message=f"Unexpected status: {response.text[:200]}",
                    error_code="CALLBACK_FAILED",
                )

            logger.info(
                f"Job complete callback succeeded: job_id={job_id}, status={status}"
            )

            if response.status_code == 204 or not response.text.strip():
                return JobCompleteResponse(saved=True)

            try:
                data = response.json()
                return JobCompleteResponse(**data) if data else JobCompleteResponse(saved=True)
            except Exception:
                return JobCompleteResponse(saved=True)

        except JobCompleteCallbackError:
            raise
        except httpx.TimeoutException as e:
            logger.error(
                f"Job complete callback timeout: job_id={job_id}, error={e}"
            )
            raise JobCompleteCallbackError(
                job_id=job_id,
                status_code=0,
                message=f"Timeout after {self._timeout}s",
                error_code="CALLBACK_TIMEOUT",
            )
        except httpx.RequestError as e:
            logger.error(
                f"Job complete callback network error: job_id={job_id}, error={e}"
            )
            raise JobCompleteCallbackError(
                job_id=job_id,
                status_code=0,
                message=f"Network error: {str(e)[:200]}",
                error_code="CALLBACK_NETWORK_ERROR",
            )
        except Exception as e:
            logger.error(
                f"Job complete callback unexpected error: job_id={job_id}, error={e}"
            )
            raise JobCompleteCallbackError(
                job_id=job_id,
                status_code=0,
                message=f"Unexpected error: {str(e)[:200]}",
                error_code="CALLBACK_FAILED",
            )

    # =========================================================================
    # Render-spec 조회
    # =========================================================================

    async def get_render_spec(self, script_id: str):
        """스크립트의 render-spec을 조회합니다.

        Args:
            script_id: 스크립트 ID

        Returns:
            RenderSpec: 렌더 스펙

        Raises:
            ScriptFetchError: 조회 실패 시
            EmptyRenderSpecError: 씬이 비어있을 때
        """
        # Import here to avoid circular dependency
        from app.models.render_spec import RenderSpecResponse

        if not self._base_url:
            raise ScriptFetchError(
                script_id=script_id,
                status_code=0,
                message="BACKEND_BASE_URL not configured",
                error_code="BACKEND_NOT_CONFIGURED",
            )

        url = f"{self._base_url}/internal/scripts/{script_id}/render-spec"
        headers = self._get_internal_headers()

        logger.info(f"Fetching render-spec: script_id={script_id}")

        try:
            if self._external_client:
                response = await self._external_client.get(
                    url,
                    headers=headers,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        url,
                        headers=headers,
                        timeout=self._timeout,
                    )

            if response.status_code == 401:
                raise ScriptFetchError(
                    script_id=script_id,
                    status_code=401,
                    message="Unauthorized - invalid or missing token",
                    error_code="SCRIPT_FETCH_UNAUTHORIZED",
                )
            elif response.status_code == 403:
                raise ScriptFetchError(
                    script_id=script_id,
                    status_code=403,
                    message="Forbidden",
                    error_code="SCRIPT_FETCH_FORBIDDEN",
                )
            elif response.status_code == 404:
                raise ScriptFetchError(
                    script_id=script_id,
                    status_code=404,
                    message="Script not found",
                    error_code="SCRIPT_NOT_FOUND",
                )
            elif response.status_code >= 500:
                raise ScriptFetchError(
                    script_id=script_id,
                    status_code=response.status_code,
                    message=f"Backend server error: {response.text[:200]}",
                    error_code="SCRIPT_FETCH_SERVER_ERROR",
                )
            elif response.status_code != 200:
                raise ScriptFetchError(
                    script_id=script_id,
                    status_code=response.status_code,
                    message=f"Unexpected status: {response.text[:200]}",
                    error_code="SCRIPT_FETCH_FAILED",
                )

            data = response.json()
            spec_response = RenderSpecResponse(**data)
            spec = spec_response.to_render_spec()

            if spec.is_empty():
                raise EmptyRenderSpecError(script_id=script_id)

            logger.info(
                f"Render-spec fetched: script_id={script_id}, "
                f"scenes={spec.get_scene_count()}"
            )

            return spec

        except ScriptFetchError:
            raise
        except EmptyRenderSpecError:
            raise
        except httpx.TimeoutException as e:
            logger.error(f"Render-spec fetch timeout: script_id={script_id}, error={e}")
            raise ScriptFetchError(
                script_id=script_id,
                status_code=0,
                message=f"Timeout after {self._timeout}s",
                error_code="SCRIPT_FETCH_TIMEOUT",
            )
        except httpx.RequestError as e:
            logger.error(f"Render-spec fetch network error: script_id={script_id}, error={e}")
            raise ScriptFetchError(
                script_id=script_id,
                status_code=0,
                message=f"Network error: {str(e)[:200]}",
                error_code="SCRIPT_FETCH_NETWORK_ERROR",
            )
        except Exception as e:
            logger.error(f"Render-spec fetch error: script_id={script_id}, error={e}")
            raise ScriptFetchError(
                script_id=script_id,
                status_code=0,
                message=f"Unexpected error: {str(e)[:200]}",
                error_code="SCRIPT_FETCH_FAILED",
            )

    # =========================================================================
    # SourceSet 오케스트레이션 (FastAPI → Spring)
    # =========================================================================

    async def get_source_set_documents(self, source_set_id: str):
        """소스셋의 문서 목록을 조회합니다.

        GET /internal/source-sets/{sourceSetId}/documents

        Args:
            source_set_id: 소스셋 ID

        Returns:
            SourceSetDocumentsResponse: 문서 목록

        Raises:
            SourceSetDocumentsFetchError: 조회 실패 시
        """
        from app.models.source_set import SourceSetDocumentsResponse

        if not self._base_url:
            raise SourceSetDocumentsFetchError(
                source_set_id=source_set_id,
                status_code=0,
                message="BACKEND_BASE_URL not configured",
                error_code="BACKEND_NOT_CONFIGURED",
            )

        url = f"{self._base_url}/internal/source-sets/{source_set_id}/documents"
        headers = self._get_internal_headers()

        logger.info(f"Fetching source-set documents: source_set_id={source_set_id}")

        try:
            if self._external_client:
                response = await self._external_client.get(
                    url,
                    headers=headers,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        url,
                        headers=headers,
                        timeout=self._timeout,
                    )

            if response.status_code == 401:
                raise SourceSetDocumentsFetchError(
                    source_set_id=source_set_id,
                    status_code=401,
                    message="Unauthorized - invalid or missing token",
                    error_code="SOURCE_SET_DOCS_UNAUTHORIZED",
                )
            elif response.status_code == 403:
                raise SourceSetDocumentsFetchError(
                    source_set_id=source_set_id,
                    status_code=403,
                    message="Forbidden",
                    error_code="SOURCE_SET_DOCS_FORBIDDEN",
                )
            elif response.status_code == 404:
                raise SourceSetDocumentsFetchError(
                    source_set_id=source_set_id,
                    status_code=404,
                    message="SourceSet not found",
                    error_code="SOURCE_SET_NOT_FOUND",
                )
            elif response.status_code >= 500:
                raise SourceSetDocumentsFetchError(
                    source_set_id=source_set_id,
                    status_code=response.status_code,
                    message=f"Backend server error: {response.text[:200]}",
                    error_code="SOURCE_SET_DOCS_SERVER_ERROR",
                )
            elif response.status_code != 200:
                raise SourceSetDocumentsFetchError(
                    source_set_id=source_set_id,
                    status_code=response.status_code,
                    message=f"Unexpected status: {response.text[:200]}",
                    error_code="SOURCE_SET_DOCS_FETCH_FAILED",
                )

            data = response.json()
            result = SourceSetDocumentsResponse(**data)

            logger.info(
                f"Source-set documents fetched: source_set_id={source_set_id}, "
                f"count={len(result.documents)}"
            )

            return result

        except SourceSetDocumentsFetchError:
            raise
        except httpx.TimeoutException as e:
            logger.error(f"Source-set documents fetch timeout: source_set_id={source_set_id}")
            raise SourceSetDocumentsFetchError(
                source_set_id=source_set_id,
                status_code=0,
                message=f"Timeout after {self._timeout}s",
                error_code="SOURCE_SET_DOCS_TIMEOUT",
            )
        except httpx.RequestError as e:
            logger.error(f"Source-set documents fetch network error: source_set_id={source_set_id}")
            raise SourceSetDocumentsFetchError(
                source_set_id=source_set_id,
                status_code=0,
                message=f"Network error: {str(e)[:200]}",
                error_code="SOURCE_SET_DOCS_NETWORK_ERROR",
            )
        except Exception as e:
            logger.error(f"Source-set documents fetch error: source_set_id={source_set_id}, error={e}")
            raise SourceSetDocumentsFetchError(
                source_set_id=source_set_id,
                status_code=0,
                message=f"Unexpected error: {str(e)[:200]}",
                error_code="SOURCE_SET_DOCS_FETCH_FAILED",
            )

    async def notify_source_set_complete(
        self,
        source_set_id: str,
        request,  # SourceSetCompleteRequest
    ):
        """소스셋 완료 콜백을 백엔드에 전송합니다.

        POST /internal/callbacks/source-sets/{sourceSetId}/complete

        Args:
            source_set_id: 소스셋 ID
            request: SourceSetCompleteRequest

        Returns:
            SourceSetCompleteResponse

        Raises:
            SourceSetCompleteCallbackError: 콜백 실패 시
        """
        from app.models.source_set import SourceSetCompleteResponse

        if not self._base_url:
            raise SourceSetCompleteCallbackError(
                source_set_id=source_set_id,
                status_code=0,
                message="BACKEND_BASE_URL not configured",
                error_code="BACKEND_NOT_CONFIGURED",
            )

        url = f"{self._base_url}/internal/callbacks/source-sets/{source_set_id}/complete"
        headers = self._get_internal_headers()

        logger.info(
            f"Sending source-set complete callback: "
            f"source_set_id={source_set_id}, status={request.status}"
        )

        try:
            payload = request.model_dump(by_alias=True, exclude_none=True)

            if self._external_client:
                response = await self._external_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=self._timeout,
                    )

            if response.status_code == 401:
                raise SourceSetCompleteCallbackError(
                    source_set_id=source_set_id,
                    status_code=401,
                    message="Unauthorized - invalid or missing token",
                    error_code="SOURCE_SET_COMPLETE_UNAUTHORIZED",
                )
            elif response.status_code == 403:
                raise SourceSetCompleteCallbackError(
                    source_set_id=source_set_id,
                    status_code=403,
                    message="Forbidden",
                    error_code="SOURCE_SET_COMPLETE_FORBIDDEN",
                )
            elif response.status_code == 404:
                raise SourceSetCompleteCallbackError(
                    source_set_id=source_set_id,
                    status_code=404,
                    message="SourceSet not found on backend",
                    error_code="SOURCE_SET_NOT_FOUND",
                )
            elif response.status_code >= 500:
                raise SourceSetCompleteCallbackError(
                    source_set_id=source_set_id,
                    status_code=response.status_code,
                    message=f"Backend server error: {response.text[:200]}",
                    error_code="SOURCE_SET_COMPLETE_SERVER_ERROR",
                )
            elif response.status_code not in (200, 201, 204):
                raise SourceSetCompleteCallbackError(
                    source_set_id=source_set_id,
                    status_code=response.status_code,
                    message=f"Unexpected status: {response.text[:200]}",
                    error_code="SOURCE_SET_COMPLETE_FAILED",
                )

            logger.info(
                f"Source-set complete callback succeeded: "
                f"source_set_id={source_set_id}"
            )

            if response.status_code == 204 or not response.text.strip():
                return SourceSetCompleteResponse(saved=True)

            try:
                data = response.json()
                return SourceSetCompleteResponse(**data) if data else SourceSetCompleteResponse(saved=True)
            except Exception:
                return SourceSetCompleteResponse(saved=True)

        except SourceSetCompleteCallbackError:
            raise
        except httpx.TimeoutException as e:
            logger.error(f"Source-set complete callback timeout: source_set_id={source_set_id}")
            raise SourceSetCompleteCallbackError(
                source_set_id=source_set_id,
                status_code=0,
                message=f"Timeout after {self._timeout}s",
                error_code="SOURCE_SET_COMPLETE_TIMEOUT",
            )
        except httpx.RequestError as e:
            logger.error(f"Source-set complete callback network error: source_set_id={source_set_id}")
            raise SourceSetCompleteCallbackError(
                source_set_id=source_set_id,
                status_code=0,
                message=f"Network error: {str(e)[:200]}",
                error_code="SOURCE_SET_COMPLETE_NETWORK_ERROR",
            )
        except Exception as e:
            logger.error(f"Source-set complete callback error: source_set_id={source_set_id}, error={e}")
            raise SourceSetCompleteCallbackError(
                source_set_id=source_set_id,
                status_code=0,
                message=f"Unexpected error: {str(e)[:200]}",
                error_code="SOURCE_SET_COMPLETE_FAILED",
            )

    async def bulk_upsert_chunks(
        self,
        document_id: str,
        request,  # ChunkBulkUpsertRequest
    ):
        """문서 청크를 백엔드 DB에 벌크 업서트합니다.

        POST /internal/rag/documents/{documentId}/chunks:bulk

        Args:
            document_id: 문서 ID
            request: ChunkBulkUpsertRequest

        Returns:
            ChunkBulkUpsertResponse

        Raises:
            ChunkBulkUpsertError: 업서트 실패 시
        """
        from app.models.source_set import ChunkBulkUpsertResponse

        if not self._base_url:
            raise ChunkBulkUpsertError(
                document_id=document_id,
                status_code=0,
                message="BACKEND_BASE_URL not configured",
                error_code="BACKEND_NOT_CONFIGURED",
            )

        url = f"{self._base_url}/internal/rag/documents/{document_id}/chunks:bulk"
        headers = self._get_internal_headers()

        logger.info(
            f"Bulk upserting chunks: document_id={document_id}, "
            f"count={len(request.chunks)}"
        )

        try:
            payload = request.model_dump(by_alias=True, exclude_none=True)

            if self._external_client:
                response = await self._external_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=self._timeout,
                    )

            if response.status_code == 401:
                raise ChunkBulkUpsertError(
                    document_id=document_id,
                    status_code=401,
                    message="Unauthorized - invalid or missing token",
                    error_code="CHUNK_UPSERT_UNAUTHORIZED",
                )
            elif response.status_code == 403:
                raise ChunkBulkUpsertError(
                    document_id=document_id,
                    status_code=403,
                    message="Forbidden",
                    error_code="CHUNK_UPSERT_FORBIDDEN",
                )
            elif response.status_code == 404:
                raise ChunkBulkUpsertError(
                    document_id=document_id,
                    status_code=404,
                    message="Document not found on backend",
                    error_code="DOCUMENT_NOT_FOUND",
                )
            elif response.status_code >= 500:
                raise ChunkBulkUpsertError(
                    document_id=document_id,
                    status_code=response.status_code,
                    message=f"Backend server error: {response.text[:200]}",
                    error_code="CHUNK_UPSERT_SERVER_ERROR",
                )
            elif response.status_code not in (200, 201, 204):
                raise ChunkBulkUpsertError(
                    document_id=document_id,
                    status_code=response.status_code,
                    message=f"Unexpected status: {response.text[:200]}",
                    error_code="CHUNK_UPSERT_FAILED",
                )

            logger.info(
                f"Chunks bulk upsert succeeded: document_id={document_id}, "
                f"count={len(request.chunks)}"
            )

            if response.status_code == 204 or not response.text.strip():
                return ChunkBulkUpsertResponse(saved=True, saved_count=len(request.chunks))

            try:
                data = response.json()
                return ChunkBulkUpsertResponse(**data) if data else ChunkBulkUpsertResponse(
                    saved=True, saved_count=len(request.chunks)
                )
            except Exception:
                return ChunkBulkUpsertResponse(saved=True, saved_count=len(request.chunks))

        except ChunkBulkUpsertError:
            raise
        except httpx.TimeoutException as e:
            logger.error(f"Chunks bulk upsert timeout: document_id={document_id}")
            raise ChunkBulkUpsertError(
                document_id=document_id,
                status_code=0,
                message=f"Timeout after {self._timeout}s",
                error_code="CHUNK_UPSERT_TIMEOUT",
            )
        except httpx.RequestError as e:
            logger.error(f"Chunks bulk upsert network error: document_id={document_id}")
            raise ChunkBulkUpsertError(
                document_id=document_id,
                status_code=0,
                message=f"Network error: {str(e)[:200]}",
                error_code="CHUNK_UPSERT_NETWORK_ERROR",
            )
        except Exception as e:
            logger.error(f"Chunks bulk upsert error: document_id={document_id}, error={e}")
            raise ChunkBulkUpsertError(
                document_id=document_id,
                status_code=0,
                message=f"Unexpected error: {str(e)[:200]}",
                error_code="CHUNK_UPSERT_FAILED",
            )

    async def bulk_upsert_fail_chunks(
        self,
        document_id: str,
        request,  # FailChunkBulkUpsertRequest
    ):
        """임베딩 실패 로그를 백엔드 DB에 벌크 업서트합니다.

        POST /internal/rag/documents/{documentId}/fail-chunks:bulk

        Args:
            document_id: 문서 ID
            request: FailChunkBulkUpsertRequest

        Returns:
            FailChunkBulkUpsertResponse

        Raises:
            FailChunkBulkUpsertError: 업서트 실패 시
        """
        from app.models.source_set import FailChunkBulkUpsertResponse

        if not self._base_url:
            raise FailChunkBulkUpsertError(
                document_id=document_id,
                status_code=0,
                message="BACKEND_BASE_URL not configured",
                error_code="BACKEND_NOT_CONFIGURED",
            )

        url = f"{self._base_url}/internal/rag/documents/{document_id}/fail-chunks:bulk"
        headers = self._get_internal_headers()

        logger.info(
            f"Bulk upserting fail chunks: document_id={document_id}, "
            f"count={len(request.fails)}"
        )

        try:
            payload = request.model_dump(by_alias=True, exclude_none=True)

            if self._external_client:
                response = await self._external_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=self._timeout,
                    )

            if response.status_code == 401:
                raise FailChunkBulkUpsertError(
                    document_id=document_id,
                    status_code=401,
                    message="Unauthorized - invalid or missing token",
                    error_code="FAIL_CHUNK_UPSERT_UNAUTHORIZED",
                )
            elif response.status_code == 403:
                raise FailChunkBulkUpsertError(
                    document_id=document_id,
                    status_code=403,
                    message="Forbidden",
                    error_code="FAIL_CHUNK_UPSERT_FORBIDDEN",
                )
            elif response.status_code == 404:
                raise FailChunkBulkUpsertError(
                    document_id=document_id,
                    status_code=404,
                    message="Document not found on backend",
                    error_code="DOCUMENT_NOT_FOUND",
                )
            elif response.status_code >= 500:
                raise FailChunkBulkUpsertError(
                    document_id=document_id,
                    status_code=response.status_code,
                    message=f"Backend server error: {response.text[:200]}",
                    error_code="FAIL_CHUNK_UPSERT_SERVER_ERROR",
                )
            elif response.status_code not in (200, 201, 204):
                raise FailChunkBulkUpsertError(
                    document_id=document_id,
                    status_code=response.status_code,
                    message=f"Unexpected status: {response.text[:200]}",
                    error_code="FAIL_CHUNK_UPSERT_FAILED",
                )

            logger.info(
                f"Fail chunks bulk upsert succeeded: document_id={document_id}, "
                f"count={len(request.fails)}"
            )

            if response.status_code == 204 or not response.text.strip():
                return FailChunkBulkUpsertResponse(saved=True, saved_count=len(request.fails))

            try:
                data = response.json()
                return FailChunkBulkUpsertResponse(**data) if data else FailChunkBulkUpsertResponse(
                    saved=True, saved_count=len(request.fails)
                )
            except Exception:
                return FailChunkBulkUpsertResponse(saved=True, saved_count=len(request.fails))

        except FailChunkBulkUpsertError:
            raise
        except httpx.TimeoutException as e:
            logger.error(f"Fail chunks bulk upsert timeout: document_id={document_id}")
            raise FailChunkBulkUpsertError(
                document_id=document_id,
                status_code=0,
                message=f"Timeout after {self._timeout}s",
                error_code="FAIL_CHUNK_UPSERT_TIMEOUT",
            )
        except httpx.RequestError as e:
            logger.error(f"Fail chunks bulk upsert network error: document_id={document_id}")
            raise FailChunkBulkUpsertError(
                document_id=document_id,
                status_code=0,
                message=f"Network error: {str(e)[:200]}",
                error_code="FAIL_CHUNK_UPSERT_NETWORK_ERROR",
            )
        except Exception as e:
            logger.error(f"Fail chunks bulk upsert error: document_id={document_id}, error={e}")
            raise FailChunkBulkUpsertError(
                document_id=document_id,
                status_code=0,
                message=f"Unexpected error: {str(e)[:200]}",
                error_code="FAIL_CHUNK_UPSERT_FAILED",
            )

    # =========================================================================
    # RAG 문서 상태 업데이트 (POLICY ingest 콜백 처리)
    # =========================================================================

    async def update_rag_document_status(
        self,
        rag_document_pk: str,
        status: str,
        document_id: str,
        version: int,
        processed_at: Optional[str] = None,
        fail_reason: Optional[str] = None,
    ) -> bool:
        """RAG 문서 상태를 백엔드에 업데이트합니다.

        PATCH /internal/rag/documents/{ragDocumentPk}/status

        Args:
            rag_document_pk: RAG 문서 PK (UUID)
            status: 상태 (COMPLETED|FAILED)
            document_id: 문서 ID
            version: 문서 버전
            processed_at: 처리 완료 시간 (ISO-8601)
            fail_reason: 실패 사유 (status=FAILED인 경우)

        Returns:
            bool: 업데이트 성공 여부

        Raises:
            RAGDocumentStatusUpdateError: 업데이트 실패 시
        """
        if not self._base_url:
            logger.warning(
                "BACKEND_BASE_URL not configured, skipping RAG document status update"
            )
            return False

        url = f"{self._base_url}/internal/rag/documents/{rag_document_pk}/status"
        headers = self._get_internal_headers()

        payload: Dict[str, Any] = {
            "status": status,
            "documentId": document_id,
            "version": version,
        }
        if processed_at:
            payload["processedAt"] = processed_at
        if fail_reason:
            payload["failReason"] = fail_reason

        logger.info(
            f"Updating RAG document status: rag_document_pk={rag_document_pk}, "
            f"status={status}, document_id={document_id}, version={version}"
        )

        try:
            if self._external_client:
                response = await self._external_client.patch(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.patch(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=self._timeout,
                    )

            if response.status_code == 401:
                raise RAGDocumentStatusUpdateError(
                    rag_document_pk=rag_document_pk,
                    status_code=401,
                    message="Unauthorized - invalid or missing token",
                    error_code="RAG_STATUS_UPDATE_UNAUTHORIZED",
                )
            elif response.status_code == 403:
                raise RAGDocumentStatusUpdateError(
                    rag_document_pk=rag_document_pk,
                    status_code=403,
                    message="Forbidden",
                    error_code="RAG_STATUS_UPDATE_FORBIDDEN",
                )
            elif response.status_code == 404:
                raise RAGDocumentStatusUpdateError(
                    rag_document_pk=rag_document_pk,
                    status_code=404,
                    message="RAG document not found on backend",
                    error_code="RAG_DOCUMENT_NOT_FOUND",
                )
            elif response.status_code >= 500:
                raise RAGDocumentStatusUpdateError(
                    rag_document_pk=rag_document_pk,
                    status_code=response.status_code,
                    message=f"Backend server error: {response.text[:200]}",
                    error_code="RAG_STATUS_UPDATE_SERVER_ERROR",
                )
            elif response.status_code not in (200, 204):
                raise RAGDocumentStatusUpdateError(
                    rag_document_pk=rag_document_pk,
                    status_code=response.status_code,
                    message=f"Unexpected status: {response.text[:200]}",
                    error_code="RAG_STATUS_UPDATE_FAILED",
                )

            logger.info(
                f"RAG document status updated: rag_document_pk={rag_document_pk}, "
                f"status={status}"
            )
            return True

        except RAGDocumentStatusUpdateError:
            raise
        except httpx.TimeoutException:
            logger.error(
                f"RAG document status update timeout: rag_document_pk={rag_document_pk}"
            )
            raise RAGDocumentStatusUpdateError(
                rag_document_pk=rag_document_pk,
                status_code=0,
                message=f"Timeout after {self._timeout}s",
                error_code="RAG_STATUS_UPDATE_TIMEOUT",
            )
        except httpx.RequestError as e:
            logger.error(
                f"RAG document status update network error: "
                f"rag_document_pk={rag_document_pk}, error={e}"
            )
            raise RAGDocumentStatusUpdateError(
                rag_document_pk=rag_document_pk,
                status_code=0,
                message=f"Network error: {str(e)[:200]}",
                error_code="RAG_STATUS_UPDATE_NETWORK_ERROR",
            )
        except Exception as e:
            logger.error(
                f"RAG document status update error: "
                f"rag_document_pk={rag_document_pk}, error={e}"
            )
            raise RAGDocumentStatusUpdateError(
                rag_document_pk=rag_document_pk,
                status_code=0,
                message=f"Unexpected error: {str(e)[:200]}",
                error_code="RAG_STATUS_UPDATE_FAILED",
            )

    # =========================================================================
    # Mock 응답 (백엔드 미연동 시)
    # =========================================================================

    def _mock_employee_edu_status(self, user_id: str) -> BackendDataResponse:
        return BackendDataResponse(
            success=True,
            data={
                "user_id": user_id,
                "total_required": 4,
                "completed": 3,
                "pending": 1,
                "courses": [
                    {"name": "정보보호교육", "status": "completed", "completed_at": "2025-03-15"},
                    {"name": "개인정보보호교육", "status": "completed", "completed_at": "2025-04-20"},
                    {"name": "직장 내 괴롭힘 방지", "status": "completed", "completed_at": "2025-05-10"},
                    {"name": "산업안전보건", "status": "pending", "deadline": "2025-12-31"},
                ],
                "next_deadline": "2025-12-31",
            },
        )

    def _mock_department_edu_stats(self, department_id: Optional[str]) -> BackendDataResponse:
        return BackendDataResponse(
            success=True,
            data={
                "department_id": department_id or "all",
                "department_name": "전체" if not department_id else "개발팀",
                "total_employees": 50,
                "completion_rate": 85.0,
                "by_course": [
                    {"name": "정보보호교육", "completed": 45, "pending": 5},
                    {"name": "개인정보보호교육", "completed": 42, "pending": 8},
                    {"name": "직장 내 괴롭힘 방지", "completed": 48, "pending": 2},
                    {"name": "산업안전보건", "completed": 40, "pending": 10},
                ],
                "pending_count": 15,
            },
        )

    def _mock_incident_overview(self, filters: Optional[Dict[str, Any]]) -> BackendDataResponse:
        return BackendDataResponse(
            success=True,
            data={
                "period": "2025-Q4",
                "total_incidents": 15,
                "by_status": {"open": 3, "in_progress": 5, "closed": 7},
                "by_type": {"security": 8, "privacy": 5, "compliance": 2},
                "trend": {"previous_period": 12, "change_rate": 25.0},
            },
        )

    def _mock_incident_detail(self, incident_id: str) -> BackendDataResponse:
        return BackendDataResponse(
            success=True,
            data={
                "incident_id": incident_id,
                "type": "security",
                "status": "in_progress",
                "reported_at": "2025-10-15T09:30:00Z",
                "summary": "외부 이메일로 내부 문서 전송 건",
                "severity": "medium",
                "assigned_to": "보안팀",
                "related_policies": ["정보보안정책 제3조", "개인정보처리방침 제5조"],
            },
        )

    def _mock_report_guide(self, incident_type: Optional[str]) -> BackendDataResponse:
        guide_type = incident_type or "general"
        return BackendDataResponse(
            success=True,
            data={
                "guide_type": guide_type,
                "title": f"{guide_type.upper()} 사고 신고 안내",
                "steps": [
                    "1. 사고 발생 일시 및 장소 확인",
                    "2. 관련 증거 자료 수집 (스크린샷, 로그 등)",
                    "3. 공식 신고 채널을 통해 접수",
                    "4. 신고 접수 번호 수령 후 보관",
                ],
                "official_channels": [
                    {"name": "보안팀 직통", "contact": "security@company.com"},
                    {"name": "신고 포털", "url": "https://report.company.com"},
                ],
                "warnings": [
                    "개인정보(주민번호, 연락처 등)를 신고 내용에 포함하지 마세요.",
                    "증거 자료는 원본을 보존하고 복사본을 제출해 주세요.",
                ],
            },
        )


# =============================================================================
# Singleton Instance & Compatibility Functions
# =============================================================================

_client: Optional[BackendClient] = None


def get_backend_client() -> BackendClient:
    """BackendClient 싱글톤 인스턴스 반환."""
    global _client
    if _client is None:
        _client = BackendClient()
    return _client


def clear_backend_client() -> None:
    """BackendClient 싱글톤 초기화 (테스트용)."""
    global _client
    _client = None


# 하위 호환성을 위한 별칭 (기존 코드 지원)
def get_backend_callback_client() -> BackendClient:
    """하위 호환성: BackendCallbackClient → BackendClient."""
    return get_backend_client()


def clear_backend_callback_client() -> None:
    """하위 호환성: clear_backend_callback_client → clear_backend_client."""
    clear_backend_client()


def get_backend_script_client() -> BackendClient:
    """하위 호환성: BackendScriptClient → BackendClient."""
    return get_backend_client()


def clear_backend_script_client() -> None:
    """하위 호환성: clear_backend_script_client → clear_backend_client."""
    clear_backend_client()


# 하위 호환성: BackendDataClient 클래스 별칭
BackendDataClient = BackendClient
BackendCallbackClient = BackendClient
BackendScriptClient = BackendClient
