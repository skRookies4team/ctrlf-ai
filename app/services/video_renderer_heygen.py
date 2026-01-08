"""
HeyGen 기반 영상 렌더러

HeyGen API를 사용하여 영상을 생성합니다.
render_with_heygen.py의 로직을 VideoRenderer 인터페이스로 구현.
"""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.clients.heygen_client import HeyGenClient
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.video_render import RenderedAssets, RenderStep
from app.services.video_render_service import VideoRenderer
from app.utils.heygen_payload import (
    build_heygen_generate_payload,
    build_heygen_video_inputs,
)
from app.utils.script_enhance import enhance_video_script_for_video
from app.utils.s3_uploader import upload_to_s3

logger = get_logger(__name__)


@dataclass
class HeyGenRendererConfig:
    """HeyGen 렌더러 설정."""

    output_dir: str = "./video_output"
    avatar_id: Optional[str] = None
    voice_id: Optional[str] = None
    bg_type: str = "color"
    bg_value: str = "#FFFFFF"
    width: int = 1280
    height: int = 720
    poll_interval_sec: int = 10
    max_polls: int = 180


@dataclass
class HeyGenRenderJobContext:
    """HeyGen 렌더 잡 컨텍스트."""

    job_id: str
    script_json: Dict[str, Any]
    output_dir: Path
    heygen_video_id: Optional[str] = None
    video_url: Optional[str] = None
    mp4_path: Optional[Path] = None
    s3_uri: Optional[str] = None
    duration_sec: float = 0.0


class HeyGenVideoRenderer(VideoRenderer):
    """HeyGen 기반 영상 렌더러.

    HeyGen API를 사용하여 영상을 생성하고 S3에 업로드합니다.

    Usage:
        renderer = HeyGenVideoRenderer()
        service.set_renderer(renderer)
    """

    def __init__(
        self,
        config: Optional[HeyGenRendererConfig] = None,
        heygen_client: Optional[HeyGenClient] = None,
    ):
        """렌더러 초기화.

        Args:
            config: 렌더러 설정
            heygen_client: HeyGen 클라이언트 (없으면 환경변수에서 생성)
        """
        settings = get_settings()
        self.config = config or HeyGenRendererConfig(
            output_dir=os.getenv("RENDER_OUTPUT_DIR", "./video_output"),
            avatar_id=os.getenv("HEYGEN_AVATAR_ID"),
            voice_id=os.getenv("HEYGEN_VOICE_ID"),
            bg_type=os.getenv("HEYGEN_BG_TYPE", "color"),
            bg_value=os.getenv("HEYGEN_BG_VALUE", "#FFFFFF"),
            width=int(os.getenv("HEYGEN_DIM_W", "1280")),
            height=int(os.getenv("HEYGEN_DIM_H", "720")),
        )

        if not self.config.avatar_id:
            raise ValueError("HEYGEN_AVATAR_ID environment variable is not set")
        if not self.config.voice_id:
            raise ValueError("HEYGEN_VOICE_ID environment variable is not set")

        self._heygen_client = heygen_client or HeyGenClient(
            api_key=os.getenv("HEYGEN_API_KEY", "")
        )
        self._contexts: Dict[str, HeyGenRenderJobContext] = {}

        # 출력 디렉토리 생성
        self._output_dir = Path(self.config.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def execute_step(
        self,
        step: RenderStep,
        script_json: dict,
        job_id: str,
    ) -> None:
        """파이프라인 단계 실행.

        Args:
            step: 실행할 단계
            script_json: 스크립트 JSON
            job_id: 잡 ID
        """
        # 컨텍스트 생성/조회
        if job_id not in self._contexts:
            job_output_dir = self._output_dir / job_id
            job_output_dir.mkdir(parents=True, exist_ok=True)
            self._contexts[job_id] = HeyGenRenderJobContext(
                job_id=job_id,
                script_json=script_json,
                output_dir=job_output_dir,
            )

        ctx = self._contexts[job_id]

        # 단계별 실행
        if step == RenderStep.VALIDATE_SCRIPT:
            await self._validate_script(ctx)
        elif step == RenderStep.GENERATE_TTS:
            # HeyGen은 TTS를 자체 처리하므로 스킵
            pass
        elif step == RenderStep.GENERATE_SUBTITLE:
            # 자막은 별도 처리 필요 (현재는 스킵)
            pass
        elif step == RenderStep.RENDER_SLIDES:
            # HeyGen은 슬라이드를 자체 처리하므로 스킵
            pass
        elif step == RenderStep.COMPOSE_VIDEO:
            await self._render_with_heygen(ctx)
        elif step == RenderStep.UPLOAD_ASSETS:
            await self._upload_to_s3(ctx)
        elif step == RenderStep.FINALIZE:
            await self._finalize(ctx)

    async def _validate_script(self, ctx: HeyGenRenderJobContext) -> None:
        """스크립트 검증."""
        script = ctx.script_json
        if not script.get("chapters"):
            raise ValueError("Script must have at least one chapter")
        logger.info(f"Script validated: {len(script.get('chapters', []))} chapters")

    async def _render_with_heygen(self, ctx: HeyGenRenderJobContext) -> None:
        """HeyGen으로 영상 렌더링."""
        script = ctx.script_json

        # 스크립트 강화 (인트로 씬 추가 등)
        enhanced = enhance_video_script_for_video(script)

        # narration 필수 보정
        for chapter in enhanced.get("chapters", []):
            for scene in chapter.get("scenes", []):
                if not scene.get("narration"):
                    scene["narration"] = scene.get("on_screen_text") or "설명입니다."

        # HeyGen video_inputs 생성
        video_inputs = build_heygen_video_inputs(
            enhanced,
            avatar_id=self.config.avatar_id,
            voice_id=self.config.voice_id,
            bg_type=self.config.bg_type,
            bg_value=self.config.bg_value,
        )

        # HeyGen payload 생성
        payload = build_heygen_generate_payload(
            video_inputs,
            width=self.config.width,
            height=self.config.height,
        )

        logger.info(f"Generating video with HeyGen: job_id={ctx.job_id}")

        # HeyGen 요청
        heygen_video_id = await self._heygen_client.generate_video(payload)
        ctx.heygen_video_id = heygen_video_id
        logger.info(f"HeyGen video_id={heygen_video_id}")

        # 상태 폴링
        for poll_count in range(self.config.max_polls):
            status = await self._heygen_client.get_video_status(heygen_video_id)
            data = status.get("data", {})
            status_str = (data.get("status") or "").lower()
            logger.info(
                f"HeyGen status poll {poll_count + 1}/{self.config.max_polls}: {status_str}"
            )

            if status_str == "completed":
                video_url = data.get("video_url")
                if not video_url:
                    raise RuntimeError("completed but video_url missing")

                ctx.video_url = video_url
                break

            if status_str == "failed":
                error_msg = data.get("error_message", "Unknown error")
                raise RuntimeError(f"HeyGen failed: {error_msg}")

            await asyncio.sleep(self.config.poll_interval_sec)
        else:
            raise TimeoutError("HeyGen polling timeout")

        # 영상 다운로드
        mp4_path = ctx.output_dir / "video.mp4"
        await self._download_file(video_url, mp4_path)
        ctx.mp4_path = mp4_path
        logger.info(f"Video downloaded: {mp4_path}")

        # 영상 길이 계산
        duration_sec = 0
        for chapter in enhanced.get("chapters", []):
            for scene in chapter.get("scenes", []):
                duration_sec += scene.get("duration_sec", 30)
        ctx.duration_sec = duration_sec

    async def _download_file(self, url: str, out_path: Path) -> None:
        """파일 다운로드."""
        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            out_path.write_bytes(r.content)

    async def _upload_to_s3(self, ctx: HeyGenRenderJobContext) -> None:
        """S3에 업로드."""
        if not ctx.mp4_path or not ctx.mp4_path.exists():
            raise ValueError("Video file not found for upload")

        # S3 키 생성
        s3_key = f"education_videos/{ctx.job_id}/video.mp4"

        # S3 업로드
        s3_url = upload_to_s3(ctx.mp4_path, s3_key)
        logger.info(f"S3 uploaded: {s3_url}")

        # s3:// 형식 URI로 변환 (백엔드 콜백용)
        settings = get_settings()
        bucket = os.getenv("S3_BUCKET_NAME") or settings.AWS_S3_BUCKET
        ctx.s3_uri = f"s3://{bucket}/{s3_key}" if bucket else s3_url

    async def _finalize(self, ctx: HeyGenRenderJobContext) -> None:
        """최종화."""
        logger.info(
            f"Render finalized: job_id={ctx.job_id}, "
            f"s3_uri={ctx.s3_uri}, duration={ctx.duration_sec}"
        )

    async def get_rendered_assets(self, job_id: str) -> RenderedAssets:
        """렌더링된 에셋 조회.

        Args:
            job_id: 잡 ID

        Returns:
            렌더링된 에셋 정보
        """
        ctx = self._contexts.get(job_id)
        if not ctx:
            raise ValueError(f"Job context not found: {job_id}")

        # S3 URI가 있으면 우선 사용 (백엔드 콜백용)
        video_path = ctx.s3_uri if ctx.s3_uri else (str(ctx.mp4_path) if ctx.mp4_path else "")

        return RenderedAssets(
            mp4_path=video_path,
            thumbnail_path="",  # HeyGen은 썸네일을 별도 제공하지 않음
            subtitle_path="",  # 자막은 별도 처리 필요
            duration_sec=ctx.duration_sec,
        )

    def get_s3_uri(self, job_id: str) -> Optional[str]:
        """S3 URI 조회 (백엔드 콜백용).

        Args:
            job_id: 잡 ID

        Returns:
            S3 URI (s3:// 형식)
        """
        ctx = self._contexts.get(job_id)
        return ctx.s3_uri if ctx else None


def get_heygen_video_renderer() -> HeyGenVideoRenderer:
    """HeyGen 렌더러 싱글톤."""
    from app.services.video_renderer_heygen import HeyGenVideoRenderer

    return HeyGenVideoRenderer()

