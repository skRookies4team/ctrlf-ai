"""
Phase 27: MVP Video Renderer

최소 기능 영상 렌더러 구현.

MVP 요구사항:
- TTS 음성 트랙이 들어간 mp4
- 자막 파일(SRT/VTT) 생성
- 썸네일 1장 생성
- 씬별 배경 + 캡션 텍스트 오버레이

기술 선택:
- TTS: gTTS (Google Text-to-Speech) 또는 edge-tts
- 영상 합성: moviepy 또는 opencv + ffmpeg
- 자막: SRT 포맷 직접 생성

의존성이 없으면 Mock 모드로 동작 (테스트용).
"""

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.models.video_render import RenderedAssets, RenderStep
from app.services.video_render_service import VideoRenderer

logger = get_logger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class RendererConfig:
    """렌더러 설정."""
    output_dir: str = "./video_output"
    tts_lang: str = "ko"
    video_width: int = 1280
    video_height: int = 720
    fps: int = 24
    background_color: tuple = (30, 30, 30)  # Dark gray
    text_color: tuple = (255, 255, 255)  # White
    font_size: int = 48
    mock_mode: bool = False  # True면 실제 렌더링 없이 mock 파일 생성


# =============================================================================
# Job Context (파이프라인 중간 결과 저장)
# =============================================================================


@dataclass
class RenderJobContext:
    """렌더 잡 컨텍스트.

    파이프라인 실행 중 중간 결과를 저장합니다.
    """
    job_id: str
    script_json: Dict[str, Any]
    output_dir: Path

    # 단계별 결과
    validated: bool = False
    tts_audio_path: Optional[str] = None
    subtitle_path: Optional[str] = None
    slides_paths: List[str] = None
    composed_video_path: Optional[str] = None
    final_video_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    duration_sec: float = 0.0

    def __post_init__(self):
        if self.slides_paths is None:
            self.slides_paths = []


# =============================================================================
# MVP Video Renderer
# =============================================================================


class MVPVideoRenderer(VideoRenderer):
    """MVP 영상 렌더러.

    최소 기능으로 영상을 생성합니다.
    실제 의존성(gTTS, moviepy)이 없으면 Mock 모드로 동작합니다.

    Usage:
        renderer = MVPVideoRenderer()
        # 서비스에 렌더러 설정
        service.set_renderer(renderer)
    """

    def __init__(self, config: Optional[RendererConfig] = None) -> None:
        """렌더러 초기화."""
        self.config = config or RendererConfig()
        self._contexts: Dict[str, RenderJobContext] = {}

        # 출력 디렉토리 생성
        self._output_dir = Path(self.config.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # 의존성 체크
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        """의존성 체크."""
        try:
            import gtts
            self._has_gtts = True
        except ImportError:
            self._has_gtts = False
            logger.warning("gTTS not available, TTS will be mocked")

        try:
            import moviepy.editor
            self._has_moviepy = True
        except ImportError:
            self._has_moviepy = False
            logger.warning("moviepy not available, video rendering will be mocked")

        try:
            from PIL import Image, ImageDraw, ImageFont
            self._has_pillow = True
        except ImportError:
            self._has_pillow = False
            logger.warning("Pillow not available, thumbnails will be mocked")

        # Mock 모드 자동 설정
        if not (self._has_gtts and self._has_moviepy):
            self.config.mock_mode = True
            logger.info("Mock mode enabled due to missing dependencies")

    async def execute_step(
        self,
        step: RenderStep,
        script_json: dict,
        job_id: str,
    ) -> None:
        """파이프라인 단계 실행."""
        # 컨텍스트 생성/조회
        if job_id not in self._contexts:
            job_output_dir = self._output_dir / job_id
            job_output_dir.mkdir(parents=True, exist_ok=True)
            self._contexts[job_id] = RenderJobContext(
                job_id=job_id,
                script_json=script_json,
                output_dir=job_output_dir,
            )

        ctx = self._contexts[job_id]

        # 단계별 실행
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

    async def get_rendered_assets(self, job_id: str) -> RenderedAssets:
        """렌더링된 에셋 조회."""
        ctx = self._contexts.get(job_id)
        if not ctx:
            raise ValueError(f"Job context not found: {job_id}")

        return RenderedAssets(
            mp4_path=ctx.final_video_path or "",
            thumbnail_path=ctx.thumbnail_path or "",
            subtitle_path=ctx.subtitle_path or "",
            duration_sec=ctx.duration_sec,
        )

    # =========================================================================
    # Pipeline Steps
    # =========================================================================

    async def _validate_script(self, ctx: RenderJobContext) -> None:
        """스크립트 검증."""
        logger.info(f"Validating script for job: {ctx.job_id}")

        script = ctx.script_json

        # 필수 필드 체크
        if "chapters" not in script and "scenes" not in script:
            # 단순 텍스트 스크립트도 허용
            if "text" not in script and "narration" not in script:
                raise ValueError("Script must have 'chapters', 'scenes', 'text', or 'narration'")

        ctx.validated = True
        logger.info(f"Script validated for job: {ctx.job_id}")

    async def _generate_tts(self, ctx: RenderJobContext) -> None:
        """TTS 음성 생성."""
        logger.info(f"Generating TTS for job: {ctx.job_id}")

        # 나레이션 텍스트 추출
        narration_text = self._extract_narration(ctx.script_json)

        if self.config.mock_mode or not self._has_gtts:
            # Mock 모드: 빈 오디오 파일 생성
            audio_path = ctx.output_dir / "audio.mp3"
            audio_path.write_bytes(b"")  # Empty file
            ctx.tts_audio_path = str(audio_path)
            ctx.duration_sec = 10.0  # Mock duration
            logger.info(f"Mock TTS created: {audio_path}")
        else:
            # 실제 TTS 생성
            from gtts import gTTS

            tts = gTTS(text=narration_text, lang=self.config.tts_lang)
            audio_path = ctx.output_dir / "audio.mp3"
            tts.save(str(audio_path))
            ctx.tts_audio_path = str(audio_path)

            # 오디오 길이 계산
            ctx.duration_sec = await self._get_audio_duration(str(audio_path))
            logger.info(f"TTS generated: {audio_path}, duration={ctx.duration_sec}s")

    async def _generate_subtitle(self, ctx: RenderJobContext) -> None:
        """자막 파일 생성 (SRT 포맷)."""
        logger.info(f"Generating subtitle for job: {ctx.job_id}")

        # 자막 텍스트 추출
        captions = self._extract_captions(ctx.script_json)

        # SRT 생성
        srt_content = self._generate_srt(captions, ctx.duration_sec)
        subtitle_path = ctx.output_dir / "subtitle.srt"
        subtitle_path.write_text(srt_content, encoding="utf-8")

        ctx.subtitle_path = str(subtitle_path)
        logger.info(f"Subtitle generated: {subtitle_path}")

    async def _render_slides(self, ctx: RenderJobContext) -> None:
        """슬라이드 이미지 생성."""
        logger.info(f"Rendering slides for job: {ctx.job_id}")

        # 씬 정보 추출
        scenes = self._extract_scenes(ctx.script_json)

        if self.config.mock_mode or not self._has_pillow:
            # Mock 모드: 빈 이미지 파일 생성
            for i, scene in enumerate(scenes):
                slide_path = ctx.output_dir / f"slide_{i:03d}.png"
                slide_path.write_bytes(b"")  # Empty file
                ctx.slides_paths.append(str(slide_path))
            logger.info(f"Mock slides created: {len(ctx.slides_paths)} slides")
        else:
            # 실제 슬라이드 생성
            from PIL import Image, ImageDraw, ImageFont

            for i, scene in enumerate(scenes):
                img = Image.new(
                    "RGB",
                    (self.config.video_width, self.config.video_height),
                    self.config.background_color,
                )
                draw = ImageDraw.Draw(img)

                # 텍스트 그리기
                text = scene.get("caption", scene.get("text", f"Scene {i + 1}"))
                try:
                    font = ImageFont.truetype("arial.ttf", self.config.font_size)
                except:
                    font = ImageFont.load_default()

                # 텍스트 중앙 정렬
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                x = (self.config.video_width - text_width) // 2
                y = (self.config.video_height - text_height) // 2
                draw.text((x, y), text, fill=self.config.text_color, font=font)

                slide_path = ctx.output_dir / f"slide_{i:03d}.png"
                img.save(str(slide_path))
                ctx.slides_paths.append(str(slide_path))

            logger.info(f"Slides rendered: {len(ctx.slides_paths)} slides")

    async def _compose_video(self, ctx: RenderJobContext) -> None:
        """영상 합성."""
        logger.info(f"Composing video for job: {ctx.job_id}")

        if self.config.mock_mode or not self._has_moviepy:
            # Mock 모드: 빈 비디오 파일 생성
            video_path = ctx.output_dir / "video.mp4"
            video_path.write_bytes(b"")  # Empty file
            ctx.composed_video_path = str(video_path)
            logger.info(f"Mock video created: {video_path}")
        else:
            # 실제 비디오 합성
            from moviepy.editor import (
                AudioFileClip,
                CompositeVideoClip,
                ImageClip,
                concatenate_videoclips,
            )

            # 슬라이드 클립 생성
            if not ctx.slides_paths:
                # 슬라이드 없으면 단색 배경
                from moviepy.editor import ColorClip
                video_clip = ColorClip(
                    size=(self.config.video_width, self.config.video_height),
                    color=self.config.background_color,
                    duration=ctx.duration_sec,
                )
            else:
                # 슬라이드별 시간 계산
                slide_duration = ctx.duration_sec / len(ctx.slides_paths)
                clips = []
                for slide_path in ctx.slides_paths:
                    clip = ImageClip(slide_path, duration=slide_duration)
                    clips.append(clip)
                video_clip = concatenate_videoclips(clips)

            # 오디오 추가
            if ctx.tts_audio_path and os.path.exists(ctx.tts_audio_path):
                audio_clip = AudioFileClip(ctx.tts_audio_path)
                video_clip = video_clip.set_audio(audio_clip)

            # 비디오 저장
            video_path = ctx.output_dir / "video.mp4"
            video_clip.write_videofile(
                str(video_path),
                fps=self.config.fps,
                codec="libx264",
                audio_codec="aac",
                verbose=False,
                logger=None,
            )
            ctx.composed_video_path = str(video_path)
            logger.info(f"Video composed: {video_path}")

    async def _upload_assets(self, ctx: RenderJobContext) -> None:
        """에셋 업로드 (MVP: 로컬 저장)."""
        logger.info(f"Uploading assets for job: {ctx.job_id}")

        # MVP에서는 로컬 경로를 URL로 사용
        # 실제 환경에서는 S3 등에 업로드 후 URL 반환

        ctx.final_video_path = ctx.composed_video_path
        logger.info(f"Assets uploaded (local): {ctx.final_video_path}")

    async def _finalize(self, ctx: RenderJobContext) -> None:
        """최종화 (썸네일 생성 등)."""
        logger.info(f"Finalizing for job: {ctx.job_id}")

        # 썸네일 생성
        if ctx.slides_paths:
            # 첫 번째 슬라이드를 썸네일로 사용
            ctx.thumbnail_path = ctx.slides_paths[0]
        else:
            # 썸네일 없으면 생성
            thumbnail_path = ctx.output_dir / "thumbnail.png"
            if self.config.mock_mode or not self._has_pillow:
                thumbnail_path.write_bytes(b"")
            else:
                from PIL import Image
                img = Image.new(
                    "RGB",
                    (self.config.video_width, self.config.video_height),
                    self.config.background_color,
                )
                img.save(str(thumbnail_path))
            ctx.thumbnail_path = str(thumbnail_path)

        logger.info(f"Finalized: thumbnail={ctx.thumbnail_path}")

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _extract_narration(self, script: dict) -> str:
        """스크립트에서 나레이션 텍스트 추출."""
        texts = []

        # chapters > scenes > narration 구조
        if "chapters" in script:
            for chapter in script["chapters"]:
                for scene in chapter.get("scenes", []):
                    if "narration" in scene:
                        texts.append(scene["narration"])

        # scenes > narration 구조
        elif "scenes" in script:
            for scene in script["scenes"]:
                if "narration" in scene:
                    texts.append(scene["narration"])

        # 단순 텍스트
        elif "narration" in script:
            texts.append(script["narration"])
        elif "text" in script:
            texts.append(script["text"])

        return " ".join(texts) if texts else "영상 콘텐츠입니다."

    def _extract_captions(self, script: dict) -> List[Dict[str, Any]]:
        """스크립트에서 자막 정보 추출."""
        captions = []

        # chapters > scenes > caption 구조
        if "chapters" in script:
            for chapter in script["chapters"]:
                for scene in chapter.get("scenes", []):
                    if "caption" in scene:
                        captions.append({"text": scene["caption"]})
                    elif "narration" in scene:
                        captions.append({"text": scene["narration"]})

        # scenes > caption 구조
        elif "scenes" in script:
            for scene in script["scenes"]:
                if "caption" in scene:
                    captions.append({"text": scene["caption"]})
                elif "narration" in scene:
                    captions.append({"text": scene["narration"]})

        # 단순 텍스트
        elif "text" in script:
            captions.append({"text": script["text"]})

        return captions if captions else [{"text": "영상 콘텐츠입니다."}]

    def _extract_scenes(self, script: dict) -> List[Dict[str, Any]]:
        """스크립트에서 씬 정보 추출."""
        scenes = []

        # chapters > scenes 구조
        if "chapters" in script:
            for chapter in script["chapters"]:
                scenes.extend(chapter.get("scenes", []))

        # scenes 구조
        elif "scenes" in script:
            scenes = script["scenes"]

        # 단순 텍스트 → 단일 씬
        elif "text" in script or "narration" in script:
            scenes = [{"text": script.get("text", script.get("narration", ""))}]

        return scenes if scenes else [{"text": "영상 콘텐츠입니다."}]

    def _generate_srt(self, captions: List[Dict[str, Any]], total_duration: float) -> str:
        """SRT 자막 파일 생성."""
        if not captions:
            return ""

        caption_duration = total_duration / len(captions)
        srt_lines = []

        for i, caption in enumerate(captions):
            start_sec = i * caption_duration
            end_sec = (i + 1) * caption_duration

            start_time = self._format_srt_time(start_sec)
            end_time = self._format_srt_time(end_sec)

            srt_lines.append(f"{i + 1}")
            srt_lines.append(f"{start_time} --> {end_time}")
            srt_lines.append(caption.get("text", ""))
            srt_lines.append("")

        return "\n".join(srt_lines)

    def _format_srt_time(self, seconds: float) -> str:
        """초를 SRT 시간 포맷으로 변환 (HH:MM:SS,mmm)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    async def _get_audio_duration(self, audio_path: str) -> float:
        """오디오 파일 길이 반환."""
        try:
            from moviepy.editor import AudioFileClip
            clip = AudioFileClip(audio_path)
            duration = clip.duration
            clip.close()
            return duration
        except Exception as e:
            logger.warning(f"Failed to get audio duration: {e}")
            return 10.0  # Default


# =============================================================================
# Singleton Instance
# =============================================================================


_renderer: Optional[MVPVideoRenderer] = None


def get_mvp_video_renderer() -> MVPVideoRenderer:
    """MVPVideoRenderer 싱글톤 인스턴스 반환."""
    global _renderer
    if _renderer is None:
        _renderer = MVPVideoRenderer()
    return _renderer
