"""
Phase 32: Real Video Renderer

실제 TTS, FFmpeg, Storage를 사용하는 영상 렌더러 구현.

구성요소:
- TTS Provider: 음성 합성 (mock, gtts, polly, gcp)
- Video Composer: FFmpeg 기반 영상 합성
- Storage Adapter: 에셋 업로드 (local, s3, minio)
- WebSocket Progress: 실시간 진행률 알림

환경변수:
- TTS_PROVIDER: mock | gtts | polly | gcp
- STORAGE_PROVIDER: local | s3 | minio
- RENDER_OUTPUT_DIR: 렌더링 출력 디렉토리 (기본: ./video_output)
"""

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.api.v1.ws_render_progress import (
    RenderProgressEvent,
    get_step_progress,
    notify_render_progress,
)
from app.clients.storage_adapter import (
    BaseStorageProvider,
    get_default_storage_provider,
)
from app.clients.tts_provider import BaseTTSProvider, get_default_tts_provider
from app.core.logging import get_logger
from app.models.video_render import RenderedAssets, RenderJobStatus, RenderStep
from app.services.video_composer import SceneInfo, VideoComposer, get_video_composer
from app.services.video_render_service import VideoRenderer

logger = get_logger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class RealRendererConfig:
    """실제 렌더러 설정."""
    output_dir: str = "./video_output"
    tts_language: str = "ko"
    video_width: int = 1280
    video_height: int = 720
    # TTS 청크 크기 (긴 텍스트 분할용)
    tts_max_chars: int = 5000


# =============================================================================
# Job Context
# =============================================================================


@dataclass
class RealRenderJobContext:
    """렌더 잡 컨텍스트."""
    job_id: str
    video_id: str
    script_json: Dict[str, Any]
    output_dir: Path

    # 상태
    validated: bool = False
    scenes: List[SceneInfo] = field(default_factory=list)

    # 파일 경로
    tts_audio_path: Optional[str] = None
    subtitle_path: Optional[str] = None
    video_path: Optional[str] = None
    thumbnail_path: Optional[str] = None

    # 업로드된 URL
    video_url: Optional[str] = None
    subtitle_url: Optional[str] = None
    thumbnail_url: Optional[str] = None

    # 메타데이터
    duration_sec: float = 0.0


# =============================================================================
# Real Video Renderer
# =============================================================================


class RealVideoRenderer(VideoRenderer):
    """실제 영상 렌더러.

    TTS, FFmpeg, Storage를 사용하여 실제 영상을 생성합니다.

    Usage:
        renderer = RealVideoRenderer()
        # VideoRenderService에 설정
        service.set_renderer(renderer)
    """

    def __init__(
        self,
        config: Optional[RealRendererConfig] = None,
        tts_provider: Optional[BaseTTSProvider] = None,
        storage_provider: Optional[BaseStorageProvider] = None,
        video_composer: Optional[VideoComposer] = None,
    ):
        """렌더러 초기화.

        Args:
            config: 렌더러 설정
            tts_provider: TTS Provider (테스트용 mock 주입)
            storage_provider: Storage Provider (테스트용 mock 주입)
            video_composer: Video Composer (테스트용 mock 주입)
        """
        self.config = config or RealRendererConfig(
            output_dir=os.getenv("RENDER_OUTPUT_DIR", "./video_output")
        )
        self._tts = tts_provider
        self._storage = storage_provider
        self._composer = video_composer
        self._contexts: Dict[str, RealRenderJobContext] = {}

        # 출력 디렉토리 생성
        self._output_dir = Path(self.config.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def _get_tts(self) -> BaseTTSProvider:
        """TTS Provider lazy loading."""
        if self._tts is None:
            self._tts = get_default_tts_provider()
        return self._tts

    def _get_storage(self) -> BaseStorageProvider:
        """Storage Provider lazy loading."""
        if self._storage is None:
            self._storage = get_default_storage_provider()
        return self._storage

    def _get_composer(self) -> VideoComposer:
        """Video Composer lazy loading."""
        if self._composer is None:
            self._composer = get_video_composer()
        return self._composer

    async def execute_step(
        self,
        step: RenderStep,
        script_json: dict,
        job_id: str,
    ) -> None:
        """파이프라인 단계 실행."""
        # 컨텍스트 생성/조회
        if job_id not in self._contexts:
            # video_id 추출 (script_json에서 또는 job_id에서)
            video_id = script_json.get("video_id", job_id.replace("job-", "video-"))

            job_output_dir = self._output_dir / job_id
            job_output_dir.mkdir(parents=True, exist_ok=True)

            self._contexts[job_id] = RealRenderJobContext(
                job_id=job_id,
                video_id=video_id,
                script_json=script_json,
                output_dir=job_output_dir,
            )

        ctx = self._contexts[job_id]

        # 진행률 알림
        progress, message = get_step_progress(step, 0.0)
        await notify_render_progress(
            job_id=ctx.job_id,
            video_id=ctx.video_id,
            status=RenderJobStatus.RUNNING,
            step=step,
            progress=progress,
            message=message,
        )

        # 단계별 실행
        try:
            if step == RenderStep.VALIDATE_SCRIPT:
                await self._validate_script(ctx)
            elif step == RenderStep.GENERATE_TTS:
                await self._generate_tts(ctx)
            elif step == RenderStep.GENERATE_SUBTITLE:
                await self._generate_subtitle(ctx)
            elif step == RenderStep.RENDER_SLIDES:
                await self._render_slides(ctx)
            elif step == RenderStep.COMPOSE_VIDEO:
                await self._compose_video(ctx)
            elif step == RenderStep.UPLOAD_ASSETS:
                await self._upload_assets(ctx)
            elif step == RenderStep.FINALIZE:
                await self._finalize(ctx)

            # 단계 완료 알림
            progress, message = get_step_progress(step, 1.0)
            await notify_render_progress(
                job_id=ctx.job_id,
                video_id=ctx.video_id,
                status=RenderJobStatus.RUNNING,
                step=step,
                progress=progress,
                message=f"{message} 완료",
            )

        except Exception as e:
            logger.error(f"Step {step.value} failed for job {job_id}: {e}")
            await notify_render_progress(
                job_id=ctx.job_id,
                video_id=ctx.video_id,
                status=RenderJobStatus.FAILED,
                step=step,
                progress=progress,
                message=f"실패: {str(e)[:100]}",
            )
            raise

    async def get_rendered_assets(self, job_id: str) -> RenderedAssets:
        """렌더링된 에셋 조회."""
        ctx = self._contexts.get(job_id)
        if not ctx:
            raise ValueError(f"Job context not found: {job_id}")

        return RenderedAssets(
            mp4_path=ctx.video_url or ctx.video_path or "",
            thumbnail_path=ctx.thumbnail_url or ctx.thumbnail_path or "",
            subtitle_path=ctx.subtitle_url or ctx.subtitle_path or "",
            duration_sec=ctx.duration_sec,
        )

    # =========================================================================
    # Pipeline Steps
    # =========================================================================

    async def _validate_script(self, ctx: RealRenderJobContext) -> None:
        """스크립트 검증."""
        logger.info(f"Validating script for job: {ctx.job_id}")

        script = ctx.script_json

        # 필수 필드 체크
        if "chapters" not in script and "scenes" not in script:
            if "text" not in script and "narration" not in script:
                raise ValueError("Script must have 'chapters', 'scenes', 'text', or 'narration'")

        # 씬 추출
        ctx.scenes = self._extract_scenes(script)

        if not ctx.scenes:
            raise ValueError("No scenes found in script")

        ctx.validated = True
        logger.info(f"Script validated: {len(ctx.scenes)} scenes found")

    async def _generate_tts(self, ctx: RealRenderJobContext) -> None:
        """TTS 음성 생성."""
        logger.info(f"Generating TTS for job: {ctx.job_id}")

        tts = self._get_tts()

        # 전체 나레이션 텍스트 추출
        full_text = " ".join(scene.narration for scene in ctx.scenes if scene.narration)

        if not full_text.strip():
            full_text = "영상 콘텐츠입니다."

        # TTS 합성
        audio_path = ctx.output_dir / "audio.mp3"
        duration = await tts.synthesize_to_file(
            text=full_text,
            output_path=audio_path,
            language=self.config.tts_language,
        )

        ctx.tts_audio_path = str(audio_path)
        ctx.duration_sec = duration

        logger.info(f"TTS generated: {audio_path}, duration={duration:.2f}s")

    async def _generate_subtitle(self, ctx: RealRenderJobContext) -> None:
        """자막 생성 (Composer에서 처리)."""
        logger.info(f"Subtitle will be generated during composition: {ctx.job_id}")
        # 실제 자막 생성은 compose 단계에서 VideoComposer가 처리

    async def _render_slides(self, ctx: RealRenderJobContext) -> None:
        """슬라이드 렌더링 (Composer에서 처리)."""
        logger.info(f"Slides will be rendered during composition: {ctx.job_id}")
        # 실제 슬라이드 렌더링은 compose 단계에서 VideoComposer가 처리

    async def _compose_video(self, ctx: RealRenderJobContext) -> None:
        """영상 합성."""
        logger.info(f"Composing video for job: {ctx.job_id}")

        composer = self._get_composer()

        # 영상 합성
        result = await composer.compose(
            scenes=ctx.scenes,
            audio_path=ctx.tts_audio_path,
            output_dir=ctx.output_dir,
            job_id=ctx.job_id,
        )

        ctx.video_path = result.video_path
        ctx.subtitle_path = result.subtitle_path
        ctx.thumbnail_path = result.thumbnail_path
        ctx.duration_sec = result.duration_sec

        logger.info(f"Video composed: {ctx.video_path}")

    async def _upload_assets(self, ctx: RealRenderJobContext) -> None:
        """에셋 업로드."""
        logger.info(f"Uploading assets for job: {ctx.job_id}")

        storage = self._get_storage()

        # 비디오 업로드
        if ctx.video_path and Path(ctx.video_path).exists():
            result = await storage.put_file(
                ctx.video_path,
                f"{ctx.job_id}/video.mp4",
                "video/mp4",
            )
            ctx.video_url = result.url
            logger.info(f"Video uploaded: {ctx.video_url}")

        # 자막 업로드
        if ctx.subtitle_path and Path(ctx.subtitle_path).exists():
            result = await storage.put_file(
                ctx.subtitle_path,
                f"{ctx.job_id}/subtitle.srt",
                "text/plain",
            )
            ctx.subtitle_url = result.url
            logger.info(f"Subtitle uploaded: {ctx.subtitle_url}")

        # 썸네일 업로드
        if ctx.thumbnail_path and Path(ctx.thumbnail_path).exists():
            result = await storage.put_file(
                ctx.thumbnail_path,
                f"{ctx.job_id}/thumbnail.jpg",
                "image/jpeg",
            )
            ctx.thumbnail_url = result.url
            logger.info(f"Thumbnail uploaded: {ctx.thumbnail_url}")

    async def _finalize(self, ctx: RealRenderJobContext) -> None:
        """최종화."""
        logger.info(f"Finalizing job: {ctx.job_id}")

        # 성공 알림
        await notify_render_progress(
            job_id=ctx.job_id,
            video_id=ctx.video_id,
            status=RenderJobStatus.SUCCEEDED,
            step=RenderStep.FINALIZE,
            progress=100,
            message="렌더링 완료!",
        )

        logger.info(
            f"Render job completed: job_id={ctx.job_id}, "
            f"video_url={ctx.video_url}, duration={ctx.duration_sec:.2f}s"
        )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _extract_scenes(self, script: dict) -> List[SceneInfo]:
        """스크립트에서 SceneInfo 목록 추출."""
        scenes = []
        scene_counter = 0

        if "chapters" in script:
            for chapter in script["chapters"]:
                for scene in chapter.get("scenes", []):
                    scene_counter += 1
                    scenes.append(SceneInfo(
                        scene_id=scene.get("scene_id", scene_counter),
                        narration=scene.get("narration", ""),
                        caption=scene.get("caption"),
                        on_screen_text=scene.get("on_screen_text"),
                        duration_sec=scene.get("duration_sec"),
                    ))

        elif "scenes" in script:
            for scene in script["scenes"]:
                scene_counter += 1
                scenes.append(SceneInfo(
                    scene_id=scene.get("scene_id", scene_counter),
                    narration=scene.get("narration", ""),
                    caption=scene.get("caption"),
                    on_screen_text=scene.get("on_screen_text"),
                    duration_sec=scene.get("duration_sec"),
                ))

        elif "text" in script or "narration" in script:
            text = script.get("narration", script.get("text", ""))
            scenes.append(SceneInfo(
                scene_id=1,
                narration=text,
                caption=text[:50] if text else None,
            ))

        return scenes


# =============================================================================
# Singleton Instance
# =============================================================================


_real_renderer: Optional[RealVideoRenderer] = None


def get_real_video_renderer() -> RealVideoRenderer:
    """RealVideoRenderer 싱글톤 인스턴스 반환."""
    global _real_renderer
    if _real_renderer is None:
        _real_renderer = RealVideoRenderer()
    return _real_renderer


def clear_real_video_renderer() -> None:
    """RealVideoRenderer 싱글톤 초기화 (테스트용)."""
    global _real_renderer
    _real_renderer = None
