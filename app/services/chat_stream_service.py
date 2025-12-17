"""
Chat Stream Service (스트리밍 채팅 서비스)

HTTP 청크 스트리밍으로 AI 응답을 전송하는 서비스입니다.
백엔드(Spring)가 NDJSON을 줄 단위로 읽어서 SSE로 변환합니다.

주요 기능:
1. NDJSON 스트리밍 응답 생성
2. 중복 요청 방지 (In-flight dedup)
3. 연결 끊김 감지 및 LLM 생성 중단
4. 메트릭 수집 (TTFB, 총 시간, 토큰 수)
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chat_stream import (
    ChatStreamRequest,
    InFlightRequest,
    StreamDoneEvent,
    StreamErrorCode,
    StreamErrorEvent,
    StreamMetaEvent,
    StreamMetrics,
    StreamTokenEvent,
)

logger = get_logger(__name__)


# =============================================================================
# In-Flight Request Tracker (중복 방지)
# =============================================================================


class InFlightTracker:
    """
    진행 중인 요청을 추적하여 중복을 방지합니다.

    메모리 기반 구현 (Redis 없이).
    TTL 기반 자동 정리 포함.
    """

    # 캐시 TTL (초)
    CACHE_TTL_SECONDS = 600  # 10분
    # 정리 주기 (초)
    CLEANUP_INTERVAL_SECONDS = 60

    def __init__(self) -> None:
        self._requests: Dict[str, InFlightRequest] = {}
        self._last_cleanup = time.time()

    def _cleanup_expired(self) -> None:
        """만료된 요청 정리."""
        now = datetime.now()
        cutoff = now - timedelta(seconds=self.CACHE_TTL_SECONDS)

        expired_keys = [
            key
            for key, req in self._requests.items()
            if req.started_at < cutoff
        ]

        for key in expired_keys:
            del self._requests[key]

        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired in-flight requests")

    def is_in_flight(self, request_id: str) -> bool:
        """요청이 현재 처리 중인지 확인."""
        # 주기적 정리
        if time.time() - self._last_cleanup > self.CLEANUP_INTERVAL_SECONDS:
            self._cleanup_expired()
            self._last_cleanup = time.time()

        if request_id not in self._requests:
            return False

        req = self._requests[request_id]
        # 완료되지 않은 요청만 in-flight
        return not req.completed

    def start_request(self, request_id: str) -> bool:
        """
        요청 시작을 등록합니다.

        Returns:
            True if registered, False if already in-flight
        """
        if self.is_in_flight(request_id):
            return False

        self._requests[request_id] = InFlightRequest(
            request_id=request_id,
            started_at=datetime.now(),
            completed=False,
        )
        return True

    def complete_request(
        self,
        request_id: str,
        final_response: Optional[str] = None,
    ) -> None:
        """요청 완료 처리."""
        if request_id in self._requests:
            self._requests[request_id].completed = True
            self._requests[request_id].final_response = final_response

    def cancel_request(self, request_id: str) -> None:
        """요청 취소 (실패/중단 시)."""
        if request_id in self._requests:
            del self._requests[request_id]

    def get_cached_response(self, request_id: str) -> Optional[str]:
        """캐시된 응답 조회 (완료된 요청)."""
        if request_id in self._requests:
            req = self._requests[request_id]
            if req.completed and req.final_response:
                return req.final_response
        return None


# 전역 트래커 인스턴스
_in_flight_tracker: Optional[InFlightTracker] = None


def get_in_flight_tracker() -> InFlightTracker:
    """싱글톤 InFlightTracker 반환."""
    global _in_flight_tracker
    if _in_flight_tracker is None:
        _in_flight_tracker = InFlightTracker()
    return _in_flight_tracker


# =============================================================================
# Chat Stream Service
# =============================================================================


class ChatStreamService:
    """
    스트리밍 채팅 서비스.

    NDJSON 형식으로 LLM 응답을 스트리밍합니다.
    """

    def __init__(
        self,
        tracker: Optional[InFlightTracker] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._tracker = tracker or get_in_flight_tracker()
        self._client = client or get_async_http_client()
        self._settings = get_settings()

    async def stream_chat(
        self,
        request: ChatStreamRequest,
    ) -> AsyncGenerator[str, None]:
        """
        채팅 응답을 스트리밍합니다.

        NDJSON 형식:
        1. meta 이벤트 (시작)
        2. token 이벤트 (여러 번)
        3. done 또는 error 이벤트 (종료)

        Args:
            request: 스트리밍 채팅 요청

        Yields:
            str: NDJSON 문자열 (줄바꿈 포함)
        """
        request_id = request.request_id
        model = self._settings.LLM_MODEL_NAME or "unknown"

        # 메트릭 초기화
        metrics = StreamMetrics(
            request_id=request_id,
            model=model,
        )
        start_time = time.perf_counter()
        ttfb_recorded = False
        accumulated_response = ""

        try:
            # 1. 중복 체크
            if not self._tracker.start_request(request_id):
                # 이미 처리 중인 요청
                logger.warning(f"Duplicate in-flight request: {request_id}")
                error_event = StreamErrorEvent(
                    code=StreamErrorCode.DUPLICATE_INFLIGHT.value,
                    message="이미 처리 중인 요청입니다. 잠시 후 다시 시도해주세요.",
                    request_id=request_id,
                )
                yield error_event.to_ndjson()
                return

            # 2. META 이벤트 전송 (연결 확정)
            meta_event = StreamMetaEvent(
                request_id=request_id,
                model=model,
                timestamp=datetime.now().isoformat(),
            )
            yield meta_event.to_ndjson()

            # 3. LLM 스트리밍 호출
            if not self._settings.llm_base_url:
                # LLM 미설정 시 fallback
                logger.warning("LLM not configured, sending fallback response")
                fallback_text = "LLM 서비스가 설정되지 않았습니다. 관리자에게 문의하세요."
                for char in fallback_text:
                    token_event = StreamTokenEvent(text=char)
                    yield token_event.to_ndjson()
                    accumulated_response += char
                    if not ttfb_recorded:
                        metrics.ttfb_ms = int((time.perf_counter() - start_time) * 1000)
                        ttfb_recorded = True
                    await asyncio.sleep(0.01)  # 시뮬레이션용 딜레이
                metrics.total_tokens = len(fallback_text)
            else:
                # 실제 LLM 스트리밍 호출
                async for token in self._stream_llm_response(request, metrics, start_time):
                    yield token
                    # token 이벤트에서 텍스트 추출하여 누적
                    if '"type":"token"' in token:
                        try:
                            import json
                            data = json.loads(token.strip())
                            if data.get("type") == "token":
                                accumulated_response += data.get("text", "")
                        except Exception:
                            pass
                    if not ttfb_recorded and '"type":"token"' in token:
                        metrics.ttfb_ms = int((time.perf_counter() - start_time) * 1000)
                        ttfb_recorded = True

            # 4. DONE 이벤트 전송
            metrics.total_elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            metrics.completed = True

            done_event = StreamDoneEvent(
                finish_reason="stop",
                total_tokens=metrics.total_tokens or None,
                elapsed_ms=metrics.total_elapsed_ms,
                ttfb_ms=metrics.ttfb_ms,
            )
            yield done_event.to_ndjson()

            # 요청 완료 처리
            self._tracker.complete_request(request_id, accumulated_response)

            # 메트릭 로깅 (PII 제외)
            self._log_metrics(metrics)

        except asyncio.CancelledError:
            # 클라이언트 연결 끊김
            logger.info(f"Stream cancelled (client disconnected): {request_id}")
            metrics.error_code = StreamErrorCode.CLIENT_DISCONNECTED.value
            metrics.total_elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            self._tracker.cancel_request(request_id)
            self._log_metrics(metrics)
            raise

        except Exception as e:
            # 기타 에러
            logger.exception(f"Stream error: {request_id}")
            metrics.error_code = StreamErrorCode.INTERNAL_ERROR.value
            metrics.total_elapsed_ms = int((time.perf_counter() - start_time) * 1000)

            error_event = StreamErrorEvent(
                code=StreamErrorCode.INTERNAL_ERROR.value,
                message=f"스트리밍 중 오류가 발생했습니다: {type(e).__name__}",
                request_id=request_id,
            )
            yield error_event.to_ndjson()

            self._tracker.cancel_request(request_id)
            self._log_metrics(metrics)

    async def _stream_llm_response(
        self,
        request: ChatStreamRequest,
        metrics: StreamMetrics,
        start_time: float,
    ) -> AsyncGenerator[str, None]:
        """
        LLM API를 호출하여 스트리밍 응답을 생성합니다.

        OpenAI 호환 API의 stream=true 사용.
        """
        base_url = str(self._settings.llm_base_url).rstrip("/")
        url = f"{base_url}/v1/chat/completions"

        payload = {
            "model": self._settings.LLM_MODEL_NAME,
            "messages": [
                {"role": "user", "content": request.user_message},
            ],
            "temperature": 0.7,
            "max_tokens": 2048,
            "stream": True,  # 스트리밍 활성화
        }

        # 역할 기반 시스템 프롬프트 추가
        if request.role:
            system_prompt = self._get_system_prompt(request.role)
            if system_prompt:
                payload["messages"].insert(0, {
                    "role": "system",
                    "content": system_prompt,
                })

        logger.info(f"Starting LLM stream: request_id={request.request_id}")

        try:
            async with self._client.stream(
                "POST",
                url,
                json=payload,
                timeout=60.0,  # 스트리밍용 긴 타임아웃
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    logger.error(f"LLM stream error: status={response.status_code}, body={error_text[:200]}")
                    error_event = StreamErrorEvent(
                        code=StreamErrorCode.LLM_ERROR.value,
                        message=f"LLM 서비스 오류 (HTTP {response.status_code})",
                        request_id=request.request_id,
                    )
                    yield error_event.to_ndjson()
                    return

                token_count = 0
                async for line in response.aiter_lines():
                    if not line:
                        continue

                    # SSE 형식: "data: {...}"
                    if line.startswith("data: "):
                        data_str = line[6:]  # "data: " 제거

                        if data_str.strip() == "[DONE]":
                            break

                        try:
                            import json
                            data = json.loads(data_str)
                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    token_event = StreamTokenEvent(text=content)
                                    yield token_event.to_ndjson()
                                    token_count += 1
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse LLM stream data: {data_str[:100]}")
                            continue

                metrics.total_tokens = token_count

        except httpx.TimeoutException:
            logger.error(f"LLM stream timeout: request_id={request.request_id}")
            metrics.error_code = StreamErrorCode.LLM_TIMEOUT.value
            error_event = StreamErrorEvent(
                code=StreamErrorCode.LLM_TIMEOUT.value,
                message="LLM 응답 시간이 초과되었습니다.",
                request_id=request.request_id,
            )
            yield error_event.to_ndjson()

        except httpx.RequestError as e:
            logger.error(f"LLM stream request error: {e}")
            metrics.error_code = StreamErrorCode.LLM_ERROR.value
            error_event = StreamErrorEvent(
                code=StreamErrorCode.LLM_ERROR.value,
                message=f"LLM 연결 오류: {type(e).__name__}",
                request_id=request.request_id,
            )
            yield error_event.to_ndjson()

    def _get_system_prompt(self, role: str) -> Optional[str]:
        """역할에 따른 시스템 프롬프트 반환."""
        prompts = {
            "employee": "당신은 CTRL+F 기업 AI 어시스턴트입니다. 직원들의 사규, 교육, 업무 관련 질문에 친절하고 정확하게 답변합니다.",
            "creator": "당신은 CTRL+F 콘텐츠 생성 AI 어시스턴트입니다. 교육 자료, FAQ, 문서 작성을 도와줍니다.",
            "reviewer": "당신은 CTRL+F 검토 AI 어시스턴트입니다. 문서 검토 및 품질 관리를 지원합니다.",
            "admin": "당신은 CTRL+F 관리자 AI 어시스턴트입니다. 시스템 관리 및 설정 관련 질문에 답변합니다.",
        }
        return prompts.get(role.lower())

    def _log_metrics(self, metrics: StreamMetrics) -> None:
        """
        메트릭을 로그에 기록합니다.

        보안/개인정보: 요청/응답 원문은 저장하지 않음.
        """
        log_data = {
            "request_id": metrics.request_id,
            "model": metrics.model,
            "ttfb_ms": metrics.ttfb_ms,
            "total_elapsed_ms": metrics.total_elapsed_ms,
            "total_tokens": metrics.total_tokens,
            "error_code": metrics.error_code,
            "completed": metrics.completed,
        }

        if metrics.error_code:
            logger.warning(f"Stream metrics (error): {log_data}")
        else:
            logger.info(f"Stream metrics: {log_data}")
