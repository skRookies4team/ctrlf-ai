"""
Backend Callback Client

AI 서버에서 백엔드로 콜백을 보내는 클라이언트.

지원 콜백:
- POST /video/script/complete: 스크립트 생성 완료 알림

Usage:
    from app.clients.backend_callback_client import get_backend_callback_client

    client = get_backend_callback_client()
    await client.notify_script_complete(
        material_id="uuid",
        script_id="uuid",
        script="생성된 스크립트 내용...",
        version=1,
    )

Environment Variables:
    BACKEND_BASE_URL: 백엔드 서비스 URL
    BACKEND_INTERNAL_TOKEN: 내부 API 인증 토큰 (X-Internal-Token)
    BACKEND_TIMEOUT_SEC: API 타임아웃 (초)
"""

from typing import Optional

import httpx
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Request/Response Models
# =============================================================================


class ScriptCompleteRequest(BaseModel):
    """스크립트 생성 완료 콜백 요청.

    Attributes:
        materialId: 자료 ID
        scriptId: 생성된 스크립트 ID
        script: LLM이 자동 생성한 스크립트
        version: 스크립트 버전 번호
    """

    materialId: str
    scriptId: str
    script: str
    version: int


class ScriptCompleteResponse(BaseModel):
    """스크립트 생성 완료 콜백 응답.

    백엔드 응답 형식에 따라 확장 가능.
    """

    success: bool = True
    message: Optional[str] = None


# =============================================================================
# Exceptions
# =============================================================================


class CallbackError(Exception):
    """콜백 실패 예외.

    Attributes:
        endpoint: 호출한 엔드포인트
        status_code: HTTP 상태 코드
        message: 에러 메시지
        error_code: 에러 코드
    """

    def __init__(
        self,
        endpoint: str,
        status_code: int,
        message: str,
        error_code: str = "CALLBACK_FAILED",
    ):
        self.endpoint = endpoint
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        super().__init__(
            f"{error_code}: {message} (endpoint={endpoint}, status={status_code})"
        )


class ScriptCompleteCallbackError(CallbackError):
    """스크립트 완료 콜백 실패 예외."""

    def __init__(
        self,
        status_code: int,
        message: str,
        error_code: str = "SCRIPT_COMPLETE_CALLBACK_FAILED",
    ):
        super().__init__(
            endpoint="/video/script/complete",
            status_code=status_code,
            message=message,
            error_code=error_code,
        )


# =============================================================================
# Backend Callback Client
# =============================================================================


class BackendCallbackClient:
    """백엔드 콜백 클라이언트.

    AI 서버에서 백엔드로 콜백(알림)을 보내는 클라이언트입니다.

    Usage:
        client = BackendCallbackClient(
            base_url="http://backend:8080",
            token="internal-token",
            timeout=30.0
        )
        await client.notify_script_complete(
            material_id="uuid",
            script_id="uuid",
            script="생성된 스크립트...",
            version=1,
        )
    """

    # 스크립트 완료 콜백 경로
    SCRIPT_COMPLETE_PATH = "/video/script/complete"

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        """클라이언트 초기화.

        Args:
            base_url: 백엔드 서비스 URL (없으면 settings에서 로드)
            token: 내부 API 인증 토큰
            timeout: API 타임아웃 (초)
            client: 커스텀 httpx 클라이언트 (테스트용)
        """
        settings = get_settings()
        self._base_url = (base_url or settings.backend_base_url or "").rstrip("/")
        self._token = token or settings.BACKEND_INTERNAL_TOKEN
        self._timeout = timeout or settings.BACKEND_TIMEOUT_SEC
        self._external_client = client

    def _get_headers(self) -> dict:
        """API 요청 헤더 생성."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._token:
            headers["X-Internal-Token"] = self._token
        return headers

    async def notify_script_complete(
        self,
        material_id: str,
        script_id: str,
        script: str,
        version: int = 1,
    ) -> ScriptCompleteResponse:
        """스크립트 생성 완료를 백엔드에 알립니다.

        Args:
            material_id: 자료 ID
            script_id: 생성된 스크립트 ID
            script: LLM이 생성한 스크립트 내용
            version: 스크립트 버전 번호 (기본: 1)

        Returns:
            ScriptCompleteResponse: 콜백 응답

        Raises:
            ScriptCompleteCallbackError: 콜백 실패 시
        """
        if not self._base_url:
            logger.warning(
                "BACKEND_BASE_URL not configured, skipping script complete callback"
            )
            return ScriptCompleteResponse(
                success=False,
                message="BACKEND_BASE_URL not configured",
            )

        url = f"{self._base_url}{self.SCRIPT_COMPLETE_PATH}"
        headers = self._get_headers()

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

            # HTTP 에러 처리
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

            # 200/201: JSON 응답 파싱, 204: 빈 응답
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


# =============================================================================
# Singleton Instance
# =============================================================================


_client: Optional[BackendCallbackClient] = None


def get_backend_callback_client() -> BackendCallbackClient:
    """BackendCallbackClient 싱글톤 인스턴스 반환."""
    global _client
    if _client is None:
        _client = BackendCallbackClient()
    return _client


def clear_backend_callback_client() -> None:
    """BackendCallbackClient 싱글톤 초기화 (테스트용)."""
    global _client
    _client = None
