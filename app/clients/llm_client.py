"""
LLM 클라이언트 모듈 (LLM Client Module)

LLM 서비스(OpenAI 호환 또는 내부 LLM 서버)와 통신하는 클라이언트입니다.

실제 API 스펙(경로, 헤더, payload 구조)은 이후 팀에서 확정 후
TODO 부분을 수정해야 합니다.

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

from typing import Any, Dict, List, Optional

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class LLMClient:
    """
    LLM 서비스(OpenAI 호환 또는 내부 LLM 서버)와 통신하는 클라이언트.

    실제 API 스펙(경로, 헤더, payload 구조)은 이후 팀에서 확정 후
    TODO 부분을 수정해야 합니다.

    Attributes:
        _client: 공용 httpx.AsyncClient 인스턴스
        _base_url: LLM 서비스 기본 URL
    """

    def __init__(self) -> None:
        """LLMClient 초기화"""
        self._client = get_async_http_client()
        self._base_url = settings.LLM_BASE_URL

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
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 512,
    ) -> Dict[str, Any]:
        """
        ChatCompletion 스타일의 응답을 요청합니다.

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
        url = f"{self._base_url}/v1/chat/completions"
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
