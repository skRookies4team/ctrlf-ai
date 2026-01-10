"""
Phase 32 + Phase 37: Video Composer (FFmpeg)

FFmpeg를 사용한 영상 합성 서비스.

기능:
- 씬별 이미지 + 오디오 → MP4 합성
- SRT 자막 파일 생성
- 썸네일 추출

Phase 37 추가 기능:
- animated 모드: Ken Burns(zoompan) + fade 전환 효과
- 씬 이미지 기반 영상 합성

의존성:
- ffmpeg: 시스템에 설치 필요 (Docker에서는 기본 포함)
- ffprobe: 오디오/비디오 정보 조회

환경변수:
- FFMPEG_PATH: ffmpeg 바이너리 경로 (기본: ffmpeg)
- FFPROBE_PATH: ffprobe 바이너리 경로 (기본: ffprobe)
- VIDEO_VISUAL_STYLE: basic | animated | presentation (기본: basic)
- VIDEO_FONT_FILE: drawtext용 폰트 파일 경로 (한글 깨짐 방지, 선택)
- VIDEO_TEXT_MAX_CHARS_PER_LINE: drawtext 한 줄 최대 글자수 (기본: 32)
- VIDEO_TEXT_MAX_LINES: drawtext 최대 줄 수 (기본: 3)
"""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class ComposerConfig:
    """Video Composer 설정."""
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    video_width: int = 1280
    video_height: int = 720
    fps: int = 24
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    audio_bitrate: str = "128k"
    video_bitrate: str = "2M"
    preset: str = "medium"  # ultrafast, superfast, fast, medium, slow
    background_color: str = "0x1E1E1E"  # Dark gray
    text_color: str = "white"
    font_size: int = 48
    font_file: Optional[str] = None  # 폰트 파일 경로 (None이면 기본)

    # Phase 37: Animated 모드 설정
    visual_style: str = "basic"  # "basic" | "animated" | "presentation"
    fade_duration: float = 0.5  # 씬 전환 fade 시간 (초)
    kenburns_zoom: float = 1.1  # Ken Burns 줌 비율 (1.0 = 줌 없음)


@dataclass
class SceneInfo:
    """씬 정보."""
    scene_id: int
    narration: str
    caption: Optional[str] = None
    on_screen_text: Optional[str] = None
    duration_sec: Optional[float] = None
    audio_path: Optional[str] = None
    image_path: Optional[str] = None


@dataclass
class ComposedVideo:
    """합성된 비디오 결과."""
    video_path: str
    subtitle_path: str
    thumbnail_path: str
    duration_sec: float
    scenes: List[SceneInfo]


# =============================================================================
# Video Composer Service
# =============================================================================


class VideoComposer:
    """FFmpeg 기반 영상 합성 서비스.

    Usage:
        composer = VideoComposer()

        # 씬 정보와 오디오로 비디오 생성
        result = await composer.compose(
            scenes=[SceneInfo(1, "나레이션 텍스트", "화면 자막")],
            audio_path="audio.mp3",
            output_dir="./output",
        )
    """

    def __init__(self, config: Optional[ComposerConfig] = None):
        self.config = config or ComposerConfig()
        self._ffmpeg = os.getenv("FFMPEG_PATH", self.config.ffmpeg_path)
        self._ffprobe = os.getenv("FFPROBE_PATH", self.config.ffprobe_path)

        # 전체 영상 길이 상한(초) - settings/env 기반
        try:
            from app.core.config import get_settings

            settings = get_settings()
            self._max_video_duration_sec: float = float(getattr(settings, "MAX_VIDEO_DURATION_SEC", 0) or 0)
            self._skip_subtitles: bool = bool(getattr(settings, "VIDEO_SKIP_SUBTITLES", False))
            self._skip_thumbnail: bool = bool(getattr(settings, "VIDEO_SKIP_THUMBNAIL", False))
        except Exception:
            # 설정 로딩 실패 시 제한 없음
            self._max_video_duration_sec = 0.0
            self._skip_subtitles = False
            self._skip_thumbnail = False

        # 한글 텍스트(drawtext) 깨짐 방지: fontfile 지정 지원
        # 우선순위: VIDEO_FONT_FILE(env) > ComposerConfig.font_file > auto-detect
        env_font = (os.getenv("VIDEO_FONT_FILE") or "").strip()
        if env_font:
            self.config.font_file = env_font
        if not self.config.font_file:
            detected = self._detect_font_file()
            if detected:
                self.config.font_file = detected
                logger.info(f"VideoComposer font selected: {detected}")

        # presentation 스타일은 기본적으로 줌 효과를 끄는 쪽이 더 "PPT 느낌"
        # (환경변수로 명시하면 그 값을 우선)
        if self.config.visual_style == "presentation" and os.getenv("VIDEO_KENBURNS_ZOOM") is None:
            self.config.kenburns_zoom = 1.0

        # FFmpeg 사용 가능 여부 확인
        self._ffmpeg_available = self._check_ffmpeg()

    def _detect_font_file(self) -> Optional[str]:
        """OS별로 한글 지원 폰트를 자동 선택합니다."""
        candidates: list[str] = []
        system = platform.system().lower()
        if system == "darwin":
            candidates.extend(
                [
                    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
                    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
                    "/Library/Fonts/AppleGothic.ttf",
                ]
            )
        else:
            candidates.extend(
                [
                    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                    "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf",
                    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                    "/usr/share/fonts/truetype/noto/NotoSansCJKkr-Regular.otf",
                ]
            )

        for p in candidates:
            if Path(p).exists():
                return p
        return None

    def _escape_drawtext_value(self, v: Optional[str]) -> str:
        """FFmpeg drawtext 옵션 값(특히 fontfile 경로)을 안전하게 이스케이프합니다."""
        if not v:
            return ""
        # drawtext는 ':'로 옵션을 구분하므로 경로의 ':'는 \\: 로 이스케이프 필요
        return str(v).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    def _wrap_text_for_drawtext(self, text: str) -> str:
        """긴 텍스트를 화면에 잘리지 않도록 줄바꿈 처리합니다.

        - VIDEO_TEXT_MAX_CHARS_PER_LINE: 한 줄 최대 글자 수 (기본: 32)
        - VIDEO_TEXT_MAX_LINES: 최대 줄 수 (기본: 3) 초과 시 말줄임(...)

        drawtext에서 개행은 '\\n' 으로 표현합니다.
        """
        raw = (text or "").strip()
        if not raw:
            return ""

        try:
            max_chars = int(os.getenv("VIDEO_TEXT_MAX_CHARS_PER_LINE", "32"))
        except Exception:
            max_chars = 32
        try:
            max_lines = int(os.getenv("VIDEO_TEXT_MAX_LINES", "3"))
        except Exception:
            max_lines = 3

        # 너무 작은 값 방어
        max_chars = max(10, min(max_chars, 200))
        max_lines = max(1, min(max_lines, 10))

        # 공백 기준 우선 랩핑, 공백이 거의 없으면 글자 단위 랩핑
        words = raw.split()
        lines: List[str] = []
        if len(words) > 1:
            cur = ""
            for w in words:
                cand = (cur + " " + w).strip() if cur else w
                if len(cand) <= max_chars:
                    cur = cand
                else:
                    if cur:
                        lines.append(cur)
                    # 단어 자체가 너무 길면 글자 단위로 쪼갬
                    while len(w) > max_chars:
                        lines.append(w[:max_chars])
                        w = w[max_chars:]
                    cur = w
            if cur:
                lines.append(cur)
        else:
            # 글자 단위 랩핑
            for i in range(0, len(raw), max_chars):
                lines.append(raw[i : i + max_chars])

        if len(lines) > max_lines:
            lines = lines[:max_lines]
            # 마지막 줄 말줄임
            if len(lines[-1]) >= 3:
                lines[-1] = lines[-1][:-3] + "..."
            else:
                lines[-1] = lines[-1] + "..."

        # drawtext용 개행
        return "\\n".join(lines)

    def _ensure_presentation_slides(
        self,
        scenes: List[SceneInfo],
        output_path: Path,
        job_id: str,
    ) -> None:
        """presentation 스타일용 슬라이드 이미지 생성.

        씬별로 `scene.image_path`를 채워서 animated 합성 파이프라인을 사용할 수 있게 합니다.
        """
        if Image is None or ImageDraw is None or ImageFont is None:
            raise RuntimeError("Pillow가 필요합니다. (pip install pillow)")

        width = self.config.video_width
        height = self.config.video_height

        # 폰트 로드
        # 기본값을 조금 줄여(타이틀/본문) 화면 잘림/가독성 이슈를 완화
        title_size = int(os.getenv("PRESENTATION_TITLE_FONT_SIZE", "34"))
        body_size = int(os.getenv("PRESENTATION_BODY_FONT_SIZE", "28"))
        line_spacing = int(os.getenv("PRESENTATION_LINE_SPACING", "14"))
        max_lines = int(os.getenv("PRESENTATION_MAX_LINES", "10"))
        max_chars = int(os.getenv("PRESENTATION_MAX_CHARS_PER_LINE", "42"))

        font_path = self.config.font_file
        def _load_font(size: int):
            try:
                return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
            except Exception:
                return ImageFont.load_default()

        title_font = _load_font(title_size)
        body_font = _load_font(body_size)

        def _wrap(text: str, max_chars_: int, max_lines_: int) -> List[str]:
            raw = (text or "").strip()
            if not raw:
                return []
            words = raw.split()
            out: List[str] = []
            if len(words) > 1:
                cur = ""
                for w in words:
                    cand = (cur + " " + w).strip() if cur else w
                    if len(cand) <= max_chars_:
                        cur = cand
                    else:
                        if cur:
                            out.append(cur)
                        cur = w
                if cur:
                    out.append(cur)
            else:
                for i in range(0, len(raw), max_chars_):
                    out.append(raw[i : i + max_chars_])
            if len(out) > max_lines_:
                out = out[:max_lines_]
                out[-1] = (out[-1][: max(0, len(out[-1]) - 3)] + "...") if out[-1] else "..."
            return out

        # 팔레트 (PPT 느낌)
        # 배경색은 설정/HeyGen PIP와 일치시키기 위해 HEX로도 제어 가능
        bg_hex = (os.getenv("PRESENTATION_BG_HEX") or "#101216").strip()
        try:
            bg = tuple(int(bg_hex.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
        except Exception:
            bg = (16, 18, 22)
        card = (25, 28, 34)
        accent = (79, 141, 255)
        text_primary = (245, 246, 248)
        text_secondary = (205, 210, 220)

        margin_x = int(width * 0.08)
        header_top = int(height * 0.06)
        header_h = int(height * 0.16)
        body_top = int(height * 0.28)
        body_bottom = int(height * 0.86)

        for idx, scene in enumerate(scenes):
            # ⚠️ presentation 슬라이드 생성 시 scene.image_path는 최종적으로 "슬라이드 파일 경로"로 덮어쓴다.
            # 그 전에 들어있던 값은 "우측 이미지 패널(실제 이미지)" 용도로만 사용해야 한다.
            # (animated/presentation 공용 파이프라인에서 scene_images를 미리 채우면,
            #  텍스트가 포함된 이미지가 패널로 들어가 '슬라이드가 2번 겹치는' 현상이 발생할 수 있음)
            panel_src = (scene.image_path or "").strip()

            def _looks_like_generated(path_str: str) -> bool:
                s = (path_str or "").lower()
                name = Path(path_str).name.lower() if path_str else ""
                return (
                    "/scene_images/" in s
                    or name.startswith("scene_")  # scene_000.png 류
                    or "_slide_" in name          # job_slide_01.png 류
                )

            panel_path = None
            if panel_src and Path(panel_src).exists() and not _looks_like_generated(panel_src):
                panel_path = panel_src

            # 타이틀/본문 텍스트
            title = (scene.caption or scene.on_screen_text or "").strip()
            if not title:
                title = (scene.narration or "").strip()
                title = title.split(".")[0][:80] if title else f"Slide {idx+1}"

            body = (scene.on_screen_text or scene.narration or scene.caption or "").strip()
            body_lines = _wrap(body, max_chars, max_lines)

            img = Image.new("RGB", (width, height), bg)
            draw = ImageDraw.Draw(img)

            # 헤더 카드
            draw.rounded_rectangle(
                [margin_x, header_top, width - margin_x, header_top + header_h],
                radius=28,
                fill=card,
            )
            # 액센트 바
            draw.rounded_rectangle(
                [margin_x, header_top, margin_x + 18, header_top + header_h],
                radius=10,
                fill=accent,
            )
            # 타이틀은 "헤더 박스 안에서 세로 중앙 정렬" + "너무 길면 자동으로 폰트 축소"로 잘림 방지
            title_x = margin_x + 38
            max_title_w = (width - margin_x) - title_x - 24  # 우측 여백

            cur_title_size = title_size
            cur_title_font = title_font
            try:
                # 긴 타이틀이면 폰트 축소 (최소 28)
                while cur_title_size > 28:
                    bbox = draw.textbbox((0, 0), title, font=cur_title_font)
                    tw = bbox[2] - bbox[0]
                    if tw <= max_title_w:
                        break
                    cur_title_size -= 2
                    cur_title_font = _load_font(cur_title_size)
            except Exception:
                pass

            try:
                bbox = draw.textbbox((0, 0), title, font=cur_title_font)
                th = bbox[3] - bbox[1]
            except Exception:
                th = cur_title_size
            title_y = int(header_top + (header_h - th) / 2)
            # 약간의 상단 안전 여백
            title_y = max(header_top + 14, title_y)

            draw.text((title_x, title_y), title, font=cur_title_font, fill=text_primary)

            # 본문 카드
            draw.rounded_rectangle(
                [margin_x, body_top, width - margin_x, body_bottom],
                radius=28,
                fill=card,
            )

            # 레이아웃: 좌측 텍스트 / 우측 이미지 패널(가능하면)
            inner_pad = 48
            panel_gap = 36
            content_left = margin_x + inner_pad
            content_top = body_top + 44
            content_right = width - margin_x - inner_pad
            content_bottom = body_bottom - 44

            # 이미지 패널 폭(오른쪽 38%) - 없으면 텍스트가 전체 폭 사용
            has_image = bool(panel_path)
            img_panel_w = int((content_right - content_left) * 0.38)
            text_area_right = (
                content_right - img_panel_w - panel_gap if has_image else content_right
            )

            # 텍스트(불릿)
            x = content_left
            y = content_top
            bullet = "• "
            for line in body_lines:
                draw.text((x, y), bullet + line, font=body_font, fill=text_secondary)
                y += body_size + line_spacing

            # 이미지 패널
            if has_image:
                panel_left = text_area_right + panel_gap
                panel_top = content_top
                panel_right = content_right
                panel_bottom = content_bottom

                # 패널 배경
                draw.rounded_rectangle(
                    [panel_left, panel_top, panel_right, panel_bottom],
                    radius=24,
                    fill=(20, 22, 27),
                )

                try:
                    src = Image.open(panel_path).convert("RGB")
                    pw = panel_right - panel_left
                    ph = panel_bottom - panel_top

                    # cover crop
                    sw, sh = src.size
                    scale = max(pw / sw, ph / sh)
                    nw, nh = int(sw * scale), int(sh * scale)
                    src = src.resize((nw, nh))
                    left = (nw - pw) // 2
                    top = (nh - ph) // 2
                    src = src.crop((left, top, left + pw, top + ph))

                    # rounded corners mask
                    mask = Image.new("L", (pw, ph), 0)
                    mdraw = ImageDraw.Draw(mask)
                    mdraw.rounded_rectangle([0, 0, pw, ph], radius=24, fill=255)

                    img.paste(src, (panel_left, panel_top), mask)
                except Exception:
                    # 이미지 로드 실패 시 그냥 패널만
                    pass

            # 페이지 표시
            page_text = f"{idx+1}/{len(scenes)}"
            draw.text((width - margin_x - 120, body_bottom + 18), page_text, font=body_font, fill=text_secondary)

            slide_path = output_path / f"{job_id}_slide_{idx+1:02d}.png"
            img.save(slide_path, "PNG")
            scene.image_path = str(slide_path)

    def _check_ffmpeg(self) -> bool:
        """FFmpeg 설치 여부 확인."""
        try:
            result = subprocess.run(
                [self._ffmpeg, "-version"],
                capture_output=True,
                timeout=5,
            )
            available = result.returncode == 0
            if available:
                logger.info("FFmpeg is available")
            else:
                logger.warning("FFmpeg not available or not working")
            return available
        except Exception as e:
            logger.warning(f"FFmpeg check failed: {e}")
            return False

    @property
    def is_available(self) -> bool:
        """FFmpeg 사용 가능 여부."""
        return self._ffmpeg_available

    async def compose(
        self,
        scenes: List[SceneInfo],
        audio_path: str,
        output_dir: Union[str, Path],
        job_id: Optional[str] = None,
    ) -> ComposedVideo:
        """씬 정보와 오디오로 비디오를 합성합니다.

        Args:
            scenes: 씬 정보 목록
            audio_path: 전체 오디오 파일 경로
            output_dir: 출력 디렉토리
            job_id: 잡 ID (파일명에 사용)

        Returns:
            ComposedVideo: 합성 결과
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        job_id = job_id or "video"

        # 1. 오디오 duration 조회
        duration_sec = await self.get_audio_duration(audio_path)
        logger.info(f"Audio duration: {duration_sec:.2f}s")

        # 2. 씬별 duration 계산
        scenes = self._calculate_scene_durations(scenes, duration_sec)

        # 3. SRT 자막 생성
        subtitle_path = output_path / f"{job_id}.srt"
        if self._skip_subtitles:
            logger.info("Subtitle generation skipped (VIDEO_SKIP_SUBTITLES)")
        else:
            self._generate_srt(scenes, subtitle_path)
            logger.info(f"Subtitle generated: {subtitle_path}")

        # 4. 비디오 합성
        video_path = output_path / f"{job_id}.mp4"

        if self._ffmpeg_available:
            # Phase 37: visual_style에 따라 합성 방식 선택
            if self.config.visual_style == "presentation":
                # 씬 텍스트 기반 슬라이드 이미지를 만든 후 animated 파이프라인(페이드 전환)을 재사용
                if Image is None:
                    logger.warning(
                        "Pillow not available. Falling back to basic FFmpeg drawtext."
                    )
                    await self._compose_with_ffmpeg(scenes, audio_path, video_path, duration_sec)
                else:
                    self._ensure_presentation_slides(scenes, output_path, job_id)
                    await self._compose_animated(scenes, audio_path, video_path, duration_sec)
            elif self.config.visual_style == "animated" and self._has_scene_images(scenes):
                await self._compose_animated(scenes, audio_path, video_path, duration_sec)
            else:
                await self._compose_with_ffmpeg(scenes, audio_path, video_path, duration_sec)
        else:
            # Mock 모드: 빈 파일 생성
            await self._compose_mock(video_path, duration_sec)

        # 5. 썸네일 생성
        thumbnail_path = output_path / f"{job_id}_thumb.jpg"
        if self._skip_thumbnail:
            logger.info("Thumbnail generation skipped (VIDEO_SKIP_THUMBNAIL)")
        else:
            await self._generate_thumbnail(video_path, thumbnail_path)

        logger.info(f"Video composed: {video_path}")

        return ComposedVideo(
            video_path=str(video_path),
            subtitle_path=str(subtitle_path) if not self._skip_subtitles else "",
            thumbnail_path=str(thumbnail_path) if not self._skip_thumbnail else "",
            duration_sec=duration_sec,
            scenes=scenes,
        )

    def _has_scene_images(self, scenes: List[SceneInfo]) -> bool:
        """모든 씬에 이미지가 있는지 확인."""
        for scene in scenes:
            if not scene.image_path or not Path(scene.image_path).exists():
                return False
        return True

    async def get_audio_duration(self, audio_path: str) -> float:
        """오디오 파일의 길이를 반환합니다."""
        if not self._ffmpeg_available:
            # Mock: 파일 크기로 대략 추정
            try:
                size = Path(audio_path).stat().st_size
                # 대략 128kbps MP3 기준
                return max(size / 16000, 5.0)
            except Exception:
                return 10.0

        try:
            loop = asyncio.get_event_loop()

            def _probe():
                result = subprocess.run(
                    [
                        self._ffprobe,
                        "-v", "quiet",
                        "-show_entries", "format=duration",
                        "-of", "json",
                        audio_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    return float(data["format"]["duration"])
                return 10.0

            return await loop.run_in_executor(None, _probe)

        except Exception as e:
            logger.warning(f"Failed to get audio duration: {e}")
            return 10.0

    def _calculate_scene_durations(
        self,
        scenes: List[SceneInfo],
        total_duration: float,
    ) -> List[SceneInfo]:
        """씬별 duration 계산."""
        if not scenes:
            return scenes

        # 전체 길이 상한 적용 (초과 시 비율 스케일링으로 맞춤)
        effective_total = total_duration
        if self._max_video_duration_sec and self._max_video_duration_sec > 0:
            if effective_total > self._max_video_duration_sec:
                logger.info(
                    f"Max video duration enforced: audio/target={effective_total:.2f}s -> "
                    f"cap={self._max_video_duration_sec:.2f}s"
                )
                effective_total = self._max_video_duration_sec

        # duration이 이미 설정된 씬 확인
        fixed_duration = sum(s.duration_sec or 0 for s in scenes)
        unfixed_scenes = [s for s in scenes if s.duration_sec is None]

        if unfixed_scenes:
            remaining = max(effective_total - fixed_duration, 1.0)
            per_scene = remaining / len(unfixed_scenes)

            for scene in unfixed_scenes:
                scene.duration_sec = per_scene

        # ✅ 중요한 보정:
        # render-spec의 duration_sec 합이 실제 오디오 길이(total_duration)와 크게 다르면
        # SRT/화면 텍스트 타이밍이 영상 끝을 넘어가서 "첫 문장만 보임" 문제가 발생합니다.
        # 이런 경우 duration을 오디오 길이에 맞게 비례 스케일링합니다.
        sum_duration = sum(s.duration_sec or 0 for s in scenes)
        if sum_duration > 0 and effective_total > 0:
            # 10% 이상 차이나면 보정
            if abs(sum_duration - effective_total) / max(effective_total, 1e-6) >= 0.10:
                scale = effective_total / sum_duration
                # 너무 과격한 스케일은 로그로 남김
                logger.warning(
                    f"Scene durations mismatch: sum={sum_duration:.2f}s, audio={effective_total:.2f}s. "
                    f"Scaling durations by {scale:.3f}."
                )
                for s in scenes:
                    if s.duration_sec is not None:
                        # 최소 0.5초는 보장 (너무 짧아져 깜빡이는 것 방지)
                        s.duration_sec = max(s.duration_sec * scale, 0.5)

        return scenes

    def _generate_srt(self, scenes: List[SceneInfo], output_path: Path) -> None:
        """SRT 자막 파일 생성."""
        srt_lines = []
        current_time = 0.0

        for i, scene in enumerate(scenes):
            start_time = current_time
            end_time = current_time + (scene.duration_sec or 5.0)

            text = scene.on_screen_text or scene.caption or scene.narration
            if text:
                srt_lines.append(str(i + 1))
                srt_lines.append(f"{self._format_srt_time(start_time)} --> {self._format_srt_time(end_time)}")
                srt_lines.append(text)
                srt_lines.append("")

            current_time = end_time

        output_path.write_text("\n".join(srt_lines), encoding="utf-8")

    def _format_srt_time(self, seconds: float) -> str:
        """초를 SRT 시간 포맷으로 변환."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    async def _compose_with_ffmpeg(
        self,
        scenes: List[SceneInfo],
        audio_path: str,
        output_path: Path,
        duration: float,
    ) -> None:
        """FFmpeg로 비디오 합성."""
        loop = asyncio.get_event_loop()

        def _compose():
            # 1. 텍스트 오버레이가 있는 배경 비디오 생성
            # 단순화: 단색 배경 + 텍스트 오버레이
            #
            # 복잡한 씬별 텍스트 대신 단색 배경으로 시작
            # 실제 프로덕션에서는 씬별 이미지 합성 필요

            # 텍스트 필터 생성 (씬별 타이밍)
            drawtext_filters = []
            current_time = 0.0

            for scene in scenes:
                # 화면 표시 텍스트 우선순위:
                # on_screen_text > caption > narration(없을 때만)
                text = scene.on_screen_text or scene.caption or scene.narration or ""
                if text:
                    text = self._wrap_text_for_drawtext(text)
                    # 특수문자 이스케이프
                    safe_text = text.replace("'", "\\'").replace(":", "\\:")

                    # 씬별 텍스트 표시
                    start = current_time
                    end = current_time + (scene.duration_sec or 5.0)

                    # 폰트 지정(한글 깨짐 방지)
                    font_part = ""
                    if self.config.font_file:
                        safe_font = self._escape_drawtext_value(self.config.font_file)
                        font_part = f"fontfile='{safe_font}':"

                    filter_str = (
                        f"drawtext=text='{safe_text}':"
                        f"{font_part}"
                        f"fontsize={self.config.font_size}:"
                        f"fontcolor={self.config.text_color}:"
                        f"line_spacing=10:"
                        f"box=1:boxcolor=black@0.35:boxborderw=20:"
                        f"x=(w-text_w)/2:y=(h-text_h)/2:"
                        f"enable='between(t,{start:.2f},{end:.2f})'"
                    )
                    drawtext_filters.append(filter_str)

                current_time += scene.duration_sec or 5.0

            # 필터 체인 구성
            if drawtext_filters:
                filter_complex = ",".join(drawtext_filters)
            else:
                filter_complex = None

            # FFmpeg 명령 구성
            cmd = [
                self._ffmpeg,
                "-y",  # 덮어쓰기
                "-f", "lavfi",
                "-i", f"color=c={self.config.background_color}:s={self.config.video_width}x{self.config.video_height}:d={duration:.2f}:r={self.config.fps}",
                "-i", audio_path,
            ]

            if filter_complex:
                cmd.extend(["-vf", filter_complex])

            cmd.extend([
                "-c:v", self.config.video_codec,
                "-preset", self.config.preset,
                "-b:v", self.config.video_bitrate,
                "-c:a", self.config.audio_codec,
                "-b:a", self.config.audio_bitrate,
                "-shortest",
                "-movflags", "+faststart",
                str(output_path),
            ])

            logger.debug(f"FFmpeg command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5분 타임아웃
            )

            if result.returncode != 0:
                logger.error(f"FFmpeg error: {result.stderr}")
                raise RuntimeError(f"FFmpeg failed: {result.stderr[:500]}")

        await loop.run_in_executor(None, _compose)

    async def _compose_mock(self, output_path: Path, duration: float) -> None:
        """Mock 비디오 생성 (FFmpeg 없을 때)."""
        # 빈 파일 생성
        output_path.write_bytes(b"\x00" * 1024)
        logger.warning(f"Mock video created (FFmpeg not available): {output_path}")

    async def _compose_animated(
        self,
        scenes: List[SceneInfo],
        audio_path: str,
        output_path: Path,
        duration: float,
    ) -> None:
        """Phase 37: Animated 모드 영상 합성 (Ken Burns + fade).

        각 씬 이미지에 zoompan(Ken Burns) 효과와 xfade(fade) 전환을 적용합니다.

        Args:
            scenes: 씬 정보 목록 (각 씬에 image_path 필요)
            audio_path: 오디오 파일 경로
            output_path: 출력 비디오 경로
            duration: 전체 비디오 길이
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._compose_animated_sync, scenes, audio_path, output_path, duration)

    def _compose_animated_sync(
        self,
        scenes: List[SceneInfo],
        audio_path: str,
        output_path: Path,
        duration: float,
    ) -> None:
        """Animated 합성 동기 버전."""
        # FFmpeg 명령 빌드
        cmd = self._build_animated_ffmpeg_command(scenes, audio_path, output_path, duration)

        logger.debug(f"FFmpeg animated command: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10분 타임아웃
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg animated error: {result.stderr}")
            raise RuntimeError(f"FFmpeg animated failed: {result.stderr[:500]}")

        logger.info(f"Animated video composed: {output_path}")

    def _build_animated_ffmpeg_command(
        self,
        scenes: List[SceneInfo],
        audio_path: str,
        output_path: Path,
        duration: float,
    ) -> List[str]:
        """Animated 모드 FFmpeg 명령 빌드.

        Ken Burns(zoompan) + fade 전환 필터를 생성합니다.

        Returns:
            List[str]: FFmpeg 명령 인자 목록
        """
        config = self.config
        fps = config.fps
        zoom = config.kenburns_zoom
        fade_dur = config.fade_duration
        width = config.video_width
        height = config.video_height

        # 입력 파일 목록
        cmd = [self._ffmpeg, "-y"]

        # 씬 이미지 입력
        for scene in scenes:
            cmd.extend(["-loop", "1", "-t", str(scene.duration_sec or 5.0), "-i", scene.image_path])

        # 오디오 입력
        cmd.extend(["-i", audio_path])

        # 필터 복합 구성
        filter_parts = []
        n_scenes = len(scenes)

        for i, scene in enumerate(scenes):
            scene_dur = scene.duration_sec or 5.0
            total_frames = int(scene_dur * fps)

            # presentation 스타일에서는 zoom=1.0으로 두는 경우가 많고,
            # 이때도 scale(2x)+zoompan을 태우면 매우 무거워집니다.
            # zoom이 사실상 비활성(≈1.0)이면 zoompan을 완전히 생략하고
            # scale+fps만 적용해 합성을 가볍게 만듭니다.
            if abs(float(zoom) - 1.0) < 1e-6:
                filter_parts.append(
                    f"[{i}:v]scale={width}:{height},fps={fps},setsar=1,format=yuv420p[v{i}]"
                )
            else:
                # Ken Burns (zoompan) 필터
                # 줌 인: 시작 1.0 → 끝 zoom
                # 랜덤하게 줌 방향 결정 (짝수 씬: 줌인, 홀수 씬: 줌아웃)
                if i % 2 == 0:
                    # Zoom in: 1.0 → zoom
                    zoom_expr = f"'min(zoom+{(zoom-1)/total_frames:.6f},pzoom*{zoom})'"
                else:
                    # Zoom out: zoom → 1.0
                    zoom_expr = f"'if(eq(on,1),{zoom},max(zoom-{(zoom-1)/total_frames:.6f},1))'"

                # 줌 중심점 약간 이동 (동적인 느낌)
                x_expr = "'iw/2-(iw/zoom/2)'"
                y_expr = "'ih/2-(ih/zoom/2)'"

                # zoompan 필터 (고해상도에서 줌 후 다운스케일)
                zoompan_filter = (
                    f"[{i}:v]scale={width*2}:{height*2},"
                    f"zoompan=z={zoom_expr}:x={x_expr}:y={y_expr}:"
                    f"d={total_frames}:s={width}x{height}:fps={fps}"
                    f"[v{i}]"
                )
                filter_parts.append(zoompan_filter)

        # Fade 전환 (xfade) 적용
        if n_scenes == 1:
            # 씬이 1개면 fade 없이 그대로
            filter_parts.append(f"[v0]format=yuv420p[vout]")
        else:
            # 씬들을 xfade로 연결
            current_offset = 0.0
            prev_label = "v0"

            for i in range(1, n_scenes):
                # 이전 씬의 끝에서 fade_dur 전에 전환 시작
                prev_dur = scenes[i - 1].duration_sec or 5.0
                current_offset += prev_dur - fade_dur

                xfade_label = f"xf{i}" if i < n_scenes - 1 else "vout_pre"
                xfade_filter = (
                    f"[{prev_label}][v{i}]xfade=transition=fade:"
                    f"duration={fade_dur}:offset={current_offset:.2f}[{xfade_label}]"
                )
                filter_parts.append(xfade_filter)
                prev_label = xfade_label

            # 최종 출력 포맷
            filter_parts.append(f"[{prev_label}]format=yuv420p[vout]")

        # 필터 체인
        filter_complex = ";".join(filter_parts)

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", f"{n_scenes}:a",  # 오디오 스트림
            "-c:v", config.video_codec,
            "-preset", config.preset,
            "-b:v", config.video_bitrate,
            "-c:a", config.audio_codec,
            "-b:a", config.audio_bitrate,
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        ])

        return cmd

    def get_animated_ffmpeg_command_preview(
        self,
        scenes: List[SceneInfo],
        audio_path: str,
        output_path: str,
    ) -> List[str]:
        """테스트용: Animated FFmpeg 명령 미리보기.

        실제 실행하지 않고 명령만 반환합니다.
        """
        return self._build_animated_ffmpeg_command(
            scenes, audio_path, Path(output_path), 0.0
        )

    async def _generate_thumbnail(
        self,
        video_path: Path,
        output_path: Path,
        time_offset: float = 1.0,
    ) -> None:
        """비디오에서 썸네일 추출."""
        if not self._ffmpeg_available:
            # Mock: 빈 파일
            output_path.write_bytes(b"\x00" * 100)
            return

        loop = asyncio.get_event_loop()

        def _extract():
            cmd = [
                self._ffmpeg,
                "-y",
                "-ss", str(time_offset),
                "-i", str(video_path),
                "-vframes", "1",
                "-q:v", "2",
                str(output_path),
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
            )

            if result.returncode != 0:
                # 썸네일 실패해도 계속 진행
                logger.warning(f"Thumbnail extraction failed: {result.stderr}")
                output_path.write_bytes(b"\x00" * 100)

        await loop.run_in_executor(None, _extract)

    async def compose_from_script(
        self,
        script_json: Dict[str, Any],
        audio_path: str,
        output_dir: Union[str, Path],
        job_id: Optional[str] = None,
    ) -> ComposedVideo:
        """스크립트 JSON에서 씬 정보 추출 후 비디오 합성.

        Args:
            script_json: VideoScript JSON (chapters/scenes 구조)
            audio_path: 오디오 파일 경로
            output_dir: 출력 디렉토리
            job_id: 잡 ID

        Returns:
            ComposedVideo: 합성 결과
        """
        scenes = self._extract_scenes_from_script(script_json)
        return await self.compose(scenes, audio_path, output_dir, job_id)

    def _extract_scenes_from_script(self, script: Dict[str, Any]) -> List[SceneInfo]:
        """스크립트 JSON에서 SceneInfo 목록 추출."""
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

        return scenes


# =============================================================================
# Singleton Instance
# =============================================================================


_composer: Optional[VideoComposer] = None


def get_video_composer() -> VideoComposer:
    """VideoComposer 싱글톤 인스턴스 반환."""
    global _composer
    if _composer is None:
        _composer = VideoComposer()
    return _composer


def clear_video_composer() -> None:
    """VideoComposer 싱글톤 초기화 (테스트용)."""
    global _composer
    _composer = None
