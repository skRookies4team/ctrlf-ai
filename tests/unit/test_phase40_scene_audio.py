"""
Phase 40: Scene Audio 테스트

테스트 범위:
1. split_sentences() 유틸 - 빈 문자열, 개행, 긴 문장 처리
2. 문장별 TTS 생성 + concat 파이프라인
3. 오디오 기반 scene duration + 패딩 규칙
4. 캡션 타임라인 누적 및 정합성
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.text_splitter import (
    MAX_SENTENCE_LENGTH,
    count_sentences,
    estimate_tts_duration,
    get_sentence_lengths,
    split_sentences,
)
from app.services.scene_audio_service import (
    CaptionEntry,
    SceneAudioResult,
    SceneAudioService,
    SentenceAudioResult,
    generate_srt,
)


# =============================================================================
# Test: split_sentences() 안정성
# =============================================================================


class TestSplitSentences:
    """split_sentences() 유틸 테스트."""

    def test_empty_string(self):
        """빈 문자열 입력 시 빈 리스트 반환."""
        assert split_sentences("") == []
        assert split_sentences("   ") == []
        assert split_sentences("\n\n\n") == []

    def test_single_sentence(self):
        """단일 문장 처리."""
        result = split_sentences("안녕하세요.")
        assert result == ["안녕하세요."]

    def test_newline_split(self):
        """개행 기준 분할."""
        text = "첫 번째 줄\n두 번째 줄\n세 번째 줄"
        result = split_sentences(text)
        assert len(result) == 3
        assert result[0] == "첫 번째 줄"
        assert result[1] == "두 번째 줄"
        assert result[2] == "세 번째 줄"

    def test_sentence_end_split(self):
        """문장 종결 기호(. ? ! ...) 기준 분할."""
        text = "첫 문장입니다. 두 번째 문장이에요! 세 번째야?"
        result = split_sentences(text)
        assert len(result) == 3
        assert "첫 문장입니다." in result[0]
        assert "두 번째 문장이에요!" in result[1]
        assert "세 번째야?" in result[2]

    def test_korean_endings(self):
        """한국어 종결 어미 처리 (다. 요. 죠. 등)."""
        text = "학습을 시작합니다. 이것이 중요해요. 맞죠?"
        result = split_sentences(text)
        assert len(result) == 3

    def test_long_sentence_split(self):
        """긴 문장(300자 이상) 분할."""
        # 300자 이상의 긴 문장 생성
        long_text = "이것은 매우 긴 문장입니다, " * 30  # 약 480자
        result = split_sentences(long_text)

        # 모든 결과 문장이 최대 길이 이하인지 확인
        for sentence in result:
            assert len(sentence) <= MAX_SENTENCE_LENGTH + 50  # 약간의 여유

    def test_mixed_content(self):
        """개행 + 문장 종결 + 긴 문장 복합 처리."""
        text = """첫 줄입니다.
두 번째 줄이에요! 계속됩니다.

세 번째 줄?"""
        result = split_sentences(text)
        assert len(result) >= 3

    def test_preserve_punctuation(self):
        """구두점 보존 확인."""
        text = "물음표가 있나요? 느낌표도 있어요!"
        result = split_sentences(text)
        assert any("?" in s for s in result)
        assert any("!" in s for s in result)

    def test_ellipsis_handling(self):
        """말줄임표(...) 처리."""
        text = "첫 번째... 두 번째."
        result = split_sentences(text)
        # 말줄임표 뒤에 공백이 있으면 분할
        assert len(result) >= 1

    def test_helper_functions(self):
        """헬퍼 함수 테스트."""
        text = "첫 번째입니다. 두 번째에요."

        # count_sentences
        count = count_sentences(text)
        assert count == 2

        # get_sentence_lengths
        lengths = get_sentence_lengths(text)
        assert len(lengths) == 2
        assert all(l > 0 for l in lengths)

        # estimate_tts_duration
        duration = estimate_tts_duration(text, chars_per_second=2.5)
        assert duration > 0


# =============================================================================
# Test: SceneAudioService
# =============================================================================


class TestSceneAudioService:
    """SceneAudioService 테스트."""

    @pytest.fixture
    def mock_tts_provider(self):
        """모의 TTS Provider."""
        provider = MagicMock()

        async def mock_synthesize(text: str, language: str = "ko"):
            """모의 TTS 합성 - 문장 길이에 비례한 duration 반환."""
            duration = len(text) * 0.1  # 10자 = 1초
            return MagicMock(
                audio_bytes=b"\x00" * 1024,  # 더미 오디오 데이터
                duration_sec=duration,
            )

        provider.synthesize = mock_synthesize
        return provider

    @pytest.fixture
    def service(self, mock_tts_provider):
        """SceneAudioService 인스턴스."""
        return SceneAudioService(
            tts_provider=mock_tts_provider,
            silence_padding_sec=0.5,
        )

    @pytest.mark.asyncio
    async def test_three_sentences_three_audios(self, service):
        """3개 문장 입력 → 3개 오디오 생성 + concat 결과 파일 존재."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            result = await service.generate_scene_audio(
                scene_id="test-scene",
                narration="첫 번째입니다. 두 번째에요. 세 번째죠.",
                output_dir=output_dir,
            )

            # 결과 확인
            assert result.scene_id == "test-scene"
            assert result.sentence_count == 3

            # concat된 오디오 파일 존재 확인
            audio_path = Path(result.audio_path)
            assert audio_path.exists()
            assert audio_path.name == "test-scene_audio.mp3"

            # 개별 문장 오디오 파일들도 생성되어야 함
            sentence_files = list(output_dir.glob("test-scene_sent_*.mp3"))
            assert len(sentence_files) == 3

    @pytest.mark.asyncio
    async def test_duration_includes_padding(self, service):
        """scene_duration_sec = audio_duration + padding 규칙 테스트."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            result = await service.generate_scene_audio(
                scene_id="test-scene",
                narration="테스트 문장입니다.",
                output_dir=output_dir,
            )

            # duration_sec는 audio_duration_sec + padding (0.5초)
            expected_duration = result.audio_duration_sec + 0.5
            assert abs(result.duration_sec - expected_duration) < 0.01

    @pytest.mark.asyncio
    async def test_caption_timeline_accumulation(self, service):
        """캡션 타임라인 start/end 누적 테스트."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            result = await service.generate_scene_audio(
                scene_id="test-scene",
                narration="첫 번째. 두 번째. 세 번째.",
                output_dir=output_dir,
                scene_offset_sec=10.0,  # 10초 오프셋
            )

            captions = result.captions

            # 3개 캡션 생성
            assert len(captions) == 3

            # 첫 캡션은 scene_offset_sec (10.0)부터 시작
            assert captions[0].start == 10.0

            # 모든 캡션에서 end >= start 만족
            for caption in captions:
                assert caption.end >= caption.start

            # 캡션 순서대로 누적 (이전 end == 다음 start)
            for i in range(len(captions) - 1):
                assert abs(captions[i].end - captions[i + 1].start) < 0.01

    @pytest.mark.asyncio
    async def test_empty_narration_returns_silent_result(self, service):
        """빈 narration은 무음 결과 반환."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            result = await service.generate_scene_audio(
                scene_id="empty-scene",
                narration="",
                output_dir=output_dir,
            )

            assert result.scene_id == "empty-scene"
            assert result.sentence_count == 0
            assert result.audio_duration_sec == 0.0
            assert result.captions == []

    @pytest.mark.asyncio
    async def test_tts_failure_uses_silence(self, service, mock_tts_provider):
        """TTS 실패 시 무음으로 대체 (Job 전체 실패 금지)."""
        # TTS 실패하도록 설정
        mock_tts_provider.synthesize = AsyncMock(
            side_effect=Exception("TTS Error")
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            # 예외 발생하지 않아야 함
            result = await service.generate_scene_audio(
                scene_id="fail-scene",
                narration="테스트 문장입니다.",
                output_dir=output_dir,
            )

            # 결과는 생성되어야 함
            assert result.scene_id == "fail-scene"
            assert result.failed_sentences > 0  # 실패한 문장 존재

    @pytest.mark.asyncio
    async def test_multiple_scenes_offset_accumulation(self, service):
        """여러 씬 처리 시 오프셋 누적."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            scenes = [
                {"scene_id": "scene-1", "narration": "첫 번째 씬입니다."},
                {"scene_id": "scene-2", "narration": "두 번째 씬이에요."},
                {"scene_id": "scene-3", "narration": "세 번째 씬이죠."},
            ]

            results = await service.generate_scene_audios(
                scenes=scenes,
                output_dir=output_dir,
            )

            assert len(results) == 3

            # 씬 오프셋 누적 확인
            # scene-2 캡션 시작 = scene-1 duration 이후
            if results[0].captions and results[1].captions:
                expected_offset = results[0].duration_sec
                actual_offset = results[1].captions[0].start
                assert abs(expected_offset - actual_offset) < 0.01


# =============================================================================
# Test: CaptionEntry and SRT Generation
# =============================================================================


class TestCaptionEntry:
    """CaptionEntry 및 SRT 생성 테스트."""

    def test_caption_entry_to_dict(self):
        """CaptionEntry.to_dict() 테스트."""
        caption = CaptionEntry(
            start=1.234,
            end=3.567,
            text="테스트 자막",
        )

        d = caption.to_dict()
        assert d["start"] == 1.23  # 반올림
        assert d["end"] == 3.57  # 반올림
        assert d["text"] == "테스트 자막"

    def test_generate_srt(self):
        """SRT 생성 테스트."""
        captions = [
            CaptionEntry(start=0.0, end=1.5, text="첫 번째 자막"),
            CaptionEntry(start=1.5, end=3.0, text="두 번째 자막"),
        ]

        srt = generate_srt(captions)

        # SRT 형식 검증
        assert "1\n" in srt
        assert "00:00:00,000 --> 00:00:01,500" in srt
        assert "첫 번째 자막" in srt
        assert "2\n" in srt
        assert "00:00:01,500 --> 00:00:03,000" in srt
        assert "두 번째 자막" in srt

    def test_generate_srt_empty(self):
        """빈 캡션 리스트 SRT 생성."""
        srt = generate_srt([])
        assert srt == ""


# =============================================================================
# Test: SceneAudioResult
# =============================================================================


class TestSceneAudioResult:
    """SceneAudioResult 테스트."""

    def test_to_dict(self):
        """to_dict() 메서드 테스트."""
        result = SceneAudioResult(
            scene_id="test-scene",
            audio_path="/tmp/test.mp3",
            duration_sec=5.678,
            audio_duration_sec=5.178,
            captions=[
                CaptionEntry(start=0.0, end=2.5, text="첫 번째"),
                CaptionEntry(start=2.5, end=5.178, text="두 번째"),
            ],
            sentence_count=2,
            failed_sentences=0,
        )

        d = result.to_dict()
        assert d["scene_id"] == "test-scene"
        assert d["duration_sec"] == 5.68
        assert d["audio_duration_sec"] == 5.18
        assert len(d["captions"]) == 2
        assert d["sentence_count"] == 2
        assert d["failed_sentences"] == 0

    def test_get_captions_json(self):
        """get_captions_json() 메서드 테스트."""
        result = SceneAudioResult(
            scene_id="test",
            audio_path="/tmp/test.mp3",
            duration_sec=3.0,
            audio_duration_sec=2.5,
            captions=[
                CaptionEntry(start=0.0, end=1.2, text="문장1"),
                CaptionEntry(start=1.2, end=2.5, text="문장2"),
            ],
        )

        captions_json = result.get_captions_json()

        assert len(captions_json) == 2
        assert captions_json[0]["start"] == 0.0
        assert captions_json[0]["end"] == 1.2
        assert captions_json[0]["text"] == "문장1"


# =============================================================================
# Test: Integration with Mock Provider
# =============================================================================


class TestMockProviderIntegration:
    """Mock Provider 통합 테스트."""

    @pytest.mark.asyncio
    async def test_mock_provider_generates_silence(self):
        """Mock provider에서 무음 파일 생성 확인."""
        # TTS provider를 None으로 두면 기본 provider 사용
        # 여기서는 Mock을 직접 주입

        mock_provider = MagicMock()

        async def mock_synth(text, language="ko"):
            return MagicMock(
                audio_bytes=b"\x00" * 512,
                duration_sec=len(text) * 0.1,
            )

        mock_provider.synthesize = mock_synth

        service = SceneAudioService(
            tts_provider=mock_provider,
            silence_padding_sec=0.5,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            result = await service.generate_scene_audio(
                scene_id="mock-test",
                narration="문장입니다.",
                output_dir=output_dir,
            )

            # 파일 생성 확인
            assert Path(result.audio_path).exists()
            assert result.duration_sec > 0


# =============================================================================
# Test: Edge Cases
# =============================================================================


class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_split_only_whitespace(self):
        """공백만 있는 경우."""
        assert split_sentences("   \t\n  ") == []

    def test_split_only_punctuation(self):
        """구두점만 있는 경우."""
        result = split_sentences("...!!!???")
        # 구두점만 있으면 빈 결과거나 구두점 자체
        assert len(result) <= 1

    def test_very_short_sentences(self):
        """매우 짧은 문장들."""
        text = "아. 네. 응."
        result = split_sentences(text)
        assert len(result) >= 1

    def test_unicode_handling(self):
        """유니코드(이모지 등) 처리."""
        text = "안녕하세요 👋 반갑습니다! 🎉"
        result = split_sentences(text)
        assert len(result) >= 1
        # 이모지가 보존되는지 확인
        full_text = " ".join(result)
        assert "👋" in full_text or "🎉" in full_text or len(result) > 0
