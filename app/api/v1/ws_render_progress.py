"""
WebSocket 렌더 진행률 API

렌더 파이프라인 진행률을 실시간으로 전달하는 WebSocket 엔드포인트.

엔드포인트:
- WS /ws/videos/{video_id}/render-progress

상태 머신: QUEUED → PROCESSING → COMPLETED | FAILED

이벤트 형식:
{
    "job_id": "job-xxx",
    "video_id": "video-xxx",
    "status": "PROCESSING",
    "step": "GENERATE_TTS",
    "progress": 25,
    "message": "TTS 음성 생성 중...",
    "timestamp": "2025-01-15T10:30:00Z"
}

사용법 (프론트엔드):
    const ws = new WebSocket("ws://localhost:8000/ws/videos/video-001/render-progress");
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log(`Progress: ${data.progress}%`);
    };
"""

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.core.logging import get_logger
from app.models.video_render import RenderJobStatus, RenderStep

logger = get_logger(__name__)

router = APIRouter()


# =============================================================================
# Data Models
# =============================================================================


class RenderProgressEvent(BaseModel):
    """렌더 진행률 이벤트."""

    job_id: str
    video_id: str
    status: str
    step: Optional[str] = None
    progress: int  # 0-100
    message: str
    timestamp: str

    @classmethod
    def create(
        cls,
        job_id: str,
        video_id: str,
        status: RenderJobStatus,
        step: Optional[RenderStep] = None,
        progress: int = 0,
        message: str = "",
    ) -> "RenderProgressEvent":
        """이벤트 생성."""
        return cls(
            job_id=job_id,
            video_id=video_id,
            status=status.value,
            step=step.value if step else None,
            progress=progress,
            message=message,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )


# =============================================================================
# Connection Manager
# =============================================================================


class RenderProgressConnectionManager:
    """WebSocket 연결 관리자.

    video_id별로 연결을 관리하여 해당 비디오의 진행률을 구독 중인
    모든 클라이언트에게 이벤트를 브로드캐스트합니다.

    Usage:
        manager = get_connection_manager()

        # 연결 등록
        await manager.connect(websocket, video_id)

        # 이벤트 브로드캐스트
        await manager.broadcast(video_id, event)

        # 연결 해제
        manager.disconnect(websocket, video_id)
    """

    def __init__(self):
        # video_id -> Set[WebSocket]
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        # WebSocket -> Set[video_id] (역방향 매핑, 정리용)
        self._socket_videos: Dict[WebSocket, Set[str]] = defaultdict(set)
        # WebSocket -> job_id (필터링용, None이면 모든 이벤트 수신)
        self._socket_job_filter: Dict[WebSocket, Optional[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        video_id: str,
        job_id: Optional[str] = None,
    ) -> None:
        """WebSocket 연결을 등록합니다.

        Args:
            websocket: WebSocket 연결
            video_id: 구독할 비디오 ID
            job_id: (Optional) 특정 잡만 필터링
        """
        await websocket.accept()

        async with self._lock:
            self._connections[video_id].add(websocket)
            self._socket_videos[websocket].add(video_id)
            self._socket_job_filter[websocket] = job_id

        logger.info(
            f"WebSocket connected: video_id={video_id}, job_id={job_id}, "
            f"total={len(self._connections[video_id])}"
        )

        # 연결 성공 메시지 전송
        await websocket.send_json(
            {
                "type": "connected",
                "video_id": video_id,
                "job_id": job_id,
                "message": "Connected to render progress stream",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        )

    def disconnect(self, websocket: WebSocket, video_id: Optional[str] = None) -> None:
        """WebSocket 연결을 해제합니다."""
        # job filter 정리
        if websocket in self._socket_job_filter:
            del self._socket_job_filter[websocket]

        # video_id가 지정된 경우
        if video_id:
            self._connections[video_id].discard(websocket)
            self._socket_videos[websocket].discard(video_id)

            # 빈 세트 정리
            if not self._connections[video_id]:
                del self._connections[video_id]
        else:
            # 모든 video_id에서 해제
            for vid in list(self._socket_videos.get(websocket, [])):
                self._connections[vid].discard(websocket)
                if not self._connections[vid]:
                    del self._connections[vid]

            if websocket in self._socket_videos:
                del self._socket_videos[websocket]

        logger.info(f"WebSocket disconnected: video_id={video_id}")

    async def broadcast(self, video_id: str, event: RenderProgressEvent) -> int:
        """특정 video_id에 연결된 클라이언트에게 이벤트를 전송합니다.

        job_id 필터링 지원:
        - 클라이언트가 특정 job_id를 구독한 경우, 해당 잡의 이벤트만 전송
        - job_id 필터가 None인 클라이언트는 모든 이벤트 수신

        Args:
            video_id: 비디오 ID
            event: 전송할 이벤트

        Returns:
            int: 전송 성공한 클라이언트 수
        """
        connections = self._connections.get(video_id, set()).copy()
        if not connections:
            return 0

        event_data = event.model_dump()
        event_job_id = event.job_id
        sent_count = 0
        failed = []

        for websocket in connections:
            # job_id 필터링
            filter_job_id = self._socket_job_filter.get(websocket)
            if filter_job_id and filter_job_id != event_job_id:
                continue  # 다른 job_id 이벤트는 스킵

            try:
                await websocket.send_json(event_data)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to send to websocket: {e}")
                failed.append(websocket)

        # 실패한 연결 정리
        for ws in failed:
            self.disconnect(ws, video_id)

        return sent_count

    async def broadcast_all(self, event: RenderProgressEvent) -> int:
        """모든 연결에 이벤트를 전송합니다 (video_id 무관).

        Args:
            event: 전송할 이벤트

        Returns:
            int: 전송 성공한 총 클라이언트 수
        """
        total_sent = 0
        for video_id in list(self._connections.keys()):
            sent = await self.broadcast(video_id, event)
            total_sent += sent
        return total_sent

    def get_connection_count(self, video_id: Optional[str] = None) -> int:
        """연결 수 반환."""
        if video_id:
            return len(self._connections.get(video_id, set()))
        return sum(len(conns) for conns in self._connections.values())

    def get_active_video_ids(self) -> List[str]:
        """활성 비디오 ID 목록 반환."""
        return list(self._connections.keys())


# =============================================================================
# Singleton Connection Manager
# =============================================================================


_connection_manager: Optional[RenderProgressConnectionManager] = None


def get_connection_manager() -> RenderProgressConnectionManager:
    """ConnectionManager 싱글톤 인스턴스 반환."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = RenderProgressConnectionManager()
    return _connection_manager


def clear_connection_manager() -> None:
    """ConnectionManager 싱글톤 초기화 (테스트용)."""
    global _connection_manager
    _connection_manager = None


# =============================================================================
# WebSocket Endpoint
# =============================================================================


@router.websocket("/videos/{video_id}/render-progress")
async def render_progress_websocket(
    websocket: WebSocket,
    video_id: str,
    job_id: Optional[str] = None,
):
    """렌더 진행률 WebSocket 엔드포인트.

    클라이언트가 이 엔드포인트에 연결하면 해당 video_id의 렌더 진행률을
    실시간으로 수신할 수 있습니다.

    Args:
        websocket: WebSocket 연결
        video_id: 구독할 비디오 ID
        job_id: (Optional) 특정 잡 ID 필터링
    """
    manager = get_connection_manager()

    # job_id 미지정 시 최신 활성 잡으로 자동 매핑
    active_job_id = job_id
    if not active_job_id:
        try:
            from app.repositories.render_job_repository import get_render_job_repository

            repo = get_render_job_repository()
            active_job = repo.get_active_by_video_id(video_id)
            if active_job:
                active_job_id = active_job.job_id
        except Exception:
            pass  # 저장소 오류 시 무시

    try:
        await manager.connect(websocket, video_id, job_id=active_job_id)

        # 연결 유지 (클라이언트가 끊을 때까지)
        while True:
            try:
                # 클라이언트로부터 메시지 수신 (ping/pong 또는 종료)
                data = await websocket.receive_text()

                # ping 처리
                if data == "ping":
                    await websocket.send_json(
                        {
                            "type": "pong",
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                        }
                    )

            except WebSocketDisconnect:
                break

    except Exception as e:
        logger.error(f"WebSocket error: video_id={video_id}, job_id={job_id}, error={e}")

    finally:
        manager.disconnect(websocket, video_id)


# =============================================================================
# Helper Functions (for external use)
# =============================================================================


async def notify_render_progress(
    job_id: str,
    video_id: str,
    status: RenderJobStatus,
    step: Optional[RenderStep] = None,
    progress: int = 0,
    message: str = "",
) -> int:
    """렌더 진행률 알림을 구독자에게 전송합니다.

    VideoRenderService에서 호출하여 진행률을 브로드캐스트합니다.

    Args:
        job_id: 잡 ID
        video_id: 비디오 ID
        status: 렌더 상태
        step: 현재 단계
        progress: 진행률 (0-100)
        message: 메시지

    Returns:
        int: 전송 성공한 클라이언트 수
    """
    manager = get_connection_manager()

    event = RenderProgressEvent.create(
        job_id=job_id,
        video_id=video_id,
        status=status,
        step=step,
        progress=progress,
        message=message,
    )

    return await manager.broadcast(video_id, event)


# 단계별 진행률 매핑
STEP_PROGRESS_MAP: Dict[RenderStep, tuple] = {
    RenderStep.VALIDATE_SCRIPT: (0, 5, "스크립트 검증 중..."),
    RenderStep.GENERATE_TTS: (5, 30, "음성 합성 중..."),
    RenderStep.GENERATE_SUBTITLE: (30, 40, "자막 생성 중..."),
    RenderStep.RENDER_SLIDES: (40, 55, "슬라이드 렌더링 중..."),
    RenderStep.COMPOSE_VIDEO: (55, 85, "영상 합성 중..."),
    RenderStep.UPLOAD_ASSETS: (85, 95, "에셋 업로드 중..."),
    RenderStep.FINALIZE: (95, 100, "마무리 중..."),
}


def get_step_progress(step: RenderStep, sub_progress: float = 0.0) -> tuple:
    """단계별 진행률 정보 반환.

    Args:
        step: 렌더 단계
        sub_progress: 단계 내 진행률 (0.0 ~ 1.0)

    Returns:
        tuple: (progress, message)
    """
    if step not in STEP_PROGRESS_MAP:
        return 0, "처리 중..."

    start, end, message = STEP_PROGRESS_MAP[step]
    progress = int(start + (end - start) * sub_progress)
    return progress, message
