"""
백엔드 API 클라이언트 모듈 (Backend API Client Module)

ctrlf-back (Spring 백엔드)와 통신하는 HTTP 클라이언트입니다.
AI 로그 전송, 세션 정보 조회 등 백엔드 API 호출을 담당합니다.

Phase 10 업데이트:
- AI 로그 전송 시 camelCase JSON 스키마 사용 (백엔드 호환)
- BACKEND_API_TOKEN 설정 시 Authorization 헤더 추가

사용 방법:
    from app.clients.backend_client import BackendClient

    client = BackendClient()
    result = await client.send_ai_log(log_entry)
"""

from typing import Any, Dict, Optional

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.ai_log import AILogEntry, AILogRequest, AILogResponse, to_backend_log_payload

logger = get_logger(__name__)
settings = get_settings()


class BackendClient:
    """
    백엔드 API 클라이언트.

    ctrlf-back (Spring 백엔드)와의 HTTP 통신을 담당합니다.
    AI 로그는 camelCase JSON 스키마로 전송됩니다.

    Attributes:
        _base_url: 백엔드 서비스 base URL
        _api_token: API 인증 토큰 (선택사항)
        _timeout: HTTP 요청 타임아웃 (초)

    Usage:
        client = BackendClient()
        success = await client.send_ai_log(log_entry)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: float = 5.0,
    ) -> None:
        """
        BackendClient 초기화.

        Args:
            base_url: 백엔드 서비스 URL. None이면 설정에서 가져옴.
            api_token: API 인증 토큰. None이면 설정에서 가져옴.
            timeout: HTTP 요청 타임아웃 (초). 기본 5초.
        """
        # Phase 9: backend_base_url 프로퍼티 사용 (mock/real 모드 자동 선택)
        self._base_url = base_url or settings.backend_base_url
        self._api_token = api_token if api_token is not None else settings.BACKEND_API_TOKEN
        self._timeout = timeout

    @property
    def is_configured(self) -> bool:
        """백엔드 URL이 설정되었는지 확인."""
        return self._base_url is not None

    def _get_auth_headers(self) -> Dict[str, str]:
        """
        인증 헤더를 반환합니다.

        BACKEND_API_TOKEN이 설정된 경우 Authorization: Bearer 헤더를 추가합니다.

        Returns:
            Dict[str, str]: 인증 헤더 딕셔너리
        """
        headers: Dict[str, str] = {}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    async def send_ai_log(self, log_entry: AILogEntry) -> AILogResponse:
        """
        AI 로그를 백엔드로 전송합니다.

        백엔드는 camelCase JSON 스키마를 기대합니다.
        요청 형식: {"log": {"sessionId": "...", "userId": "...", ...}}

        Args:
            log_entry: 전송할 AI 로그 엔트리

        Returns:
            AILogResponse: 백엔드 응답

        Note:
            - 백엔드 URL이 설정되지 않은 경우 로컬 전용으로 성공 응답 반환
            - BACKEND_API_TOKEN 설정 시 Authorization 헤더 추가
        """
        if not self._base_url:
            logger.debug("Backend URL not configured, returning mock response")
            return AILogResponse(
                success=True,
                log_id=None,
                message="Backend not configured (local only)",
            )

        endpoint = f"{self._base_url}/api/ai-logs"

        try:
            client = get_async_http_client()

            # camelCase JSON payload 생성
            payload = to_backend_log_payload(log_entry)

            # 인증 헤더 추가
            headers = self._get_auth_headers()

            response = await client.post(
                endpoint,
                json=payload,
                headers=headers if headers else None,
                timeout=self._timeout,
            )

            if response.status_code in (200, 201):
                try:
                    data = response.json()
                    # 백엔드 응답: {"success": true, "log_id": "...", "message": "..."}
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
        """
        백엔드 서비스 상태를 확인합니다.

        Returns:
            bool: 백엔드가 정상이면 True
        """
        if not self._base_url:
            return False

        try:
            client = get_async_http_client()
            # Spring Actuator health endpoint
            response = await client.get(
                f"{self._base_url}/actuator/health",
                timeout=self._timeout,
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Backend health check failed: {e}")
            return False
