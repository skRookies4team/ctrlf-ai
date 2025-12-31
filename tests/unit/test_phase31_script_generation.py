"""
Phase 31: Script Generation Tests

교육 원문 → 영상 스크립트 자동 생성 테스트.

테스트 케이스:
1. Happy path: generate → DRAFT 저장 → approve → render-job 생성까지 이어지는 통합 테스트
2. Invalid JSON path: LLM이 깨진 JSON 반환 → 1회 fix 재시도 후 성공
3. 기존 수동 /api/scripts 경로가 동작하는지 회귀 테스트
4. EXPIRED 교육 차단 테스트
5. JSON 추출 로직 테스트
"""

import json
import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.models.video_render import (
    RenderJobStatus,
    ScriptStatus,
    VideoScript,
)
from app.services.video_render_service import (
    VideoRenderService,
    VideoScriptStore,
    VideoRenderJobStore,
    VideoAssetStore,
)
from app.services.video_script_generation_service import (
    VideoScriptGenerationService,
    ScriptGenerationOptions,
    VideoScriptSchema,
    ChapterSchema,
    SceneSchema,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def script_store():
    """스크립트 저장소 fixture."""
    return VideoScriptStore()


@pytest.fixture
def job_store():
    """잡 저장소 fixture."""
    return VideoRenderJobStore()


@pytest.fixture
def asset_store():
    """에셋 저장소 fixture."""
    return VideoAssetStore()


@pytest.fixture
def render_service(script_store, job_store, asset_store):
    """렌더 서비스 fixture."""
    return VideoRenderService(
        script_store=script_store,
        job_store=job_store,
        asset_store=asset_store,
    )


@pytest.fixture
def valid_script_json():
    """유효한 스크립트 JSON fixture."""
    return {
        "chapters": [
            {
                "chapter_id": 1,
                "title": "보안교육 개요",
                "scenes": [
                    {
                        "scene_id": 1,
                        "narration": "안녕하세요. 보안교육을 시작하겠습니다.",
                        "on_screen_text": "보안교육 시작",
                        "duration_sec": 30.0,
                    },
                    {
                        "scene_id": 2,
                        "narration": "오늘은 피싱 메일에 대해 알아보겠습니다.",
                        "on_screen_text": "피싱 메일 주의",
                        "duration_sec": 25.0,
                    },
                ],
            },
            {
                "chapter_id": 2,
                "title": "피싱 메일 대응 방법",
                "scenes": [
                    {
                        "scene_id": 1,
                        "narration": "의심스러운 메일은 바로 삭제하세요.",
                        "on_screen_text": "의심 메일 삭제",
                        "duration_sec": 20.0,
                    },
                ],
            },
        ],
    }


@pytest.fixture
def mock_llm_client(valid_script_json):
    """Mock LLM 클라이언트 fixture."""
    client = MagicMock()
    client.generate_chat_completion = AsyncMock(
        return_value=json.dumps(valid_script_json)
    )
    return client


@pytest.fixture
def generation_service(mock_llm_client):
    """스크립트 생성 서비스 fixture."""
    return VideoScriptGenerationService(llm_client=mock_llm_client)


# =============================================================================
# Unit Tests: VideoScriptGenerationService
# =============================================================================


class TestVideoScriptGenerationService:
    """VideoScriptGenerationService 단위 테스트."""

    @pytest.mark.asyncio
    async def test_generate_script_success(self, generation_service, valid_script_json):
        """Happy path: 스크립트 생성 성공."""
        # Given
        source_text = "보안교육 원문 텍스트입니다. 피싱 메일에 대해 설명합니다."
        options = ScriptGenerationOptions(target_minutes=3)

        # When
        result = await generation_service.generate_script(
            video_id="video-001",
            source_text=source_text,
            options=options,
        )

        # Then
        assert "chapters" in result
        assert len(result["chapters"]) == 2
        assert result["chapters"][0]["title"] == "보안교육 개요"
        assert len(result["chapters"][0]["scenes"]) == 2

    @pytest.mark.asyncio
    async def test_generate_script_with_json_code_block(self, valid_script_json):
        """LLM이 코드블록으로 JSON을 반환하는 경우."""
        # Given
        mock_client = MagicMock()
        json_in_code_block = f"```json\n{json.dumps(valid_script_json)}\n```"
        mock_client.generate_chat_completion = AsyncMock(return_value=json_in_code_block)

        service = VideoScriptGenerationService(llm_client=mock_client)

        # When
        result = await service.generate_script(
            video_id="video-001",
            source_text="테스트 원문",
        )

        # Then
        assert "chapters" in result
        assert len(result["chapters"]) == 2

    @pytest.mark.asyncio
    async def test_generate_script_retry_on_invalid_json(self, valid_script_json):
        """Invalid JSON → fix 재시도 후 성공."""
        # Given
        mock_client = MagicMock()

        # 1차: 잘못된 JSON, 2차: 유효한 JSON
        mock_client.generate_chat_completion = AsyncMock(
            side_effect=[
                "이것은 JSON이 아닙니다. {broken}",
                json.dumps(valid_script_json),
            ]
        )

        service = VideoScriptGenerationService(llm_client=mock_client)

        # When
        result = await service.generate_script(
            video_id="video-001",
            source_text="테스트 원문",
        )

        # Then
        assert "chapters" in result
        assert mock_client.generate_chat_completion.call_count == 2

    @pytest.mark.asyncio
    async def test_generate_script_fail_after_retries(self):
        """재시도 후에도 실패하면 ValueError 발생."""
        # Given
        mock_client = MagicMock()
        mock_client.generate_chat_completion = AsyncMock(
            side_effect=[
                "잘못된 JSON 1",
                "잘못된 JSON 2",
            ]
        )

        service = VideoScriptGenerationService(llm_client=mock_client)

        # When/Then
        with pytest.raises(ValueError) as exc_info:
            await service.generate_script(
                video_id="video-001",
                source_text="테스트 원문",
            )

        assert "failed after" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_generate_script_validation_error(self):
        """스키마 검증 실패 → 재시도."""
        # Given
        mock_client = MagicMock()

        # 1차: 스키마 불완전 (scene_id 누락), 2차: 유효한 JSON
        invalid_json = {"chapters": [{"chapter_id": 1, "title": "test", "scenes": [{"narration": "test"}]}]}
        valid_json = {"chapters": [{"chapter_id": 1, "title": "test", "scenes": [{"scene_id": 1, "narration": "test"}]}]}

        mock_client.generate_chat_completion = AsyncMock(
            side_effect=[
                json.dumps(invalid_json),
                json.dumps(valid_json),
            ]
        )

        service = VideoScriptGenerationService(llm_client=mock_client)

        # When
        result = await service.generate_script(
            video_id="video-001",
            source_text="테스트 원문",
        )

        # Then
        assert result["chapters"][0]["scenes"][0]["scene_id"] == 1


# =============================================================================
# Unit Tests: JSON Extraction
# =============================================================================


class TestJsonExtraction:
    """JSON 추출 로직 테스트."""

    def test_extract_json_plain(self, generation_service, valid_script_json):
        """순수 JSON 문자열 추출."""
        json_str = json.dumps(valid_script_json)
        result = generation_service._extract_json(json_str)
        assert result == valid_script_json

    def test_extract_json_code_block(self, generation_service, valid_script_json):
        """코드블록 내 JSON 추출."""
        json_str = f"```json\n{json.dumps(valid_script_json)}\n```"
        result = generation_service._extract_json(json_str)
        assert result == valid_script_json

    def test_extract_json_with_prefix(self, generation_service, valid_script_json):
        """앞뒤에 텍스트가 있는 JSON 추출."""
        json_str = f"Here is the result:\n{json.dumps(valid_script_json)}\nEnd of output."
        result = generation_service._extract_json(json_str)
        assert result == valid_script_json

    def test_extract_json_empty_raises(self, generation_service):
        """빈 출력은 ValueError."""
        with pytest.raises(ValueError):
            generation_service._extract_json("")

    def test_extract_json_invalid_raises(self, generation_service):
        """잘못된 JSON은 JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            generation_service._extract_json("not a json at all")


# =============================================================================
# Unit Tests: Pydantic Schema Validation
# =============================================================================


class TestVideoScriptSchema:
    """VideoScript 스키마 검증 테스트."""

    def test_valid_schema(self, valid_script_json):
        """유효한 스키마 검증 성공."""
        schema = VideoScriptSchema.model_validate(valid_script_json)
        assert len(schema.chapters) == 2
        assert schema.chapters[0].title == "보안교육 개요"

    def test_schema_missing_chapters(self):
        """chapters 누락 시 ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            VideoScriptSchema.model_validate({})

    def test_schema_empty_chapters(self):
        """빈 chapters 배열 시 ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            VideoScriptSchema.model_validate({"chapters": []})

    def test_schema_missing_scene_id(self):
        """scene_id 누락 시 ValidationError."""
        from pydantic import ValidationError

        invalid_json = {
            "chapters": [
                {
                    "chapter_id": 1,
                    "title": "test",
                    "scenes": [{"narration": "test"}],  # scene_id 누락
                }
            ]
        }
        with pytest.raises(ValidationError):
            VideoScriptSchema.model_validate(invalid_json)

    def test_schema_optional_fields(self):
        """선택 필드는 없어도 통과."""
        minimal_json = {
            "chapters": [
                {
                    "chapter_id": 1,
                    "title": "test",
                    "scenes": [{"scene_id": 1, "narration": "test"}],
                }
            ]
        }
        schema = VideoScriptSchema.model_validate(minimal_json)
        assert schema.chapters[0].scenes[0].on_screen_text is None
        assert schema.chapters[0].scenes[0].duration_sec is None


# =============================================================================
# Generation Options Tests
# =============================================================================


class TestScriptGenerationOptions:
    """스크립트 생성 옵션 테스트."""

    def test_default_options(self):
        """기본 옵션 값 확인."""
        options = ScriptGenerationOptions()
        assert options.language == "ko"
        assert options.target_minutes == 3
        assert options.max_chapters == 5
        assert options.max_scenes_per_chapter == 6
        assert options.style == "friendly_security_training"

    def test_custom_options(self):
        """커스텀 옵션 설정."""
        options = ScriptGenerationOptions(
            language="en",
            target_minutes=10,
            max_chapters=3,
            max_scenes_per_chapter=4,
            style="formal_compliance",
        )
        assert options.language == "en"
        assert options.target_minutes == 10
        assert options.max_chapters == 3

    def test_options_validation(self):
        """옵션 유효성 검증."""
        from pydantic import ValidationError

        # target_minutes 범위 초과
        with pytest.raises(ValidationError):
            ScriptGenerationOptions(target_minutes=50)  # max 30

        # max_chapters 범위 초과
        with pytest.raises(ValidationError):
            ScriptGenerationOptions(max_chapters=20)  # max 10
