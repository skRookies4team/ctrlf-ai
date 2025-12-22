"""
LLM 클라이언트 모듈 (LLM Client Module)

LLM 서비스(OpenAI 호환 또는 내부 LLM 서버)와 통신하는 클라이언트입니다.

실제 API 스펙(경로, 헤더, payload 구조)은 이후 팀에서 확정 후
TODO 부분을 수정해야 합니다.

Phase 12 업데이트:
- 타임아웃 설정 명시 (30초)
- UpstreamServiceError로 에러 래핑
- 재시도 로직 추가 (1회)
- 개별 latency 측정

사용 방법:
    from app.clients.llm_client import LLMClient

    client = LLMClient()

    # 헬스체크
    is_healthy = await client.health_check()

    # 채팅 완성 요청
    response = await client.generate_chat_completion(
        messages=[{"role": "user", "content": "안녕하세요"}]
    )
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.exceptions import ErrorType, ServiceType, UpstreamServiceError
from app.core.logging import get_logger
from app.core.retry import (
    DEFAULT_LLM_TIMEOUT,
    LLM_RETRY_CONFIG,
    retry_async_operation,
)

logger = get_logger(__name__)


class LLMClient:
    """
    LLM 서비스(OpenAI 호환 또는 내부 LLM 서버)와 통신하는 클라이언트.

    실제 API 스펙(경로, 헤더, payload 구조)은 이후 팀에서 확정 후
    TODO 부분을 수정해야 합니다.

    Phase 12 업데이트:
    - 타임아웃 설정 명시 (30초)
    - UpstreamServiceError로 에러 래핑
    - 재시도 로직 추가 (1회)
    - 개별 latency 측정

    Attributes:
        _client: 공용 httpx.AsyncClient 인스턴스
        _base_url: LLM 서비스 기본 URL
        _timeout: HTTP 요청 타임아웃 (초)
    """

    # 설정이 없거나 LLM 호출 실패 시 반환할 기본 메시지
    FALLBACK_MESSAGE = (
        "LLM service is not configured or unavailable. "
        "This is a fallback response. Please configure LLM_BASE_URL "
        "or check the LLM service status."
    )

    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = DEFAULT_LLM_TIMEOUT,
    ) -> None:
        """
        LLMClient 초기화.

        Args:
            base_url: LLM 서비스 URL. None이면 settings.llm_base_url 사용.
            client: httpx.AsyncClient 인스턴스. None이면 공용 클라이언트 사용.
            timeout: HTTP 요청 타임아웃 (초). 기본 30초.

        Note:
            Phase 9: AI_ENV 환경변수에 따라 mock/real URL이 자동 선택됩니다.
        """
        settings = get_settings()
        # Phase 9: llm_base_url 프로퍼티 사용 (mock/real 모드 자동 선택)
        self._base_url = base_url if base_url is not None else settings.llm_base_url
        self._client = client or get_async_http_client()
        self._timeout = timeout

        if not self._base_url:
            logger.warning(
                "LLM URL is not configured. "
                "LLM API calls will be skipped and return fallback responses."
            )

    def _ensure_base_url(self) -> None:
        """
        BASE_URL이 설정되어 있는지 확인합니다.

        Raises:
            RuntimeError: LLM_BASE_URL이 설정되지 않은 경우
        """
        if not self._base_url:
            raise RuntimeError("LLM_BASE_URL is not configured")

    async def health_check(self) -> bool:
        """
        LLM 서비스 헬스체크를 수행합니다.

        BASE_URL이 설정되지 않은 경우 False를 반환합니다.
        상위 레벨에서 '점검 대상 아님'으로 판단할 수 있습니다.

        Returns:
            bool: LLM 서비스가 정상이면 True, 그렇지 않으면 False
        """
        if not self._base_url:
            logger.warning("LLM_BASE_URL is not set, skipping health check")
            return False

        try:
            # TODO: 실제 LLM health 엔드포인트로 수정
            url = f"{self._base_url}/health"
            resp = await self._client.get(url)
            ok = resp.status_code == 200
            if not ok:
                logger.error("LLM health check failed: status=%s", resp.status_code)
            return ok
        except Exception as e:
            logger.exception("LLM health check error: %s", e)
            return False

    async def generate_chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        """
        ChatCompletion 스타일의 응답을 요청하고 텍스트를 반환합니다.

        ChatService에서 사용하는 통합 메서드입니다.
        base_url이 설정되지 않은 경우 fallback 메시지를 반환합니다.

        Phase 12: 에러 발생 시 UpstreamServiceError를 raise합니다.
        ChatService에서 fallback 처리를 해야 합니다.

        Args:
            messages: 대화 히스토리
                [{"role": "user"/"assistant"/"system", "content": "..."}]
            model: 사용할 모델 이름 (선택)
            temperature: 응답 다양성 조절 (0.0 ~ 1.0)
            max_tokens: 최대 토큰 수

        Returns:
            str: LLM 응답 텍스트

        Raises:
            UpstreamServiceError: LLM 호출 실패 시 (Phase 12)
        """
        if not self._base_url:
            logger.warning("LLM generate_chat_completion skipped: base_url not configured")
            return self.FALLBACK_MESSAGE

        base = str(self._base_url).rstrip("/")
        url = f"{base}/v1/chat/completions"

        # 모델명이 없으면 설정에서 가져옴
        settings = get_settings()
        actual_model = model or settings.LLM_MODEL_NAME

        payload: Dict[str, Any] = {
            "model": actual_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        logger.info(
            f"Sending chat completion request to LLM: "
            f"messages_count={len(messages)}, model={actual_model}"
        )
        logger.debug(f"LLM request payload: {payload}")

        try:
            # Phase 12: 재시도 로직으로 감싼 HTTP 요청
            response = await retry_async_operation(
                self._client.post,
                url,
                json=payload,
                timeout=self._timeout,
                config=LLM_RETRY_CONFIG,
                operation_name="llm_chat_completion",
            )
            response.raise_for_status()

            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                logger.warning("LLM response has no choices")
                raise UpstreamServiceError(
                    service=ServiceType.LLM,
                    error_type=ErrorType.UPSTREAM_ERROR,
                    message="LLM response has no choices",
                )

            message = choices[0].get("message", {})
            content = message.get("content", "")

            if not content:
                logger.warning("LLM response has empty content")
                raise UpstreamServiceError(
                    service=ServiceType.LLM,
                    error_type=ErrorType.UPSTREAM_ERROR,
                    message="LLM response has empty content",
                )

            logger.info(
                f"LLM chat completion success: response_length={len(content)}"
            )
            return content

        except UpstreamServiceError:
            # 이미 래핑된 예외는 그대로 raise
            raise

        except httpx.TimeoutException as e:
            logger.error(f"LLM chat completion timeout after {self._timeout}s")
            raise UpstreamServiceError(
                service=ServiceType.LLM,
                error_type=ErrorType.UPSTREAM_TIMEOUT,
                message=f"LLM timeout after {self._timeout}s",
                is_timeout=True,
                original_error=e,
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"LLM chat completion HTTP error: status={e.response.status_code}, "
                f"detail={e.response.text[:200] if e.response.text else 'N/A'}"
            )
            raise UpstreamServiceError(
                service=ServiceType.LLM,
                error_type=ErrorType.UPSTREAM_ERROR,
                message=f"LLM HTTP {e.response.status_code}",
                status_code=e.response.status_code,
                original_error=e,
            )

        except httpx.RequestError as e:
            logger.error(f"LLM chat completion request error: {e}")
            raise UpstreamServiceError(
                service=ServiceType.LLM,
                error_type=ErrorType.UPSTREAM_ERROR,
                message=f"LLM request error: {type(e).__name__}",
                original_error=e,
            )

        except Exception as e:
            logger.exception("LLM chat completion unexpected error")
            raise UpstreamServiceError(
                service=ServiceType.LLM,
                error_type=ErrorType.INTERNAL_ERROR,
                message=f"LLM unexpected error: {type(e).__name__}",
                original_error=e,
            )

    async def generate_chat_completion_with_latency(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> Tuple[str, int]:
        """
        ChatCompletion을 요청하고 응답과 latency를 함께 반환합니다.

        Phase 12: 개별 서비스 latency 측정을 위해 추가.

        Args:
            messages: 대화 히스토리
            model: 사용할 모델 이름 (선택)
            temperature: 응답 다양성 조절
            max_tokens: 최대 토큰 수

        Returns:
            Tuple[str, int]: (응답 텍스트, latency_ms)

        Raises:
            UpstreamServiceError: LLM 호출 실패 시
        """
        start_time = time.perf_counter()
        try:
            result = await self.generate_chat_completion(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            return result, latency_ms
        except Exception:
            # latency는 실패해도 측정
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.debug(f"LLM call failed after {latency_ms}ms")
            raise

    async def generate_chat_completion_raw(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 512,
    ) -> Dict[str, Any]:
        """
        ChatCompletion 스타일의 원본 응답을 반환합니다.

        TODO: 실제 LLM API 스펙에 맞게 엔드포인트/필드 이름을 수정해야 합니다.

        Args:
            messages: 대화 히스토리
                [{"role": "user"/"assistant"/"system", "content": "..."}]
            model: 사용할 모델 이름 (선택)
            temperature: 응답 다양성 조절 (0.0 ~ 1.0)
            max_tokens: 최대 토큰 수

        Returns:
            Dict[str, Any]: LLM 원본 응답 JSON

        Raises:
            RuntimeError: LLM_BASE_URL이 설정되지 않은 경우
            httpx.HTTPStatusError: HTTP 요청 실패 시
        """
        self._ensure_base_url()
        payload: Dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if model:
            payload["model"] = model

        # TODO: 실제 LLM 엔드포인트로 수정 (예: /v1/chat/completions)
        base = str(self._base_url).rstrip("/")
        url = f"{base}/v1/chat/completions"
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def generate_embedding(
        self,
        text: str,
        *,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        텍스트 임베딩을 생성합니다.

        TODO: 실제 임베딩 API 스펙에 맞게 수정 필요

        Args:
            text: 임베딩할 텍스트
            model: 사용할 임베딩 모델 이름 (선택)

        Returns:
            Dict[str, Any]: 임베딩 응답 JSON

        Raises:
            RuntimeError: LLM_BASE_URL이 설정되지 않은 경우
            httpx.HTTPStatusError: HTTP 요청 실패 시
        """
        self._ensure_base_url()
        payload: Dict[str, Any] = {
            "input": text,
        }
        if model:
            payload["model"] = model

        # TODO: 실제 임베딩 엔드포인트로 수정 (예: /v1/embeddings)
        url = f"{self._base_url}/v1/embeddings"
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_llm_client: Optional["LLMClient"] = None


def get_llm_client() -> "LLMClient":
    """
    LLMClient 싱글톤 인스턴스를 반환합니다.

    첫 호출 시 인스턴스를 생성하고, 이후에는 동일 인스턴스를 반환합니다.
    테스트에서는 clear_llm_client()로 초기화할 수 있습니다.

    Returns:
        LLMClient: 싱글톤 클라이언트 인스턴스
    """
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def clear_llm_client() -> None:
    """
    LLMClient 싱글톤 인스턴스를 제거합니다 (테스트용).

    테스트 격리를 위해 각 테스트 후 호출하여 싱글톤을 초기화합니다.
    """
    global _llm_client
    _llm_client = None
