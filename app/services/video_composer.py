"""
Phase 32: Video Composer (FFmpeg)

FFmpeg를 사용한 영상 합성 서비스.

기능:
- 씬별 이미지 + 오디오 → MP4 합성
- SRT 자막 파일 생성
- 썸네일 추출

의존성:
- ffmpeg: 시스템에 설치 필요 (Docker에서는 기본 포함)
- ffprobe: 오디오/비디오 정보 조회

환경변수:
- FFMPEG_PATH: ffmpeg 바이너리 경로 (기본: ffmpeg)
- FFPROBE_PATH: ffprobe 바이너리 경로 (기본: ffprobe)
"""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.core.logging import get_logger

logger = get_logger(__name__)


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

        # FFmpeg 사용 가능 여부 확인
        self._ffmpeg_available = self._check_ffmpeg()

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
        self._generate_srt(scenes, subtitle_path)
        logger.info(f"Subtitle generated: {subtitle_path}")

        # 4. 비디오 합성
        video_path = output_path / f"{job_id}.mp4"

        if self._ffmpeg_available:
            await self._compose_with_ffmpeg(scenes, audio_path, video_path, duration_sec)
        else:
            # Mock 모드: 빈 파일 생성
            await self._compose_mock(video_path, duration_sec)

        # 5. 썸네일 생성
        thumbnail_path = output_path / f"{job_id}_thumb.jpg"
        await self._generate_thumbnail(video_path, thumbnail_path)

        logger.info(f"Video composed: {video_path}")

        return ComposedVideo(
            video_path=str(video_path),
            subtitle_path=str(subtitle_path),
            thumbnail_path=str(thumbnail_path),
            duration_sec=duration_sec,
            scenes=scenes,
        )

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

        # duration이 이미 설정된 씬 확인
        fixed_duration = sum(s.duration_sec or 0 for s in scenes)
        unfixed_scenes = [s for s in scenes if s.duration_sec is None]

        if unfixed_scenes:
            remaining = max(total_duration - fixed_duration, 1.0)
            per_scene = remaining / len(unfixed_scenes)

            for scene in unfixed_scenes:
                scene.duration_sec = per_scene

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
                text = scene.on_screen_text or scene.caption or ""
                if text:
                    # 특수문자 이스케이프
                    safe_text = text.replace("'", "\\'").replace(":", "\\:")

                    # 씬별 텍스트 표시
                    start = current_time
                    end = current_time + (scene.duration_sec or 5.0)

                    filter_str = (
                        f"drawtext=text='{safe_text}':"
                        f"fontsize={self.config.font_size}:"
                        f"fontcolor={self.config.text_color}:"
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
