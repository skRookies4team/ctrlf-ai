"""
Phase 32: TTS Provider Adapter

Text-to-Speech 서비스 어댑터 인터페이스 및 구현체.

지원 Provider:
- mock: 테스트용 Mock TTS (무음 파일 생성)
- polly: AWS Polly
- gcp: Google Cloud TTS
- gtts: Google TTS (무료, 간단한 용도)

환경변수:
- TTS_PROVIDER: mock | polly | gcp | gtts (기본: mock)
- AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (polly용)
- GOOGLE_APPLICATION_CREDENTIALS (gcp용)
"""

import asyncio
import io
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Union

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Types and Enums
# =============================================================================


class TTSProvider(str, Enum):
    """TTS Provider 종류."""
    MOCK = "mock"
    POLLY = "polly"
    GCP = "gcp"
    GTTS = "gtts"


class TTSVoice(str, Enum):
    """TTS 음성 종류 (기본 제공)."""
    # Korean
    KO_FEMALE = "ko-female"
    KO_MALE = "ko-male"
    # English
    EN_FEMALE = "en-female"
    EN_MALE = "en-male"


@dataclass
class TTSResult:
    """TTS 합성 결과."""
    audio_bytes: bytes
    duration_sec: float
    format: str = "mp3"
    sample_rate: int = 22050


@dataclass
class TTSConfig:
    """TTS 설정."""
    provider: TTSProvider = TTSProvider.MOCK
    language: str = "ko"
    voice: str = "ko-female"
    speed: float = 1.0
    # Provider별 설정
    aws_region: str = "ap-northeast-2"
    aws_polly_voice_id: str = "Seoyeon"  # Korean female
    gcp_voice_name: str = "ko-KR-Wavenet-A"
    gcp_speaking_rate: float = 1.0


# =============================================================================
# Abstract Base Provider
# =============================================================================


class BaseTTSProvider(ABC):
    """TTS Provider 기본 인터페이스."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: float = 1.0,
        language: str = "ko",
    ) -> TTSResult:
        """텍스트를 음성으로 변환합니다.

        Args:
            text: 변환할 텍스트
            voice: 음성 ID (provider별 상이)
            speed: 재생 속도 (1.0 = 기본)
            language: 언어 코드

        Returns:
            TTSResult: 합성된 오디오 데이터
        """
        pass

    async def synthesize_to_file(
        self,
        text: str,
        output_path: Union[str, Path],
        voice: Optional[str] = None,
        speed: float = 1.0,
        language: str = "ko",
    ) -> float:
        """텍스트를 음성 파일로 저장합니다.

        Args:
            text: 변환할 텍스트
            output_path: 출력 파일 경로
            voice: 음성 ID
            speed: 재생 속도
            language: 언어 코드

        Returns:
            float: 오디오 길이 (초)
        """
        result = await self.synthesize(text, voice, speed, language)
        Path(output_path).write_bytes(result.audio_bytes)
        return result.duration_sec


# =============================================================================
# Mock TTS Provider
# =============================================================================


class MockTTSProvider(BaseTTSProvider):
    """Mock TTS Provider (테스트용).

    실제 음성 합성 없이 무음 파일을 생성합니다.
    """

    # 한국어 기준 분당 약 150자 (초당 2.5자)
    CHARS_PER_SECOND = 2.5

    async def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: float = 1.0,
        language: str = "ko",
    ) -> TTSResult:
        """Mock 음성 합성."""
        # 텍스트 길이로 duration 추정
        duration_sec = len(text) / (self.CHARS_PER_SECOND * speed)
        duration_sec = max(duration_sec, 1.0)  # 최소 1초

        # 무음 MP3 파일 생성 (44 bytes minimal MP3 header)
        # 실제로는 ffmpeg로 생성하는 것이 좋지만 테스트용으로 간단히 처리
        mock_audio = self._generate_silent_mp3(duration_sec)

        logger.info(f"Mock TTS synthesized: text_len={len(text)}, duration={duration_sec:.1f}s")

        return TTSResult(
            audio_bytes=mock_audio,
            duration_sec=duration_sec,
            format="mp3",
        )

    def _generate_silent_mp3(self, duration_sec: float) -> bytes:
        """무음 MP3 데이터 생성 (간단한 placeholder)."""
        # 실제로는 ffmpeg로 무음 오디오를 생성해야 하지만,
        # 테스트용으로 빈 바이트를 반환
        # FFmpeg 실제 렌더 단계에서 이 파일이 사용될 때 처리됨
        return b"\x00" * 1024  # Placeholder


# =============================================================================
# gTTS Provider (무료 Google TTS)
# =============================================================================


class GTTSProvider(BaseTTSProvider):
    """gTTS Provider (무료 Google TTS).

    pip install gtts
    """

    async def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: float = 1.0,
        language: str = "ko",
    ) -> TTSResult:
        """gTTS 음성 합성."""
        try:
            from gtts import gTTS
        except ImportError:
            logger.error("gTTS not installed. Run: pip install gtts")
            raise RuntimeError("gTTS library not available")

        # gTTS는 비동기가 아니므로 executor에서 실행
        loop = asyncio.get_event_loop()

        def _synthesize():
            tts = gTTS(text=text, lang=language, slow=(speed < 0.8))
            buffer = io.BytesIO()
            tts.write_to_fp(buffer)
            return buffer.getvalue()

        audio_bytes = await loop.run_in_executor(None, _synthesize)

        # duration 추정 (gTTS는 실제 duration을 제공하지 않음)
        duration_sec = len(text) / 2.5  # 대략적 추정
        duration_sec = max(duration_sec, 1.0)

        logger.info(f"gTTS synthesized: text_len={len(text)}, size={len(audio_bytes)}")

        return TTSResult(
            audio_bytes=audio_bytes,
            duration_sec=duration_sec,
            format="mp3",
        )


# =============================================================================
# AWS Polly Provider
# =============================================================================


class PollyTTSProvider(BaseTTSProvider):
    """AWS Polly TTS Provider.

    환경변수:
    - AWS_REGION (기본: ap-northeast-2)
    - AWS_ACCESS_KEY_ID
    - AWS_SECRET_ACCESS_KEY
    """

    # Polly 한국어 음성
    KOREAN_VOICES = {
        "ko-female": "Seoyeon",
        "ko-male": "Seoyeon",  # 한국어 남성 음성 없음
    }

    ENGLISH_VOICES = {
        "en-female": "Joanna",
        "en-male": "Matthew",
    }

    def __init__(self, region: Optional[str] = None):
        self.region = region or os.getenv("AWS_REGION", "ap-northeast-2")
        self._client = None

    def _get_client(self):
        """Boto3 Polly 클라이언트 반환."""
        if self._client is None:
            try:
                import boto3
            except ImportError:
                raise RuntimeError("boto3 not installed. Run: pip install boto3")
            self._client = boto3.client("polly", region_name=self.region)
        return self._client

    async def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: float = 1.0,
        language: str = "ko",
    ) -> TTSResult:
        """AWS Polly 음성 합성."""
        client = self._get_client()

        # 음성 선택
        voice_id = self._get_voice_id(voice, language)

        # SSML로 속도 조절
        if speed != 1.0:
            rate = f"{int(speed * 100)}%"
            ssml_text = f'<speak><prosody rate="{rate}">{text}</prosody></speak>'
            text_type = "ssml"
        else:
            ssml_text = text
            text_type = "text"

        loop = asyncio.get_event_loop()

        def _synthesize():
            response = client.synthesize_speech(
                Text=ssml_text,
                TextType=text_type,
                OutputFormat="mp3",
                VoiceId=voice_id,
                Engine="neural" if language == "ko" else "standard",
            )
            return response["AudioStream"].read()

        audio_bytes = await loop.run_in_executor(None, _synthesize)

        # Duration 추정
        duration_sec = len(text) / 2.5
        duration_sec = max(duration_sec, 1.0)

        logger.info(f"Polly synthesized: voice={voice_id}, size={len(audio_bytes)}")

        return TTSResult(
            audio_bytes=audio_bytes,
            duration_sec=duration_sec,
            format="mp3",
        )

    def _get_voice_id(self, voice: Optional[str], language: str) -> str:
        """음성 ID 반환."""
        if voice and voice in self.KOREAN_VOICES:
            return self.KOREAN_VOICES[voice]
        if voice and voice in self.ENGLISH_VOICES:
            return self.ENGLISH_VOICES[voice]

        # 언어별 기본 음성
        if language.startswith("ko"):
            return "Seoyeon"
        return "Joanna"


# =============================================================================
# Google Cloud TTS Provider
# =============================================================================


class GCPTTSProvider(BaseTTSProvider):
    """Google Cloud TTS Provider.

    환경변수:
    - GOOGLE_APPLICATION_CREDENTIALS: 서비스 계정 키 파일 경로

    pip install google-cloud-texttospeech
    """

    KOREAN_VOICES = {
        "ko-female": "ko-KR-Wavenet-A",
        "ko-male": "ko-KR-Wavenet-C",
    }

    ENGLISH_VOICES = {
        "en-female": "en-US-Wavenet-F",
        "en-male": "en-US-Wavenet-D",
    }

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Google Cloud TTS 클라이언트 반환."""
        if self._client is None:
            try:
                from google.cloud import texttospeech
            except ImportError:
                raise RuntimeError(
                    "google-cloud-texttospeech not installed. "
                    "Run: pip install google-cloud-texttospeech"
                )
            self._client = texttospeech.TextToSpeechClient()
        return self._client

    async def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: float = 1.0,
        language: str = "ko",
    ) -> TTSResult:
        """Google Cloud TTS 음성 합성."""
        try:
            from google.cloud import texttospeech
        except ImportError:
            raise RuntimeError("google-cloud-texttospeech not installed")

        client = self._get_client()

        # 음성 선택
        voice_name = self._get_voice_name(voice, language)
        language_code = "ko-KR" if language.startswith("ko") else "en-US"

        loop = asyncio.get_event_loop()

        def _synthesize():
            synthesis_input = texttospeech.SynthesisInput(text=text)
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=speed,
            )
            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config,
            )
            return response.audio_content

        audio_bytes = await loop.run_in_executor(None, _synthesize)

        # Duration 추정
        duration_sec = len(text) / 2.5
        duration_sec = max(duration_sec, 1.0)

        logger.info(f"GCP TTS synthesized: voice={voice_name}, size={len(audio_bytes)}")

        return TTSResult(
            audio_bytes=audio_bytes,
            duration_sec=duration_sec,
            format="mp3",
        )

    def _get_voice_name(self, voice: Optional[str], language: str) -> str:
        """음성 이름 반환."""
        if voice and voice in self.KOREAN_VOICES:
            return self.KOREAN_VOICES[voice]
        if voice and voice in self.ENGLISH_VOICES:
            return self.ENGLISH_VOICES[voice]

        # 언어별 기본 음성
        if language.startswith("ko"):
            return "ko-KR-Wavenet-A"
        return "en-US-Wavenet-F"


# =============================================================================
# Factory Function
# =============================================================================


def get_tts_provider(provider: Optional[TTSProvider] = None) -> BaseTTSProvider:
    """TTS Provider 인스턴스를 반환합니다.

    Args:
        provider: Provider 종류. None이면 환경변수 TTS_PROVIDER 사용.

    Returns:
        BaseTTSProvider: TTS Provider 인스턴스

    환경변수:
        TTS_PROVIDER: mock | polly | gcp | gtts
    """
    if provider is None:
        provider_str = os.getenv("TTS_PROVIDER", "mock").lower()
        try:
            provider = TTSProvider(provider_str)
        except ValueError:
            logger.warning(f"Unknown TTS_PROVIDER: {provider_str}, using mock")
            provider = TTSProvider.MOCK

    if provider == TTSProvider.MOCK:
        return MockTTSProvider()
    elif provider == TTSProvider.GTTS:
        return GTTSProvider()
    elif provider == TTSProvider.POLLY:
        return PollyTTSProvider()
    elif provider == TTSProvider.GCP:
        return GCPTTSProvider()
    else:
        logger.warning(f"Unknown provider: {provider}, using mock")
        return MockTTSProvider()


# =============================================================================
# Singleton for default provider
# =============================================================================


_default_provider: Optional[BaseTTSProvider] = None


def get_default_tts_provider() -> BaseTTSProvider:
    """기본 TTS Provider 싱글톤 인스턴스 반환."""
    global _default_provider
    if _default_provider is None:
        _default_provider = get_tts_provider()
    return _default_provider


def clear_tts_provider() -> None:
    """TTS Provider 싱글톤 초기화 (테스트용)."""
    global _default_provider
    _default_provider = None
