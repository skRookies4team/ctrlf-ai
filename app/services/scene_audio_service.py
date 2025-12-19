"""
Phase 40: Scene Audio Service

씬(Scene) 단위 오디오 생성 및 캡션 타임라인 관리 서비스.

주요 기능:
1. 문장별 TTS 생성 + Concatenation
2. 오디오 길이 기반 씬 duration 확정
3. 캡션 타임라인(JSON/SRT) 생성

Usage:
    service = SceneAudioService()

    # 씬 오디오 생성
    result = await service.generate_scene_audio(
        scene_id="scene-001",
        narration="첫 번째 문장입니다. 두 번째 문장이에요.",
        output_dir=Path("./output"),
    )

    print(result.audio_path)       # ./output/scene-001.mp3
    print(result.duration_sec)     # 5.2
    print(result.captions)         # [{"start": 0.0, "end": 2.5, "text": "..."}, ...]
"""

import asyncio
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.clients.tts_provider import (
    BaseTTSProvider,
    TTSProvider,
    TTSResult,
    get_tts_provider,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.utils.text_splitter import split_sentences

logger = get_logger(__name__)


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class CaptionEntry:
    """캡션 타임라인 항목."""

    start: float  # 시작 시간 (초)
    end: float  # 종료 시간 (초)
    text: str  # 자막 텍스트

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text,
        }


@dataclass
class SentenceAudioResult:
    """문장 오디오 생성 결과."""

    sentence: str
    audio_path: Optional[str]
    duration_sec: float
    success: bool
    error: Optional[str] = None


@dataclass
class SceneAudioResult:
    """씬 오디오 생성 결과."""

    scene_id: str
    audio_path: str  # 합성된 오디오 파일 경로
    duration_sec: float  # 패딩 포함 최종 duration
    audio_duration_sec: float  # 순수 오디오 duration
    captions: List[CaptionEntry] = field(default_factory=list)
    sentence_count: int = 0
    failed_sentences: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "scene_id": self.scene_id,
            "audio_path": self.audio_path,
            "duration_sec": round(self.duration_sec, 2),
            "audio_duration_sec": round(self.audio_duration_sec, 2),
            "captions": [c.to_dict() for c in self.captions],
            "sentence_count": self.sentence_count,
            "failed_sentences": self.failed_sentences,
        }

    def get_captions_json(self) -> List[Dict[str, Any]]:
        """캡션 JSON 리스트 반환."""
        return [c.to_dict() for c in self.captions]


# =============================================================================
# Scene Audio Service
# =============================================================================


class SceneAudioService:
    """씬 오디오 서비스.

    문장별 TTS 생성, 오디오 합성, 캡션 타임라인 생성을 담당합니다.
    """

    # 무음 placeholder (TTS 실패 시 대체)
    SILENCE_DURATION_SEC = 0.5

    def __init__(
        self,
        tts_provider: Optional[BaseTTSProvider] = None,
        silence_padding_sec: Optional[float] = None,
    ):
        """서비스 초기화.

        Args:
            tts_provider: TTS Provider (없으면 환경변수 기반 생성)
            silence_padding_sec: 씬 끝 패딩 시간 (없으면 환경변수 사용)
        """
        self._tts = tts_provider or get_tts_provider()
        settings = get_settings()

        # 환경변수에서 패딩 시간 로드 (기본: 0.5초)
        self._silence_padding_sec = silence_padding_sec
        if self._silence_padding_sec is None:
            self._silence_padding_sec = getattr(
                settings, "SCENE_SILENCE_PADDING_SEC", 0.5
            )

        # FFmpeg 사용 가능 여부
        self._has_ffmpeg = self._check_ffmpeg()

    def _check_ffmpeg(self) -> bool:
        """FFmpeg 사용 가능 여부 확인."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            logger.warning("FFmpeg not available - audio concat will be limited")
            return False

    # =========================================================================
    # Main API
    # =========================================================================

    async def generate_scene_audio(
        self,
        scene_id: str,
        narration: str,
        output_dir: Path,
        scene_offset_sec: float = 0.0,
    ) -> SceneAudioResult:
        """씬 오디오를 생성합니다.

        1. narration을 문장으로 분할
        2. 각 문장에 대해 TTS 생성
        3. 생성된 오디오들을 하나로 concat
        4. 캡션 타임라인 생성
        5. duration 계산 (+ 패딩)

        Args:
            scene_id: 씬 ID
            narration: 나레이션 텍스트
            output_dir: 출력 디렉토리
            scene_offset_sec: 전체 영상에서 씬 시작 오프셋

        Returns:
            SceneAudioResult: 씬 오디오 생성 결과
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 빈 narration 처리
        if not narration or not narration.strip():
            logger.warning(f"Empty narration for scene: {scene_id}")
            return self._create_silent_result(scene_id, output_dir, scene_offset_sec)

        # Step 1: 문장 분할
        sentences = split_sentences(narration)
        if not sentences:
            return self._create_silent_result(scene_id, output_dir, scene_offset_sec)

        logger.info(f"Scene {scene_id}: {len(sentences)} sentences to process")

        # Step 2: 각 문장 TTS 생성
        sentence_results = await self._generate_sentence_audios(
            scene_id=scene_id,
            sentences=sentences,
            output_dir=output_dir,
        )

        # Step 3: 오디오 concat
        concat_path, total_audio_duration = await self._concat_audios(
            scene_id=scene_id,
            sentence_results=sentence_results,
            output_dir=output_dir,
        )

        # Step 4: 캡션 타임라인 생성
        captions = self._generate_caption_timeline(
            sentence_results=sentence_results,
            scene_offset_sec=scene_offset_sec,
        )

        # Step 5: duration 계산 (+ 패딩)
        final_duration = total_audio_duration + self._silence_padding_sec

        # 실패 문장 수 계산
        failed_count = sum(1 for r in sentence_results if not r.success)

        result = SceneAudioResult(
            scene_id=scene_id,
            audio_path=str(concat_path),
            duration_sec=final_duration,
            audio_duration_sec=total_audio_duration,
            captions=captions,
            sentence_count=len(sentences),
            failed_sentences=failed_count,
        )

        logger.info(
            f"Scene {scene_id} audio generated: "
            f"duration={final_duration:.2f}s, "
            f"captions={len(captions)}, "
            f"failed={failed_count}/{len(sentences)}"
        )

        return result

    async def generate_scene_audios(
        self,
        scenes: List[Dict[str, Any]],
        output_dir: Path,
    ) -> List[SceneAudioResult]:
        """여러 씬의 오디오를 순차적으로 생성합니다.

        전체 영상 타임라인에서 씬 오프셋을 누적하며 캡션을 생성합니다.

        Args:
            scenes: 씬 정보 리스트 [{"scene_id": "...", "narration": "..."}, ...]
            output_dir: 출력 디렉토리

        Returns:
            List[SceneAudioResult]: 씬별 오디오 결과 리스트
        """
        results = []
        current_offset = 0.0

        for scene in scenes:
            scene_id = scene.get("scene_id", f"scene-{len(results)}")
            narration = scene.get("narration", "")

            result = await self.generate_scene_audio(
                scene_id=scene_id,
                narration=narration,
                output_dir=output_dir,
                scene_offset_sec=current_offset,
            )

            results.append(result)

            # 다음 씬 오프셋 = 현재 씬 종료 시점
            current_offset += result.duration_sec

        logger.info(
            f"Generated {len(results)} scene audios, "
            f"total duration: {current_offset:.2f}s"
        )

        return results

    # =========================================================================
    # TTS Generation
    # =========================================================================

    async def _generate_sentence_audios(
        self,
        scene_id: str,
        sentences: List[str],
        output_dir: Path,
    ) -> List[SentenceAudioResult]:
        """각 문장에 대해 TTS 오디오를 생성합니다.

        실패 시 무음으로 대체합니다 (Job 전체 실패 금지).

        Args:
            scene_id: 씬 ID
            sentences: 문장 리스트
            output_dir: 출력 디렉토리

        Returns:
            List[SentenceAudioResult]: 문장별 오디오 결과
        """
        results = []

        for i, sentence in enumerate(sentences):
            audio_path = output_dir / f"{scene_id}_sent_{i:03d}.mp3"

            try:
                # TTS 생성
                tts_result = await self._tts.synthesize(
                    text=sentence,
                    language="ko",
                )

                # 파일 저장
                audio_path.write_bytes(tts_result.audio_bytes)

                # duration 계산 (실제 파일에서 읽거나 TTS 결과 사용)
                duration = await self._get_audio_duration(str(audio_path))
                if duration <= 0:
                    duration = tts_result.duration_sec

                results.append(
                    SentenceAudioResult(
                        sentence=sentence,
                        audio_path=str(audio_path),
                        duration_sec=duration,
                        success=True,
                    )
                )

                logger.debug(
                    f"TTS generated: scene={scene_id}, sent={i}, "
                    f"duration={duration:.2f}s"
                )

            except Exception as e:
                # 실패 시 무음으로 대체
                logger.warning(
                    f"TTS failed for sentence {i}: {e}, using silence"
                )

                # 무음 파일 생성
                silence_path = await self._create_silence_audio(
                    output_path=audio_path,
                    duration_sec=self.SILENCE_DURATION_SEC,
                )

                results.append(
                    SentenceAudioResult(
                        sentence=sentence,
                        audio_path=str(silence_path),
                        duration_sec=self.SILENCE_DURATION_SEC,
                        success=False,
                        error=str(e),
                    )
                )

        return results

    # =========================================================================
    # Audio Concatenation
    # =========================================================================

    async def _concat_audios(
        self,
        scene_id: str,
        sentence_results: List[SentenceAudioResult],
        output_dir: Path,
    ) -> tuple:
        """문장 오디오들을 하나로 합칩니다.

        Args:
            scene_id: 씬 ID
            sentence_results: 문장별 오디오 결과
            output_dir: 출력 디렉토리

        Returns:
            (concat_path, total_duration): 합성 파일 경로와 총 duration
        """
        concat_path = output_dir / f"{scene_id}_audio.mp3"

        # 유효한 오디오 파일만 필터링
        audio_paths = [
            r.audio_path
            for r in sentence_results
            if r.audio_path and Path(r.audio_path).exists()
        ]

        if not audio_paths:
            # 오디오 없으면 무음 생성
            await self._create_silence_audio(concat_path, self.SILENCE_DURATION_SEC)
            return concat_path, self.SILENCE_DURATION_SEC

        if len(audio_paths) == 1:
            # 단일 파일이면 복사
            import shutil

            shutil.copy(audio_paths[0], concat_path)
            duration = sum(r.duration_sec for r in sentence_results)
            return concat_path, duration

        # FFmpeg로 concat
        if self._has_ffmpeg:
            success = await self._ffmpeg_concat(audio_paths, concat_path)
            if success:
                duration = await self._get_audio_duration(str(concat_path))
                if duration <= 0:
                    duration = sum(r.duration_sec for r in sentence_results)
                return concat_path, duration

        # FFmpeg 실패 시: 첫 번째 파일만 사용
        logger.warning("FFmpeg concat failed, using first audio only")
        import shutil

        shutil.copy(audio_paths[0], concat_path)
        duration = sum(r.duration_sec for r in sentence_results)
        return concat_path, duration

    async def _ffmpeg_concat(
        self,
        audio_paths: List[str],
        output_path: Path,
    ) -> bool:
        """FFmpeg를 사용해 오디오 파일들을 합칩니다.

        Args:
            audio_paths: 입력 오디오 파일 경로들
            output_path: 출력 파일 경로

        Returns:
            bool: 성공 여부
        """
        try:
            # concat list 파일 생성
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as f:
                for path in audio_paths:
                    # 경로 이스케이프
                    escaped_path = str(path).replace("'", "'\\''")
                    f.write(f"file '{escaped_path}'\n")
                list_file = f.name

            # FFmpeg concat 실행
            cmd = [
                "ffmpeg",
                "-y",  # 덮어쓰기
                "-f", "concat",
                "-safe", "0",
                "-i", list_file,
                "-c", "copy",  # 재인코딩 없이 복사
                str(output_path),
            ]

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=60,
                ),
            )

            # 임시 파일 삭제
            os.unlink(list_file)

            if result.returncode != 0:
                logger.error(f"FFmpeg concat failed: {result.stderr.decode()}")
                return False

            return True

        except Exception as e:
            logger.error(f"FFmpeg concat error: {e}")
            return False

    # =========================================================================
    # Caption Timeline
    # =========================================================================

    def _generate_caption_timeline(
        self,
        sentence_results: List[SentenceAudioResult],
        scene_offset_sec: float = 0.0,
    ) -> List[CaptionEntry]:
        """문장별 오디오 duration을 기반으로 캡션 타임라인을 생성합니다.

        Args:
            sentence_results: 문장별 오디오 결과
            scene_offset_sec: 씬 시작 오프셋 (전체 영상 기준)

        Returns:
            List[CaptionEntry]: 캡션 타임라인
        """
        captions = []
        current_time = scene_offset_sec

        for result in sentence_results:
            start = current_time
            end = current_time + result.duration_sec

            captions.append(
                CaptionEntry(
                    start=start,
                    end=end,
                    text=result.sentence,
                )
            )

            current_time = end

        return captions

    # =========================================================================
    # Helper Methods
    # =========================================================================

    async def _get_audio_duration(self, audio_path: str) -> float:
        """오디오 파일의 재생 시간을 반환합니다.

        Args:
            audio_path: 오디오 파일 경로

        Returns:
            float: 재생 시간 (초), 실패 시 0
        """
        if not self._has_ffmpeg:
            return 0.0

        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ]

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=10,
                ),
            )

            if result.returncode == 0:
                duration_str = result.stdout.decode().strip()
                return float(duration_str) if duration_str else 0.0

        except Exception as e:
            logger.warning(f"Failed to get audio duration: {e}")

        return 0.0

    async def _create_silence_audio(
        self,
        output_path: Path,
        duration_sec: float,
    ) -> Path:
        """무음 오디오 파일을 생성합니다.

        Args:
            output_path: 출력 파일 경로
            duration_sec: 재생 시간 (초)

        Returns:
            Path: 생성된 파일 경로
        """
        output_path = Path(output_path)

        if self._has_ffmpeg:
            try:
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-f", "lavfi",
                    "-i", f"anullsrc=r=22050:cl=mono:d={duration_sec}",
                    "-c:a", "libmp3lame",
                    "-q:a", "9",
                    str(output_path),
                ]

                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        cmd,
                        capture_output=True,
                        timeout=30,
                    ),
                )

                if result.returncode == 0:
                    return output_path

            except Exception as e:
                logger.warning(f"FFmpeg silence generation failed: {e}")

        # FFmpeg 실패 시: placeholder 바이트
        output_path.write_bytes(b"\x00" * 1024)
        return output_path

    def _create_silent_result(
        self,
        scene_id: str,
        output_dir: Path,
        scene_offset_sec: float,
    ) -> SceneAudioResult:
        """빈 narration에 대한 무음 결과를 생성합니다.

        Args:
            scene_id: 씬 ID
            output_dir: 출력 디렉토리
            scene_offset_sec: 씬 시작 오프셋

        Returns:
            SceneAudioResult: 무음 결과
        """
        audio_path = output_dir / f"{scene_id}_audio.mp3"

        # 동기적으로 무음 파일 생성 (빈 파일)
        audio_path.write_bytes(b"\x00" * 1024)

        duration = self._silence_padding_sec

        return SceneAudioResult(
            scene_id=scene_id,
            audio_path=str(audio_path),
            duration_sec=duration,
            audio_duration_sec=0.0,
            captions=[],
            sentence_count=0,
            failed_sentences=0,
        )


# =============================================================================
# SRT Generation
# =============================================================================


def generate_srt(captions: List[CaptionEntry]) -> str:
    """캡션 리스트를 SRT 형식 문자열로 변환합니다.

    Args:
        captions: 캡션 엔트리 리스트

    Returns:
        str: SRT 형식 문자열
    """
    srt_lines = []

    for i, caption in enumerate(captions, 1):
        start_time = _format_srt_time(caption.start)
        end_time = _format_srt_time(caption.end)

        srt_lines.append(str(i))
        srt_lines.append(f"{start_time} --> {end_time}")
        srt_lines.append(caption.text)
        srt_lines.append("")  # 빈 줄

    return "\n".join(srt_lines)


def _format_srt_time(seconds: float) -> str:
    """초를 SRT 시간 형식(HH:MM:SS,mmm)으로 변환합니다.

    Args:
        seconds: 시간 (초)

    Returns:
        str: SRT 시간 형식
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# =============================================================================
# Singleton
# =============================================================================


_scene_audio_service: Optional[SceneAudioService] = None


def get_scene_audio_service() -> SceneAudioService:
    """SceneAudioService 싱글톤 인스턴스 반환."""
    global _scene_audio_service
    if _scene_audio_service is None:
        _scene_audio_service = SceneAudioService()
    return _scene_audio_service


def clear_scene_audio_service() -> None:
    """SceneAudioService 싱글톤 초기화 (테스트용)."""
    global _scene_audio_service
    _scene_audio_service = None
