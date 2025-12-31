"""
Telemetry Publisher - Async Queue + Batch Flush + Fire-and-Forget

v1 Telemetry 이벤트를 백엔드로 전송하는 비동기 Publisher입니다.
요청 처리 경로와 분리하여 전송 실패가 채팅 응답에 영향을 주지 않습니다.

설계 원칙:
- enqueue는 절대 네트워크 호출/await를 하지 않음 (동기 I/O 금지)
- 배치 전송(events[]) + 주기 flush + 큐 포화 시 drop
- 전송 실패해도 예외를 밖으로 던지지 않음
- retry_once=True면 1회만 재시도 후 drop

사용법:
    from app.telemetry.publisher import get_telemetry_publisher

    publisher = get_telemetry_publisher()
    publisher.enqueue(event)  # 비동기 아님, 즉시 반환
"""

import asyncio
from collections import deque
from datetime import datetime
from typing import Deque, Optional

import httpx

from app.core.logging import get_logger
from app.telemetry.models import TelemetryEnvelope, TelemetryEvent

logger = get_logger(__name__)

# 전역 싱글톤 인스턴스
_publisher: Optional["TelemetryPublisher"] = None


class TelemetryPublisher:
    """텔레메트리 이벤트 비동기 Publisher.

    백그라운드 태스크에서 이벤트를 배치로 묶어 백엔드로 전송합니다.
    """

    def __init__(
        self,
        backend_base_url: str,
        internal_token: str,
        source: str = "ai-gateway",
        enabled: bool = True,
        batch_size: int = 20,
        flush_sec: float = 2.0,
        max_queue_size: int = 1000,
        timeout_sec: float = 3.0,
        retry_once: bool = True,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        """TelemetryPublisher 초기화.

        Args:
            backend_base_url: 백엔드 베이스 URL
            internal_token: X-Internal-Token 헤더 값
            source: 이벤트 소스 식별자
            enabled: 활성화 여부 (False면 enqueue 무시)
            batch_size: 배치 전송 크기
            flush_sec: flush 주기 (초)
            max_queue_size: 최대 큐 크기 (초과 시 drop)
            timeout_sec: HTTP 요청 타임아웃
            retry_once: 실패 시 1회 재시도 여부
            http_client: 외부 주입 httpx.AsyncClient (테스트용)
        """
        self.backend_base_url = backend_base_url.rstrip("/")
        self.internal_token = internal_token
        self.source = source
        self.enabled = enabled
        self.batch_size = batch_size
        self.flush_sec = flush_sec
        self.max_queue_size = max_queue_size
        self.timeout_sec = timeout_sec
        self.retry_once = retry_once

        # 내부 상태
        self._queue: Deque[TelemetryEvent] = deque(maxlen=max_queue_size)
        self._http_client = http_client
        self._owns_client = http_client is None
        self._flush_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._started = False

    def enqueue(self, event: TelemetryEvent) -> bool:
        """이벤트를 큐에 추가합니다.

        절대 await/네트워크 호출을 하지 않습니다.
        예외 발생 시에도 False를 반환하고 raise하지 않습니다.

        Args:
            event: 전송할 TelemetryEvent

        Returns:
            True: 성공적으로 큐에 추가됨
            False: enabled=False, 큐 포화, 또는 오류
        """
        try:
            if not self.enabled:
                return False

            if len(self._queue) >= self.max_queue_size:
                logger.warning(
                    "Telemetry queue full, dropping event",
                    extra={"event_id": str(event.event_id)},
                )
                return False

            self._queue.append(event)
            return True

        except Exception as e:
            logger.error(
                "Error enqueueing telemetry event",
                extra={"error": str(e)},
            )
            return False

    async def start(self) -> None:
        """백그라운드 flush 태스크를 시작합니다."""
        if self._started:
            return

        self._stop_event.clear()
        self._started = True

        # HTTP 클라이언트 생성 (주입되지 않은 경우)
        if self._http_client is None:
            from app.clients.http_client import get_async_http_client
            self._http_client = get_async_http_client()
            self._owns_client = False  # shared client는 직접 close 안 함

        # 백그라운드 flush 루프 시작
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(
            "TelemetryPublisher started",
            extra={
                "batch_size": self.batch_size,
                "flush_sec": self.flush_sec,
                "max_queue_size": self.max_queue_size,
            },
        )

    async def stop(self) -> None:
        """백그라운드 flush 태스크를 종료합니다.

        남은 이벤트를 best-effort로 1회 flush 시도합니다.
        """
        if not self._started:
            return

        self._stop_event.set()

        if self._flush_task:
            try:
                await asyncio.wait_for(self._flush_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._flush_task.cancel()
                try:
                    await self._flush_task
                except asyncio.CancelledError:
                    pass

        # 마지막 drain 시도
        if len(self._queue) > 0:
            try:
                await self._flush_batch()
            except Exception as e:
                logger.warning(
                    "Failed to flush remaining events on shutdown",
                    extra={"error": str(e), "remaining": len(self._queue)},
                )

        self._started = False
        logger.info("TelemetryPublisher stopped")

    async def flush_now(self) -> int:
        """현재 큐의 이벤트를 즉시 flush합니다.

        테스트 편의를 위한 메서드입니다.

        Returns:
            전송 시도한 이벤트 수
        """
        if not self.enabled or len(self._queue) == 0:
            return 0

        return await self._flush_batch()

    async def _flush_loop(self) -> None:
        """백그라운드 flush 루프."""
        while not self._stop_event.is_set():
            try:
                # flush 조건 체크
                if len(self._queue) >= self.batch_size:
                    await self._flush_batch()
                else:
                    # 주기적 flush 대기
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self.flush_sec,
                        )
                        # stop_event가 set되면 루프 종료
                        break
                    except asyncio.TimeoutError:
                        # 타임아웃 = flush 주기 도래
                        if len(self._queue) > 0:
                            await self._flush_batch()

            except Exception as e:
                logger.error(
                    "Error in telemetry flush loop",
                    extra={"error": str(e)},
                )
                await asyncio.sleep(1.0)  # 에러 시 짧은 대기 후 재시도

    async def _flush_batch(self) -> int:
        """큐에서 배치를 꺼내 전송합니다.

        Returns:
            전송 시도한 이벤트 수
        """
        if len(self._queue) == 0:
            return 0

        # 배치 추출
        batch: list[TelemetryEvent] = []
        while len(self._queue) > 0 and len(batch) < self.batch_size:
            batch.append(self._queue.popleft())

        if not batch:
            return 0

        # Envelope 생성
        envelope = TelemetryEnvelope(
            source=self.source,
            sent_at=datetime.now(),
            events=batch,
        )

        # 전송 시도
        success = await self._send_envelope(envelope)

        if not success and self.retry_once:
            # 1회 재시도
            await asyncio.sleep(0.1)  # 짧은 대기
            success = await self._send_envelope(envelope)

        if not success:
            # 재시도 후에도 실패 - drop (큐로 되돌리지 않음)
            event_ids = [str(e.event_id) for e in batch[:3]]  # 처음 3개만 로깅
            logger.error(
                "Telemetry batch dropped after retry",
                extra={
                    "dropped_count": len(batch),
                    "sample_event_ids": event_ids,
                },
            )

        return len(batch)

    async def _send_envelope(self, envelope: TelemetryEnvelope) -> bool:
        """Envelope를 백엔드로 전송합니다.

        Args:
            envelope: 전송할 TelemetryEnvelope

        Returns:
            True: 전송 성공 (200)
            False: 전송 실패
        """
        if self._http_client is None:
            logger.error("HTTP client not initialized")
            return False

        url = f"{self.backend_base_url}/internal/telemetry/events"
        headers = {
            "X-Internal-Token": self.internal_token,
            "Content-Type": "application/json",
        }

        try:
            body = envelope.model_dump(by_alias=True, exclude_none=True, mode="json")
            response = await self._http_client.post(
                url,
                json=body,
                headers=headers,
                timeout=self.timeout_sec,
            )

            if response.status_code == 200:
                logger.debug(
                    "Telemetry batch sent",
                    extra={"event_count": len(envelope.events)},
                )
                return True
            else:
                logger.warning(
                    "Telemetry send failed",
                    extra={
                        "status_code": response.status_code,
                        "event_count": len(envelope.events),
                    },
                )
                return False

        except httpx.TimeoutException:
            logger.warning(
                "Telemetry send timeout",
                extra={"timeout_sec": self.timeout_sec},
            )
            return False

        except Exception as e:
            logger.error(
                "Telemetry send error",
                extra={"error": str(e)},
            )
            return False


def get_telemetry_publisher() -> Optional[TelemetryPublisher]:
    """전역 TelemetryPublisher 인스턴스를 반환합니다.

    Returns:
        TelemetryPublisher 인스턴스 또는 None (미초기화 시)
    """
    return _publisher


def set_telemetry_publisher(publisher: Optional[TelemetryPublisher]) -> None:
    """전역 TelemetryPublisher 인스턴스를 설정합니다.

    Args:
        publisher: 설정할 TelemetryPublisher 인스턴스
    """
    global _publisher
    _publisher = publisher
