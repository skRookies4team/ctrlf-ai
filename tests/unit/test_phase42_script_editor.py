"""
Phase 42: Script Editor 테스트

테스트 범위:
1. EditorView 변환 테스트 (VideoScript → ScriptEditorView)
2. Patch 적용 테스트 (narration 수정 → 산출물 무효화 확인)
3. status 제약 테스트 (DRAFT만 수정 가능)
"""

import pytest
from copy import deepcopy
from unittest.mock import MagicMock, patch

from app.models.script_editor import (
    SceneEditorItem,
    SceneUpdateItem,
    ScriptEditorPatchRequest,
    ScriptEditorPatchResponse,
    ScriptEditorView,
    apply_scene_updates,
    script_to_editor_view,
    _invalidate_audio_outputs,
    _invalidate_caption_outputs,
)
from app.models.video_render import ScriptStatus, VideoScript


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_raw_json():
    """테스트용 샘플 raw_json 데이터."""
    return {
        "language": "ko",
        "chapters": [
            {
                "chapter_id": 1,
                "title": "소개",
                "scenes": [
                    {
                        "scene_id": 1,
                        "narration": "안녕하세요, 첫 번째 씬입니다.",
                        "on_screen_text": "소개 씬",
                        "subtitle_text": "안녕하세요",
                        "tts_audio_path": "/audio/scene1.mp3",
                        "audio_duration_sec": 3.5,
                        "captions": [{"start": 0, "end": 3.5, "text": "안녕하세요"}],
                    },
                    {
                        "scene_id": 2,
                        "narration": "두 번째 씬입니다.",
                        "on_screen_text": "본론",
                        "tts_audio_path": "/audio/scene2.mp3",
                        "audio_duration_sec": 2.5,
                        "captions": [{"start": 3.5, "end": 6.0, "text": "두 번째"}],
                    },
                ],
            },
            {
                "chapter_id": 2,
                "title": "결론",
                "scenes": [
                    {
                        "scene_id": 3,
                        "narration": "마지막 씬입니다.",
                        "on_screen_text": "결론",
                        "audio_path": "/audio/scene3.mp3",
                        "caption_timeline": [{"start": 6.0, "end": 8.0, "text": "마지막"}],
                    },
                ],
            },
        ],
    }


@pytest.fixture
def sample_video_script(sample_raw_json):
    """테스트용 VideoScript 객체."""
    return VideoScript(
        script_id="script-001",
        video_id="video-001",
        raw_json=sample_raw_json,
        status=ScriptStatus.DRAFT,
        created_by="test-user",
    )


@pytest.fixture
def approved_video_script(sample_raw_json):
    """APPROVED 상태의 VideoScript 객체."""
    return VideoScript(
        script_id="script-002",
        video_id="video-002",
        raw_json=sample_raw_json,
        status=ScriptStatus.APPROVED,
        created_by="test-user",
    )


# =============================================================================
# Test: EditorView 변환 테스트
# =============================================================================


class TestScriptToEditorView:
    """VideoScript → ScriptEditorView 변환 테스트."""

    def test_basic_conversion(self, sample_video_script):
        """기본 변환 테스트."""
        view = script_to_editor_view(sample_video_script)

        assert view.script_id == "script-001"
        assert view.video_id == "video-001"
        assert view.language == "ko"
        assert view.status == "DRAFT"
        assert view.editable is True
        assert view.total_scenes == 3
        assert len(view.scenes) == 3

    def test_scene_order_is_sequential(self, sample_video_script):
        """씬 순서가 1-based로 순차적인지 확인."""
        view = script_to_editor_view(sample_video_script)

        orders = [s.order for s in view.scenes]
        assert orders == [1, 2, 3]

    def test_scene_details_extraction(self, sample_video_script):
        """씬 상세 정보 추출 테스트."""
        view = script_to_editor_view(sample_video_script)

        scene1 = view.scenes[0]
        assert scene1.scene_id == 1
        assert scene1.chapter_id == 1
        assert scene1.chapter_title == "소개"
        assert scene1.narration_text == "안녕하세요, 첫 번째 씬입니다."
        assert scene1.scene_title == "소개 씬"
        assert scene1.subtitle_text == "안녕하세요"

    def test_has_audio_detection(self, sample_video_script):
        """오디오 존재 여부 감지."""
        view = script_to_editor_view(sample_video_script)

        # scene 1, 2: tts_audio_path 존재
        assert view.scenes[0].has_audio is True
        assert view.scenes[1].has_audio is True

        # scene 3: audio_path 존재
        assert view.scenes[2].has_audio is True

    def test_has_captions_detection(self, sample_video_script):
        """캡션 존재 여부 감지."""
        view = script_to_editor_view(sample_video_script)

        # scene 1, 2: captions 존재
        assert view.scenes[0].has_captions is True
        assert view.scenes[1].has_captions is True

        # scene 3: caption_timeline 존재
        assert view.scenes[2].has_captions is True

    def test_title_from_first_chapter(self, sample_video_script):
        """첫 챕터 제목이 title로 설정되는지 확인."""
        view = script_to_editor_view(sample_video_script)
        assert view.title == "소개"

    def test_approved_status_not_editable(self, approved_video_script):
        """APPROVED 상태는 editable=False."""
        view = script_to_editor_view(approved_video_script)
        assert view.editable is False
        assert view.status == "APPROVED"

    def test_empty_chapters(self):
        """빈 chapters 배열 처리."""
        script = VideoScript(
            script_id="empty-script",
            video_id="video-empty",
            raw_json={"chapters": []},
            status=ScriptStatus.DRAFT,
            created_by="test-user",
        )
        view = script_to_editor_view(script)

        assert view.total_scenes == 0
        assert view.scenes == []
        assert view.title is None

    def test_subtitle_fallback_to_on_screen_text(self, sample_raw_json):
        """subtitle_text가 없으면 on_screen_text로 대체."""
        # scene 2에는 subtitle_text가 없음
        script = VideoScript(
            script_id="test",
            video_id="test-video",
            raw_json=sample_raw_json,
            status=ScriptStatus.DRAFT,
            created_by="test-user",
        )
        view = script_to_editor_view(script)

        # scene 2의 subtitle_text는 on_screen_text ("본론")로 대체됨
        assert view.scenes[1].subtitle_text == "본론"


# =============================================================================
# Test: Patch 적용 및 무효화 테스트
# =============================================================================


class TestApplySceneUpdates:
    """apply_scene_updates() 테스트."""

    def test_narration_change_invalidates_audio_and_captions(self, sample_raw_json):
        """narration 변경 시 오디오 + 캡션 모두 무효화."""
        raw_json = deepcopy(sample_raw_json)
        updates = [
            SceneUpdateItem(
                scene_id=1,
                narration_text="새로운 나레이션입니다.",
            )
        ]

        updated_json, updated_ids, audio_count, caption_count = apply_scene_updates(
            raw_json, updates
        )

        # 업데이트 확인
        assert 1 in updated_ids
        assert audio_count == 1
        assert caption_count == 1

        # narration 변경 확인
        scene1 = updated_json["chapters"][0]["scenes"][0]
        assert scene1["narration"] == "새로운 나레이션입니다."

        # 오디오 필드 무효화 확인
        assert scene1.get("tts_audio_path") is None
        assert scene1.get("audio_duration_sec") is None

        # 캡션 필드 무효화 확인
        assert scene1.get("captions") is None

    def test_subtitle_only_change_invalidates_captions_only(self, sample_raw_json):
        """subtitle_text만 변경 시 캡션만 무효화."""
        raw_json = deepcopy(sample_raw_json)
        updates = [
            SceneUpdateItem(
                scene_id=1,
                subtitle_text="새로운 자막",
            )
        ]

        updated_json, updated_ids, audio_count, caption_count = apply_scene_updates(
            raw_json, updates
        )

        # 업데이트 확인
        assert 1 in updated_ids
        assert audio_count == 0  # 오디오는 무효화 안됨
        assert caption_count == 1  # 캡션만 무효화

        # subtitle 변경 확인
        scene1 = updated_json["chapters"][0]["scenes"][0]
        assert scene1["subtitle_text"] == "새로운 자막"

        # 오디오 필드는 유지
        assert scene1.get("tts_audio_path") == "/audio/scene1.mp3"

        # 캡션 필드 무효화 확인
        assert scene1.get("captions") is None

    def test_both_narration_and_subtitle_change(self, sample_raw_json):
        """narration + subtitle 동시 변경."""
        raw_json = deepcopy(sample_raw_json)
        updates = [
            SceneUpdateItem(
                scene_id=1,
                narration_text="새 나레이션",
                subtitle_text="새 자막",
            )
        ]

        updated_json, updated_ids, audio_count, caption_count = apply_scene_updates(
            raw_json, updates
        )

        # narration 변경이 우선이므로 오디오 + 캡션 모두 무효화
        assert audio_count == 1
        assert caption_count == 1

        scene1 = updated_json["chapters"][0]["scenes"][0]
        assert scene1["narration"] == "새 나레이션"
        assert scene1["subtitle_text"] == "새 자막"

    def test_multiple_scenes_update(self, sample_raw_json):
        """여러 씬 동시 업데이트."""
        raw_json = deepcopy(sample_raw_json)
        updates = [
            SceneUpdateItem(scene_id=1, narration_text="씬1 새 나레이션"),
            SceneUpdateItem(scene_id=2, subtitle_text="씬2 새 자막"),
            SceneUpdateItem(scene_id=3, narration_text="씬3 새 나레이션"),
        ]

        updated_json, updated_ids, audio_count, caption_count = apply_scene_updates(
            raw_json, updates
        )

        assert len(updated_ids) == 3
        assert 1 in updated_ids
        assert 2 in updated_ids
        assert 3 in updated_ids

        # scene 1, 3: narration 변경 → 오디오+캡션 무효화
        # scene 2: subtitle만 변경 → 캡션만 무효화
        assert audio_count == 2
        assert caption_count == 3

    def test_no_change_if_same_value(self, sample_raw_json):
        """같은 값으로 업데이트하면 변경 없음."""
        raw_json = deepcopy(sample_raw_json)
        updates = [
            SceneUpdateItem(
                scene_id=1,
                narration_text="안녕하세요, 첫 번째 씬입니다.",  # 기존과 동일
            )
        ]

        updated_json, updated_ids, audio_count, caption_count = apply_scene_updates(
            raw_json, updates
        )

        # 변경 없음
        assert updated_ids == []
        assert audio_count == 0
        assert caption_count == 0

    def test_nonexistent_scene_ignored(self, sample_raw_json):
        """존재하지 않는 scene_id는 무시."""
        raw_json = deepcopy(sample_raw_json)
        updates = [
            SceneUpdateItem(scene_id=999, narration_text="새 나레이션"),
        ]

        updated_json, updated_ids, audio_count, caption_count = apply_scene_updates(
            raw_json, updates
        )

        assert updated_ids == []
        assert audio_count == 0
        assert caption_count == 0


# =============================================================================
# Test: 무효화 헬퍼 함수
# =============================================================================


class TestInvalidationHelpers:
    """무효화 헬퍼 함수 테스트."""

    def test_invalidate_audio_outputs(self):
        """_invalidate_audio_outputs() 테스트."""
        scene = {
            "scene_id": 1,
            "tts_audio_path": "/audio/test.mp3",
            "audio_path": "/audio/test2.mp3",
            "audio_duration_sec": 5.0,
            "duration_sec": 5.5,
            "narration": "테스트",
        }

        _invalidate_audio_outputs(scene)

        assert scene["tts_audio_path"] is None
        assert scene["audio_path"] is None
        assert scene["audio_duration_sec"] is None
        assert scene["duration_sec"] is None
        # narration은 유지
        assert scene["narration"] == "테스트"

    def test_invalidate_caption_outputs(self):
        """_invalidate_caption_outputs() 테스트."""
        scene = {
            "scene_id": 1,
            "captions": [{"start": 0, "end": 1, "text": "test"}],
            "caption_timeline": [{"start": 0, "end": 1}],
            "srt_path": "/srt/test.srt",
            "narration": "테스트",
        }

        _invalidate_caption_outputs(scene)

        assert scene["captions"] is None
        assert scene["caption_timeline"] is None
        assert scene["srt_path"] is None
        # narration은 유지
        assert scene["narration"] == "테스트"


# =============================================================================
# Test: API Status 제약 테스트
# =============================================================================


class TestStatusConstraints:
    """상태 제약 테스트 (DRAFT만 편집 가능)."""

    def test_draft_is_editable(self, sample_video_script):
        """DRAFT 상태는 편집 가능."""
        view = script_to_editor_view(sample_video_script)
        assert view.editable is True

    def test_approved_not_editable(self, approved_video_script):
        """APPROVED 상태는 편집 불가."""
        view = script_to_editor_view(approved_video_script)
        assert view.editable is False

    def test_published_not_editable(self, sample_raw_json):
        """PUBLISHED 상태는 편집 불가."""
        script = VideoScript(
            script_id="pub-script",
            video_id="video-pub",
            raw_json=sample_raw_json,
            status=ScriptStatus.PUBLISHED,
            created_by="test-user",
        )
        view = script_to_editor_view(script)
        assert view.editable is False

    def test_pending_review_not_editable(self, sample_raw_json):
        """PENDING_REVIEW 상태는 편집 불가."""
        script = VideoScript(
            script_id="pending-script",
            video_id="video-pending",
            raw_json=sample_raw_json,
            status=ScriptStatus.PENDING_REVIEW,
            created_by="test-user",
        )
        view = script_to_editor_view(script)
        assert view.editable is False


# =============================================================================
# Test: Pydantic 모델 검증
# =============================================================================


class TestPydanticModels:
    """Pydantic 모델 검증 테스트."""

    def test_scene_editor_item_creation(self):
        """SceneEditorItem 생성 테스트."""
        item = SceneEditorItem(
            scene_id=1,
            order=1,
            chapter_id=1,
            chapter_title="테스트 챕터",
            scene_title="테스트 씬",
            narration_text="나레이션 텍스트",
            subtitle_text="자막 텍스트",
            has_audio=True,
            has_captions=True,
        )

        assert item.scene_id == 1
        assert item.has_audio is True

    def test_script_editor_view_creation(self):
        """ScriptEditorView 생성 테스트."""
        view = ScriptEditorView(
            script_id="test-script",
            video_id="test-video",
            title="테스트 제목",
            language="ko",
            status="DRAFT",
            scenes=[],
            total_scenes=0,
            editable=True,
        )

        assert view.script_id == "test-script"
        assert view.editable is True

    def test_scene_update_item_optional_fields(self):
        """SceneUpdateItem 선택 필드 테스트."""
        # narration_text만 지정
        update1 = SceneUpdateItem(scene_id=1, narration_text="새 나레이션")
        assert update1.narration_text == "새 나레이션"
        assert update1.subtitle_text is None

        # subtitle_text만 지정
        update2 = SceneUpdateItem(scene_id=2, subtitle_text="새 자막")
        assert update2.narration_text is None
        assert update2.subtitle_text == "새 자막"

    def test_patch_request_requires_updates(self):
        """ScriptEditorPatchRequest는 최소 1개 업데이트 필요."""
        # 정상 요청
        request = ScriptEditorPatchRequest(
            updates=[SceneUpdateItem(scene_id=1, narration_text="테스트")]
        )
        assert len(request.updates) == 1

    def test_patch_response_fields(self):
        """ScriptEditorPatchResponse 필드 테스트."""
        response = ScriptEditorPatchResponse(
            success=True,
            updated_scene_ids=[1, 2],
            invalidated_audio_count=2,
            invalidated_caption_count=2,
            message="2개 씬 수정됨",
        )

        assert response.success is True
        assert len(response.updated_scene_ids) == 2
        assert response.invalidated_audio_count == 2


# =============================================================================
# Test: Edge Cases
# =============================================================================


class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_scene_without_audio_fields(self):
        """오디오 필드가 없는 씬 처리."""
        raw_json = {
            "chapters": [
                {
                    "chapter_id": 1,
                    "title": "테스트",
                    "scenes": [
                        {
                            "scene_id": 1,
                            "narration": "테스트 나레이션",
                            # 오디오/캡션 필드 없음
                        }
                    ],
                }
            ]
        }

        script = VideoScript(
            script_id="test",
            video_id="test-video",
            raw_json=raw_json,
            status=ScriptStatus.DRAFT,
            created_by="test-user",
        )

        view = script_to_editor_view(script)
        assert view.scenes[0].has_audio is False
        assert view.scenes[0].has_captions is False

    def test_multiple_chapters(self):
        """여러 챕터에 걸친 씬 처리."""
        raw_json = {
            "language": "ko",
            "chapters": [
                {
                    "chapter_id": 1,
                    "title": "챕터1",
                    "scenes": [
                        {"scene_id": 1, "narration": "씬1"},
                        {"scene_id": 2, "narration": "씬2"},
                    ],
                },
                {
                    "chapter_id": 2,
                    "title": "챕터2",
                    "scenes": [
                        {"scene_id": 3, "narration": "씬3"},
                    ],
                },
            ],
        }

        script = VideoScript(
            script_id="multi",
            video_id="video-multi",
            raw_json=raw_json,
            status=ScriptStatus.DRAFT,
            created_by="test-user",
        )

        view = script_to_editor_view(script)

        assert view.total_scenes == 3
        assert view.scenes[0].chapter_id == 1
        assert view.scenes[1].chapter_id == 1
        assert view.scenes[2].chapter_id == 2

    def test_empty_narration(self):
        """빈 narration 처리."""
        raw_json = {
            "chapters": [
                {
                    "chapter_id": 1,
                    "title": "테스트",
                    "scenes": [
                        {"scene_id": 1, "narration": ""},
                    ],
                }
            ]
        }

        script = VideoScript(
            script_id="empty",
            video_id="video-empty",
            raw_json=raw_json,
            status=ScriptStatus.DRAFT,
            created_by="test-user",
        )

        view = script_to_editor_view(script)
        assert view.scenes[0].narration_text == ""
