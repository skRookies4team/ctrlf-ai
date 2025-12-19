"""
Phase 38: Backend Script Client

백엔드에서 render-spec을 조회하는 클라이언트.
Job 시작 시 scriptId로 최신 렌더 스펙을 가져옵니다.

Usage:
    from app.clients.backend_script_client import get_backend_script_client

    client = get_backend_script_client()
    spec = await client.get_render_spec("script-uuid")

Environment Variables:
    BACKEND_BASE_URL: 백엔드 서비스 URL
    BACKEND_INTERNAL_TOKEN: 내부 API 인증 토큰 (X-Internal-Token)
    BACKEND_TIMEOUT_SEC: API 타임아웃 (초)
"""

from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.render_spec import RenderSpec, RenderSpecResponse

logger = get_logger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class ScriptFetchError(Exception):
    """스크립트 조회 실패 예외.

    Attributes:
        script_id: 조회 시도한 스크립트 ID
        status_code: HTTP 상태 코드
        message: 에러 메시지
        error_code: 에러 코드 (SCRIPT_FETCH_FAILED 등)
    """

    def __init__(
        self,
        script_id: str,
        status_code: int,
        message: str,
        error_code: str = "SCRIPT_FETCH_FAILED",
    ):
        self.script_id = script_id
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        super().__init__(f"{error_code}: {message} (script_id={script_id}, status={status_code})")


class EmptyRenderSpecError(Exception):
    """빈 render-spec 예외.

    Attributes:
        script_id: 스크립트 ID
    """

    def __init__(self, script_id: str):
        self.script_id = script_id
        self.error_code = "EMPTY_RENDER_SPEC"
        super().__init__(f"EMPTY_RENDER_SPEC: No scenes in render-spec (script_id={script_id})")


# =============================================================================
# Backend Script Client
# =============================================================================


class BackendScriptClient:
    """백엔드 스크립트 조회 클라이언트.

    백엔드 내부 API를 호출하여 render-spec을 조회합니다.

    API Contract:
        GET {BACKEND_BASE_URL}/internal/scripts/{scriptId}/render-spec
        Headers: X-Internal-Token: {BACKEND_INTERNAL_TOKEN}

    Responses:
        200: RenderSpec JSON
        401: Unauthorized (토큰 없음/잘못됨)
        403: Forbidden
        404: Script not found
        5xx: Server error

    Usage:
        client = BackendScriptClient(
            base_url="http://backend:8080",
            token="internal-token",
            timeout=30.0
        )
        spec = await client.get_render_spec("script-uuid")
    """

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

    async def get_render_spec(self, script_id: str) -> RenderSpec:
        """스크립트의 render-spec을 조회합니다.

        Args:
            script_id: 스크립트 ID

        Returns:
            RenderSpec: 렌더 스펙

        Raises:
            ScriptFetchError: 조회 실패 시
            EmptyRenderSpecError: 씬이 비어있을 때
        """
        if not self._base_url:
            raise ScriptFetchError(
                script_id=script_id,
                status_code=0,
                message="BACKEND_BASE_URL not configured",
                error_code="BACKEND_NOT_CONFIGURED",
            )

        url = f"{self._base_url}/internal/scripts/{script_id}/render-spec"
        headers = self._get_headers()

        logger.info(f"Fetching render-spec: script_id={script_id}")

        try:
            if self._external_client:
                # 테스트용 커스텀 클라이언트 사용
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

            # HTTP 에러 처리
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

            # 응답 파싱
            data = response.json()
            spec_response = RenderSpecResponse(**data)
            spec = spec_response.to_render_spec()

            # 빈 render-spec 확인
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


# =============================================================================
# Singleton Instance
# =============================================================================


_client: Optional[BackendScriptClient] = None


def get_backend_script_client() -> BackendScriptClient:
    """BackendScriptClient 싱글톤 인스턴스 반환."""
    global _client
    if _client is None:
        _client = BackendScriptClient()
    return _client


def clear_backend_script_client() -> None:
    """BackendScriptClient 싱글톤 초기화 (테스트용)."""
    global _client
    _client = None
