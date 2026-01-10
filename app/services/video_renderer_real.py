"""
Phase 34/37: Real Video Renderer

실제 TTS, FFmpeg, Storage를 사용하는 영상 렌더러 구현.

구성요소:
- TTS Provider: 음성 합성 (mock, gtts, polly, gcp)
- Video Composer: FFmpeg 기반 영상 합성
- Storage Adapter: 에셋 업로드 (local, s3)
- WebSocket Progress: 실시간 진행률 알림

환경변수:
- TTS_PROVIDER: mock | gtts | polly | gcp
- STORAGE_PROVIDER: local | s3
- RENDER_OUTPUT_DIR: 렌더링 출력 디렉토리 (기본: ./video_output)
- VIDEO_VISUAL_STYLE: basic | animated (Phase 37)

Phase 34 변경사항:
- object_key 규칙: videos/{video_id}/{script_id}/{job_id}/video.mp4
- StorageUploadError 예외 처리 추가

Phase 37 변경사항:
- VIDEO_VISUAL_STYLE 환경변수 지원
- animated 모드: 씬 이미지 생성 + Ken Burns + fade 전환
- VisualPlanExtractor, ImageAssetService 통합
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
    StorageUploadError,
    get_default_storage_provider,
)
from app.clients.tts_provider import BaseTTSProvider, get_default_tts_provider
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.video_render import RenderedAssets, RenderJobStatus, RenderStep
from app.services.image_asset_service import ImageAssetService, get_image_asset_service
from app.services.video_composer import SceneInfo, VideoComposer, get_video_composer
from app.services.video_render_service import VideoRenderer
from app.services.visual_plan import VisualPlanExtractor, get_visual_plan_extractor

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
    # Phase 37: 시각 스타일 (basic | animated)
    visual_style: str = "basic"
    # Render engine (ffmpeg | heygen)
    render_engine: str = "ffmpeg"


# =============================================================================
# Job Context
# =============================================================================


@dataclass
class RealRenderJobContext:
    """렌더 잡 컨텍스트.

    Phase 34: script_id 추가 (object_key 규칙용)
    """
    job_id: str
    video_id: str
    script_id: str  # Phase 34: object_key 규칙용
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
        image_service: Optional[ImageAssetService] = None,
        plan_extractor: Optional[VisualPlanExtractor] = None,
    ):
        """렌더러 초기화.

        Args:
            config: 렌더러 설정
            tts_provider: TTS Provider (테스트용 mock 주입)
            storage_provider: Storage Provider (테스트용 mock 주입)
            video_composer: Video Composer (테스트용 mock 주입)
            image_service: ImageAssetService (테스트용 mock 주입, Phase 37)
            plan_extractor: VisualPlanExtractor (테스트용 mock 주입, Phase 37)
        """
        settings = get_settings()
        self.config = config or RealRendererConfig(
            output_dir=os.getenv("RENDER_OUTPUT_DIR", "./video_output"),
            visual_style=settings.VIDEO_VISUAL_STYLE,
            render_engine=settings.VIDEO_RENDER_ENGINE,
        )
        self._tts = tts_provider
        self._storage = storage_provider
        self._composer = video_composer
        self._image_service = image_service
        self._plan_extractor = plan_extractor
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
        """Video Composer lazy loading.

        Phase 37: visual_style 설정을 ComposerConfig에 전달.
        """
        if self._composer is None:
            from app.services.video_composer import ComposerConfig
            settings = get_settings()
            config = ComposerConfig(
                visual_style=settings.VIDEO_VISUAL_STYLE,
                fade_duration=settings.VIDEO_FADE_DURATION,
                # presentation은 기본적으로 줌 효과를 끄는 쪽이 더 PPT 느낌
                kenburns_zoom=(
                    1.0
                    if (settings.VIDEO_VISUAL_STYLE == "presentation")
                    else settings.VIDEO_KENBURNS_ZOOM
                ),
                video_width=settings.VIDEO_WIDTH,
                video_height=settings.VIDEO_HEIGHT,
                fps=settings.VIDEO_FPS,
            )
            self._composer = VideoComposer(config=config)
        return self._composer

    def _get_image_service(self) -> ImageAssetService:
        """ImageAssetService lazy loading (Phase 37)."""
        if self._image_service is None:
            self._image_service = get_image_asset_service()
        return self._image_service

    def _get_plan_extractor(self) -> VisualPlanExtractor:
        """VisualPlanExtractor lazy loading (Phase 37)."""
        if self._plan_extractor is None:
            self._plan_extractor = get_visual_plan_extractor()
        return self._plan_extractor

    async def execute_step(
        self,
        step: RenderStep,
        script_json: dict,
        job_id: str,
    ) -> None:
        """파이프라인 단계 실행."""
        # 컨텍스트 생성/조회
        if job_id not in self._contexts:
            # video_id, script_id 추출
            video_id = script_json.get("video_id", job_id.replace("job-", "video-"))
            script_id = script_json.get("script_id", "script-default")

            job_output_dir = self._output_dir / job_id
            job_output_dir.mkdir(parents=True, exist_ok=True)

            self._contexts[job_id] = RealRenderJobContext(
                job_id=job_id,
                video_id=video_id,
                script_id=script_id,
                script_json=script_json,
                output_dir=job_output_dir,
            )

        ctx = self._contexts[job_id]

        # 진행률 알림
        progress, message = get_step_progress(step, 0.0)
        await notify_render_progress(
            job_id=ctx.job_id,
            video_id=ctx.video_id,
            status=RenderJobStatus.PROCESSING,
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
                status=RenderJobStatus.PROCESSING,
                step=step,
                progress=progress,
                message=f"{message} 완료",
            )

        except StorageUploadError as e:
            # Phase 34: Storage 업로드 실패 시 STORAGE_UPLOAD_FAILED 에러
            logger.error(f"Storage upload failed for job {job_id}: {e}")
            await notify_render_progress(
                job_id=ctx.job_id,
                video_id=ctx.video_id,
                status=RenderJobStatus.FAILED,
                step=step,
                progress=progress,
                message=f"스토리지 업로드 실패: {str(e)[:100]}",
            )
            raise
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
        # - HeyGen 엔진은 180초 제한이 있어, AI 서버에서 렌더 직전에 챕터/씬 수를 제한합니다.
        # - (백엔드에 저장된 스크립트가 길더라도, 렌더는 제한된 부분만 수행)
        engine = (self.config.render_engine or "").lower()
        settings = get_settings()
        max_chapters = int(getattr(settings, "SCRIPT_MAX_CHAPTERS", 0) or 0)
        max_scenes_per_chapter = int(getattr(settings, "SCRIPT_MAX_SCENES_PER_CHAPTER", 0) or 0)

        if engine.startswith("heygen") and (max_chapters > 0 or max_scenes_per_chapter > 0):
            full_scenes = self._extract_scenes(script)
            ctx.scenes = self._extract_scenes_limited(
                script,
                max_chapters=max_chapters,
                max_scenes_per_chapter=max_scenes_per_chapter,
            )
            logger.info(
                f"Script scenes limited for HeyGen: "
                f"from {len(full_scenes)} -> {len(ctx.scenes)} "
                f"(max_chapters={max_chapters}, max_scenes_per_chapter={max_scenes_per_chapter})"
            )
        else:
            ctx.scenes = self._extract_scenes(script)

        if not ctx.scenes:
            raise ValueError("No scenes found in script")

        ctx.validated = True
        logger.info(f"Script validated: {len(ctx.scenes)} scenes found")

    def _extract_scenes_limited(
        self,
        script: dict,
        max_chapters: int = 0,
        max_scenes_per_chapter: int = 0,
    ) -> List[SceneInfo]:
        """스크립트에서 SceneInfo를 추출하되, 챕터/씬 수를 제한합니다."""
        if "chapters" in script:
            chapters = script.get("chapters", [])
            if isinstance(chapters, list) and max_chapters > 0:
                chapters = chapters[:max_chapters]

            limited_script = {"chapters": []}
            for ch in chapters if isinstance(chapters, list) else []:
                if not isinstance(ch, dict):
                    continue
                scenes = ch.get("scenes", [])
                if isinstance(scenes, list) and max_scenes_per_chapter > 0:
                    scenes = scenes[:max_scenes_per_chapter]
                limited_script["chapters"].append({**ch, "scenes": scenes})

            return self._extract_scenes(limited_script)

        if "scenes" in script:
            scenes = script.get("scenes", [])
            if isinstance(scenes, list) and max_scenes_per_chapter > 0:
                scenes = scenes[:max_scenes_per_chapter]
            return self._extract_scenes({"scenes": scenes})

        # text/narration 등은 제한 의미가 없으므로 그대로 처리
        return self._extract_scenes(script)

    async def _generate_tts(self, ctx: RealRenderJobContext) -> None:
        """TTS 음성 생성."""
        logger.info(f"Generating TTS for job: {ctx.job_id}")

        # HeyGen 계열 엔진은 자체 TTS를 사용하므로 스킵
        if (self.config.render_engine or "").lower().startswith("heygen"):
            # duration 보정(가능하면 scene.duration_sec 합계)
            duration = 0.0
            for s in ctx.scenes:
                if s.duration_sec:
                    try:
                        duration += float(s.duration_sec)
                    except Exception:
                        continue
            ctx.duration_sec = duration
            logger.info(f"Skipping TTS (heygen engine): job={ctx.job_id}, duration_hint={duration:.2f}s")
            return

        # ✅ 씬별 TTS + (durationSec 기반) 무음 패딩 + 전체 concat
        # durationSec이 있는 render-spec이면 그 길이를 "목표 길이"로 보고 오디오를 늘립니다.
        from app.services.scene_audio_service import SceneAudioService
        import subprocess

        scene_dicts = []
        for s in ctx.scenes:
            scene_dicts.append(
                {
                    "scene_id": str(s.scene_id),
                    "narration": s.narration or "",
                    "duration_sec": s.duration_sec,  # 목표 길이(초) - 있으면 패딩
                }
            )

        scene_audio = SceneAudioService(tts_provider=self._get_tts())
        scene_results = await scene_audio.generate_scene_audios(
            scenes=scene_dicts,
            output_dir=ctx.output_dir,
        )

        # 씬 duration 업데이트(Composer/SRT 타이밍과 동기화)
        total_duration = 0.0
        for i, r in enumerate(scene_results):
            total_duration += float(r.duration_sec or 0.0)
            if i < len(ctx.scenes):
                ctx.scenes[i].duration_sec = float(r.duration_sec or ctx.scenes[i].duration_sec or 0.0)

        # 전체 오디오 concat
        if len(scene_results) == 1:
            ctx.tts_audio_path = scene_results[0].audio_path
        else:
            audio_paths = [r.audio_path for r in scene_results if r.audio_path and Path(r.audio_path).exists()]
            out_path = ctx.output_dir / "audio_full.mp3"

            # concat filter로 디코드 후 재인코딩(가장 안정)
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
            for p in audio_paths:
                cmd.extend(["-i", p])
            pads = "".join([f"[{i}:a]" for i in range(len(audio_paths))])
            filter_complex = f"{pads}concat=n={len(audio_paths)}:v=0:a=1[aout]"
            cmd.extend(
                [
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[aout]",
                    "-c:a",
                    "libmp3lame",
                    "-q:a",
                    "4",
                    str(out_path),
                ]
            )
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg audio concat failed: {result.stderr.decode()[-800:]}")
            ctx.tts_audio_path = str(out_path)

        ctx.duration_sec = total_duration
        logger.info(f"TTS generated (scene+pad): job={ctx.job_id}, duration={total_duration:.2f}s, audio={ctx.tts_audio_path}")

    async def _generate_subtitle(self, ctx: RealRenderJobContext) -> None:
        """자막 생성 (Composer에서 처리)."""
        logger.info(f"Subtitle will be generated during composition: {ctx.job_id}")
        # 실제 자막 생성은 compose 단계에서 VideoComposer가 처리

    async def _render_slides(self, ctx: RealRenderJobContext) -> None:
        """슬라이드 렌더링.

        Phase 37: animated 모드에서 씬 이미지 생성.
        - basic 모드: Composer에서 단색 배경 처리
        - animated 모드: VisualPlan → ImageAssetService → scene.image_path 설정
        - presentation 모드: 슬라이드는 VideoComposer(Pillow)에서 생성하므로 여기서 scene.image_path를 채우지 않음
          (채우면 '슬라이드(텍스트 포함)'가 우측 이미지 패널로 다시 들어가 중복/겹침이 발생할 수 있음)
        """
        if self.config.visual_style not in ("animated", "presentation"):
            logger.info(f"Basic mode - slides rendered during composition: {ctx.job_id}")
            return

        # ✅ presentation: scene.image_path는 "패널용 이미지"로 간주되는데,
        # 여기서 생성하는 scene_images는 이미 텍스트가 포함된 이미지가 될 수 있어 중복이 발생함.
        # 따라서 presentation에서는 생성 스킵. (백엔드에서 실제 이미지가 제공되는 경우에만 image_path를 사용)
        if self.config.visual_style == "presentation":
            logger.info(f"presentation mode - skipping scene_images generation: {ctx.job_id}")
            return

        logger.info(f"{self.config.visual_style} mode - generating scene images for job: {ctx.job_id}")

        # Phase 37: VisualPlanExtractor로 각 씬의 시각적 계획 추출
        extractor = self._get_plan_extractor()
        image_service = self._get_image_service()

        # 이미지 출력 디렉토리 (로컬 임시 폴더)
        image_dir = ctx.output_dir / "scene_images"
        image_dir.mkdir(parents=True, exist_ok=True)

        # 각 씬에 대해 VisualPlan 생성 → 이미지 생성
        for i, scene in enumerate(ctx.scenes):
            # VisualPlan 추출
            plan = extractor.extract(scene)

            # 이미지 생성
            image_path = image_service.generate_scene_image(
                plan=plan,
                output_dir=image_dir,
                scene_index=i,
            )

            # 씬에 이미지 경로 설정
            scene.image_path = image_path
            logger.debug(f"Scene {scene.scene_id} image generated: {image_path}")

        logger.info(f"Generated {len(ctx.scenes)} scene images for job: {ctx.job_id}")

    async def _compose_video(self, ctx: RealRenderJobContext) -> None:
        """영상 합성."""
        logger.info(f"Composing video for job: {ctx.job_id}")

        engine = (self.config.render_engine or "").lower()
        if engine == "heygen_pip":
            await self._compose_video_heygen_pip(ctx)
            return
        if engine == "heygen":
            await self._compose_video_heygen(ctx)
            return

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

    async def _compose_video_heygen(self, ctx: RealRenderJobContext) -> None:
        """HeyGen으로 영상 생성 후 다운로드하여 video_path로 저장."""
        from app.clients.heygen_client import HeyGenClient
        from app.utils.heygen_payload import build_heygen_generate_payload, build_heygen_video_inputs
        import httpx

        settings = get_settings()

        api_key = (settings.HEYGEN_API_KEY or "").strip()
        avatar_id = (settings.HEYGEN_AVATAR_ID or "").strip()
        voice_id = (settings.HEYGEN_VOICE_ID or "").strip()

        if not api_key or not avatar_id or not voice_id:
            raise ValueError("HEYGEN_API_KEY / HEYGEN_AVATAR_ID / HEYGEN_VOICE_ID 미설정 (VIDEO_RENDER_ENGINE=heygen)")

        # ctx.scenes 기반으로 HeyGen VideoScript 형태 구성
        chapter = {
            "chapter_id": 1,
            "title": "render_job",
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "on_screen_text": s.on_screen_text or s.caption,
                    "duration_sec": s.duration_sec,
                }
                for s in ctx.scenes
            ],
        }
        video_script = {"chapters": [chapter]}

        video_inputs = build_heygen_video_inputs(
            video_script,
            avatar_id=avatar_id,
            voice_id=voice_id,
            bg_type=settings.HEYGEN_BG_TYPE,
            bg_value=settings.HEYGEN_BG_VALUE,
        )
        heygen_w = int(getattr(settings, "HEYGEN_VIDEO_WIDTH", settings.VIDEO_WIDTH))
        heygen_h = int(getattr(settings, "HEYGEN_VIDEO_HEIGHT", settings.VIDEO_HEIGHT))
        payload = build_heygen_generate_payload(
            video_inputs,
            width=heygen_w,
            height=heygen_h,
        )

        client = HeyGenClient(api_key=api_key)
        heygen_video_id = await client.generate_video(payload)
        logger.info(
            f"HeyGen generate requested: job={ctx.job_id}, heygen_video_id={heygen_video_id}, requested={heygen_w}x{heygen_h}"
        )

        # poll
        video_url: Optional[str] = None
        poll_interval = int(
            settings.HEYGEN_POLL_INTERVAL_SEC_FAST
            if getattr(settings, "VIDEO_FAST_MODE", False)
            else settings.HEYGEN_POLL_INTERVAL_SEC
        )
        for i in range(int(settings.HEYGEN_MAX_POLLS)):
            status = await client.get_video_status(heygen_video_id)
            data = status.get("data", {}) if isinstance(status, dict) else {}
            s = (data.get("status") or "").lower()
            if s == "completed":
                video_url = data.get("video_url")
                break
            if s == "failed":
                try:
                    err = (data.get("error") or {}) if isinstance(data.get("error"), dict) else {}
                    code = err.get("code")
                    msg = err.get("message") or err.get("detail") or ""
                except Exception:
                    code, msg = None, ""
                raise RuntimeError(
                    f"HeyGen render failed: job={ctx.job_id}, requested={heygen_w}x{heygen_h}, "
                    f"code={code}, message={msg}, status={status}"
                )
            await asyncio.sleep(poll_interval)

        if not video_url:
            raise TimeoutError(f"HeyGen render polling timeout: job={ctx.job_id}, heygen_video_id={heygen_video_id}")

        # download
        out_path = ctx.output_dir / "video.mp4"
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as http:
            async with http.stream("GET", video_url) as resp:
                resp.raise_for_status()
                with open(out_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)

        ctx.video_path = str(out_path)
        logger.info(f"HeyGen video downloaded: job={ctx.job_id}, path={ctx.video_path}")

    async def _compose_video_heygen_pip(self, ctx: RealRenderJobContext) -> None:
        """옵션B: HeyGen(아바타+음성) + FFmpeg(슬라이드 위 PIP 합성)으로 최종 video_path 생성."""
        import httpx
        import subprocess
        import tempfile

        from app.clients.heygen_client import HeyGenClient
        from app.utils.heygen_payload import (
            build_heygen_generate_payload,
            build_heygen_video_inputs,
        )

        settings = get_settings()

        api_key = (settings.HEYGEN_API_KEY or "").strip()
        avatar_id = (settings.HEYGEN_AVATAR_ID or "").strip()
        voice_id = (settings.HEYGEN_VOICE_ID or "").strip()
        if not api_key or not avatar_id or not voice_id:
            raise ValueError(
                "HEYGEN_API_KEY / HEYGEN_AVATAR_ID / HEYGEN_VOICE_ID 미설정 (VIDEO_RENDER_ENGINE=heygen_pip)"
            )

        # ---------------------------------------------------------------------
        # 1) HeyGen으로 아바타 영상 생성 (그린 배경 기본 → chromakey 합성)
        # ---------------------------------------------------------------------
        # HeyGen PIP 배경색: 프레젠테이션 배경과 동일하게 맞춤(기본 #101216)
        # - HEYGEN_PIP_BG_COLOR가 있으면 우선
        # - 없으면 PRESENTATION_BG_HEX 사용
        pip_bg_color = (os.getenv("HEYGEN_PIP_BG_COLOR") or os.getenv("PRESENTATION_BG_HEX") or "#101216").strip()
        pip_bg_type = "color"

        chapter = {
            "chapter_id": 1,
            "title": "render_job",
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "on_screen_text": s.on_screen_text or s.caption,
                    "duration_sec": s.duration_sec,
                }
                for s in ctx.scenes
            ],
        }
        video_script = {"chapters": [chapter]}

        video_inputs = build_heygen_video_inputs(
            video_script,
            avatar_id=avatar_id,
            voice_id=voice_id,
            bg_type=pip_bg_type,
            bg_value=pip_bg_color,
        )
        heygen_w = int(getattr(settings, "HEYGEN_VIDEO_WIDTH", settings.VIDEO_WIDTH))
        heygen_h = int(getattr(settings, "HEYGEN_VIDEO_HEIGHT", settings.VIDEO_HEIGHT))
        payload = build_heygen_generate_payload(
            video_inputs,
            width=heygen_w,
            height=heygen_h,
        )

        client = HeyGenClient(api_key=api_key)
        heygen_video_id = await client.generate_video(payload)
        logger.info(
            f"HeyGen(PIP) generate requested: job={ctx.job_id}, heygen_video_id={heygen_video_id}, requested={heygen_w}x{heygen_h}"
        )

        # poll
        video_url: Optional[str] = None
        poll_interval = int(
            settings.HEYGEN_POLL_INTERVAL_SEC_FAST
            if getattr(settings, "VIDEO_FAST_MODE", False)
            else settings.HEYGEN_POLL_INTERVAL_SEC
        )
        for i in range(int(settings.HEYGEN_MAX_POLLS)):
            status = await client.get_video_status(heygen_video_id)
            data = status.get("data", {}) if isinstance(status, dict) else {}
            s = (data.get("status") or "").lower()
            if s == "completed":
                video_url = data.get("video_url")
                break
            if s == "failed":
                # 플랜 제한(RESOLUTION_NOT_ALLOWED) 등은 메시지를 더 명확히 전달
                try:
                    err = (data.get("error") or {}) if isinstance(data.get("error"), dict) else {}
                    code = err.get("code")
                    msg = err.get("message") or err.get("detail") or ""
                except Exception:
                    code, msg = None, ""
                raise RuntimeError(
                    f"HeyGen render failed: job={ctx.job_id}, requested={heygen_w}x{heygen_h}, "
                    f"code={code}, message={msg}, status={status}"
                )
            await asyncio.sleep(poll_interval)

        if not video_url:
            raise TimeoutError(
                f"HeyGen render polling timeout: job={ctx.job_id}, heygen_video_id={heygen_video_id}"
            )

        avatar_mp4 = ctx.output_dir / "avatar.mp4"
        # 재사용(캐시): 동일 job 재시도 시 기존 다운로드가 있으면 재다운로드 생략
        if avatar_mp4.exists() and avatar_mp4.stat().st_size > 0:
            logger.info(f"HeyGen(PIP) avatar reuse: job={ctx.job_id}, path={avatar_mp4}")
        else:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as http:
                async with http.stream("GET", video_url) as resp:
                    resp.raise_for_status()
                    with open(avatar_mp4, "wb") as f:
                        async for chunk in resp.aiter_bytes():
                            f.write(chunk)
            logger.info(f"HeyGen(PIP) avatar downloaded: job={ctx.job_id}, path={avatar_mp4}")

        # ---------------------------------------------------------------------
        # 2) 슬라이드(프레젠테이션) 영상 생성 (스크립트 내용 표시)
        #    - audio는 avatar에서 추출한 것을 사용 (타임라인 동기화)
        # ---------------------------------------------------------------------
        avatar_audio = ctx.output_dir / "avatar_audio.m4a"
        # 재사용(캐시): 기존 추출 파일이 있으면 스킵
        if avatar_audio.exists() and avatar_audio.stat().st_size > 0:
            logger.info(f"HeyGen(PIP) avatar audio reuse: job={ctx.job_id}, path={avatar_audio}")
        else:
            extract_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(avatar_mp4),
                "-vn",
                "-c:a",
                "copy",
                str(avatar_audio),
            ]
            r = subprocess.run(extract_cmd, capture_output=True, timeout=120)
            if r.returncode != 0:
                # fallback: 재인코딩
                extract_cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(avatar_mp4),
                    "-vn",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    str(avatar_audio),
                ]
                r2 = subprocess.run(extract_cmd, capture_output=True, timeout=120)
                if r2.returncode != 0:
                    raise RuntimeError(
                        f"Failed to extract avatar audio: {r2.stderr.decode()[:300]}"
                    )

        slides_result = None  # FAST 모드에서는 None일 수 있음

        # FAST 모드: 슬라이드 생성 생략 → 단색 배경(bg) + avatar_audio로 bg.mp4 생성 (매우 빠름)
        fast_mode = bool(getattr(settings, "VIDEO_FAST_MODE", False))
        skip_slides = bool(getattr(settings, "VIDEO_SKIP_SLIDES", False))
        if fast_mode and skip_slides:
            bg_mp4 = ctx.output_dir / "bg.mp4"
            if not (bg_mp4.exists() and bg_mp4.stat().st_size > 0):
                bg_hex = (os.getenv("PRESENTATION_BG_HEX") or "#101216").strip().lstrip("#")
                color = f"0x{bg_hex}"
                out_w = int(settings.VIDEO_WIDTH)
                out_h = int(settings.VIDEO_HEIGHT)
                fps = int(settings.VIDEO_FPS)
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={color}:s={out_w}x{out_h}:r={fps}",
                    "-i",
                    str(avatar_audio),
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-b:v",
                    "2M",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    str(bg_mp4),
                ]
                rr = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if rr.returncode != 0:
                    raise RuntimeError(f"Failed to build bg video: {rr.stderr[:500]}")
            slides_mp4 = bg_mp4
            ctx.subtitle_path = None
        else:
            composer = self._get_composer()
            slides_dir = ctx.output_dir / "slides"
            slides_result = await composer.compose(
                scenes=ctx.scenes,
                audio_path=str(avatar_audio),
                output_dir=slides_dir,
                job_id="slides",
            )
            slides_mp4 = Path(slides_result.video_path)
            ctx.subtitle_path = slides_result.subtitle_path  # 자막은 슬라이드 타임라인 기준

        # ---------------------------------------------------------------------
        # 3) FFmpeg overlay (좌/우/하단) + chromakey
        # ---------------------------------------------------------------------
        pos = (os.getenv("PIP_AVATAR_POSITION") or "right").strip().lower()
        try:
            ratio = float(os.getenv("PIP_AVATAR_WIDTH_RATIO", "0.32"))
        except Exception:
            ratio = 0.32
        ratio = max(0.15, min(ratio, 0.60))

        try:
            margin = int(os.getenv("PIP_AVATAR_MARGIN", "40"))
        except Exception:
            margin = 40
        margin = max(0, min(margin, 200))

        out_w = int(settings.VIDEO_WIDTH)
        out_h = int(settings.VIDEO_HEIGHT)
        avatar_w = max(int(out_w * ratio), 200)

        if pos == "left":
            x_expr = str(margin)
            y_expr = f"H-h-{margin}"
        elif pos == "bottom":
            x_expr = "(W-w)/2"
            y_expr = f"H-h-{margin}"
        else:  # right default
            x_expr = f"W-w-{margin}"
            y_expr = f"H-h-{margin}"

        # chroma key 설정
        chroma_enabled = (os.getenv("PIP_CHROMA_KEY") or "1").strip() not in ("0", "false", "False")
        # 크로마키 색상: 기본은 PIP 배경색과 동일하게(프레젠테이션 배경에 맞춤)
        def _hex_to_0x(v: str) -> str:
            vv = (v or "").strip()
            if vv.startswith("0x"):
                return vv
            if vv.startswith("#"):
                vv = vv[1:]
            if len(vv) == 6:
                return "0x" + vv.upper()
            return "0x101216"

        chroma_hex = (os.getenv("PIP_CHROMA_COLOR") or _hex_to_0x(pip_bg_color)).strip()
        chroma_sim = os.getenv("PIP_CHROMA_SIMILARITY") or "0.12"
        chroma_blend = os.getenv("PIP_CHROMA_BLEND") or "0.08"

        # PIP 스타일: 라운드/그림자
        round_enabled = (os.getenv("PIP_ROUND") or "1").strip() not in ("0", "false", "False")
        try:
            round_r = int(os.getenv("PIP_ROUND_RADIUS", "24"))
        except Exception:
            round_r = 24
        round_r = max(0, min(round_r, 80))

        shadow_enabled = (os.getenv("PIP_SHADOW") or "1").strip() not in ("0", "false", "False")
        try:
            sh_dx = int(os.getenv("PIP_SHADOW_DX", "10"))
            sh_dy = int(os.getenv("PIP_SHADOW_DY", "10"))
            sh_blur = int(os.getenv("PIP_SHADOW_BLUR", "12"))
        except Exception:
            sh_dx, sh_dy, sh_blur = 10, 10, 12
        sh_blur = max(0, min(sh_blur, 40))
        try:
            sh_op = float(os.getenv("PIP_SHADOW_OPACITY", "0.45"))
        except Exception:
            sh_op = 0.45
        sh_op = max(0.0, min(sh_op, 1.0))

        av_chain = "setpts=PTS-STARTPTS"
        if chroma_enabled:
            av_chain += f",chromakey={chroma_hex}:{chroma_sim}:{chroma_blend}"
        av_chain += f",scale={avatar_w}:-1"

        # 라운드 마스크(필요 시): RGBA로 만든 뒤 alpha를 둥글게
        # (간단 구현: 코너 영역만 잘라내는 geq alpha)
        if round_enabled and round_r > 0:
            r = round_r
            alpha_expr = (
                f"if("
                f"lt(X,{r})*lt(Y,{r})*gt(hypot({r}-X,{r}-Y),{r}),0,"
                f"if("
                f"lt(X,{r})*gt(Y,H-{r})*gt(hypot({r}-X,Y-(H-{r})),{r}),0,"
                f"if("
                f"gt(X,W-{r})*lt(Y,{r})*gt(hypot(X-(W-{r}),{r}-Y),{r}),0,"
                f"if("
                f"gt(X,W-{r})*gt(Y,H-{r})*gt(hypot(X-(W-{r}),Y-(H-{r})),{r}),0,255"
                f"))))"
            )
            # geq는 a만 주면 에러가 나므로 r/g/b도 명시해 원본을 유지하면서 alpha만 마스킹
            # (format=yuva444p로 변환 후 적용)
            av_chain += f",format=yuva444p,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha_expr}'"

        # 그림자: 아바타를 검정 반투명으로 만들고 blur 후 뒤에 깔기
        filter_parts = [f"[0:v]setpts=PTS-STARTPTS[bg]", f"[1:v]{av_chain}[av]"]

        if shadow_enabled and sh_blur > 0 and sh_op > 0:
            filter_parts.append("[av]split=2[av_fg][av_sh]")
            # alpha 유지 + 색상 검정 + blur
            filter_parts.append(
                f"[av_sh]colorchannelmixer=rr=0:gg=0:bb=0:aa={sh_op:.3f},boxblur={sh_blur}:1[shadow]"
            )
            filter_parts.append(
                f"[bg][shadow]overlay={x_expr}+{sh_dx}:{y_expr}+{sh_dy}[bg2]"
            )
            filter_parts.append(f"[bg2][av_fg]overlay={x_expr}:{y_expr}[vout]")
        else:
            filter_parts.append(f"[bg][av]overlay={x_expr}:{y_expr}[vout]")

        filter_complex = ";".join(filter_parts)

        final_mp4 = ctx.output_dir / "video.mp4"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as _:
            pass  # placeholder to avoid lint warnings for tempfile import

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(slides_mp4),
            "-i",
            str(avatar_mp4),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            # 오디오는 slides(mp4)에 이미 포함되어 있음(avatar_audio 기반).
            # avatar.mp4 오디오 스트림 유무/인덱스 차이로 실패하는 경우를 방지하기 위해 slides 오디오를 사용.
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-b:v",
            "2M",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final_mp4),
        ]
        logger.debug(f"FFmpeg heygen_pip command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg heygen_pip failed: {result.stderr.decode()[-1200:]}"
            )

        # 썸네일 추출(최종 영상 기준) - FAST 모드/설정에서 스킵 가능
        skip_thumb = bool(getattr(settings, "VIDEO_SKIP_THUMBNAIL", False))
        thumb_path = ctx.output_dir / "thumb.jpg"
        if skip_thumb:
            logger.info("Thumbnail generation skipped (VIDEO_SKIP_THUMBNAIL)")
        else:
            thumb_cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                "1",
                "-i",
                str(final_mp4),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(thumb_path),
            ]
            subprocess.run(thumb_cmd, capture_output=True, timeout=60)

        ctx.video_path = str(final_mp4)

        # duration/thumbnail은 slides_result가 없을 수 있음(FAST 모드)
        if not skip_thumb and thumb_path.exists():
            ctx.thumbnail_path = str(thumb_path)
        else:
            ctx.thumbnail_path = getattr(slides_result, "thumbnail_path", "") or ""

        duration_sec: Optional[float] = getattr(slides_result, "duration_sec", None)
        if duration_sec is None:
            # 최종 mp4 기준으로 duration 측정(가벼운 ffprobe)
            try:
                probe = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "quiet",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        str(final_mp4),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if probe.returncode == 0:
                    duration_sec = float((probe.stdout or "").strip())
            except Exception:
                duration_sec = None
        ctx.duration_sec = float(duration_sec or 0.0)
        logger.info(
            f"HeyGen(PIP) composed: job={ctx.job_id}, video={ctx.video_path}, subtitle={ctx.subtitle_path}"
        )

    async def _upload_assets(self, ctx: RealRenderJobContext) -> None:
        """에셋 업로드.

        업로드 정책(요청 반영):
        - mp4만 업로드
        - 폴더 depth를 늘리지 않고 videos/ 아래에 파일 1개로 저장
          예) videos/{job_id}.mp4
        """
        logger.info(f"Uploading assets for job: {ctx.job_id}")

        storage = self._get_storage()

        # mp4만 업로드 (subtitles/thumb 업로드는 생략)
        mp4_key = f"videos/{ctx.job_id}.mp4"
        if ctx.video_path and Path(ctx.video_path).exists():
            result = await storage.put_file(
                ctx.video_path,
                mp4_key,
                "video/mp4",
            )
            ctx.video_url = result.url
            logger.info(f"Video uploaded: {ctx.video_url}")

    async def _finalize(self, ctx: RealRenderJobContext) -> None:
        """최종화."""
        logger.info(f"Finalizing job: {ctx.job_id}")

        # 성공 알림
        await notify_render_progress(
            job_id=ctx.job_id,
            video_id=ctx.video_id,
            status=RenderJobStatus.COMPLETED,
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
    settings = get_settings()
    if _real_renderer is None:
        _real_renderer = RealVideoRenderer()
    else:
        # ✅ 설정(특히 VIDEO_RENDER_ENGINE/VIDEO_VISUAL_STYLE)이 바뀌면 싱글톤을 갱신
        # uvicorn --reload 환경에서 .env 수정 후에도 이전 설정이 고착되는 문제 방지
        cur_engine = (_real_renderer.config.render_engine or "").lower()
        want_engine = (settings.VIDEO_RENDER_ENGINE or "ffmpeg").lower()
        cur_style = (_real_renderer.config.visual_style or "").lower()
        want_style = (settings.VIDEO_VISUAL_STYLE or "basic").lower()
        if cur_engine != want_engine or cur_style != want_style:
            _real_renderer = RealVideoRenderer()
    return _real_renderer


def clear_real_video_renderer() -> None:
    """RealVideoRenderer 싱글톤 초기화 (테스트용)."""
    global _real_renderer
    _real_renderer = None
