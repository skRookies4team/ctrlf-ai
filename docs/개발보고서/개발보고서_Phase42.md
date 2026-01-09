# Phase 42: Script Editor API 개발 보고서

## 개요

영상 스크립트(VideoScript)의 개별 씬(scene)을 편집할 수 있는 API를 구현했습니다. 프론트엔드/백엔드가 raw_json을 직접 다루지 않고, 씬 중심의 편집 인터페이스를 사용할 수 있습니다.

## 구현 내용

### 1. 새로운 파일

#### app/models/script_editor.py

편집용 DTO 모델과 변환 함수:

```python
# DTO 모델
- SceneEditorItem: 씬 편집용 항목 (scene_id, order, chapter_id, narration_text, subtitle_text, has_audio, has_captions)
- ScriptEditorView: 편집용 스크립트 뷰 (GET 응답)
- SceneUpdateItem: 씬 업데이트 항목 (scene_id, narration_text, subtitle_text)
- ScriptEditorPatchRequest: PATCH 요청 (updates 배열)
- ScriptEditorPatchResponse: PATCH 응답 (updated_scene_ids, invalidated_audio_count, invalidated_caption_count)

# 변환/무효화 함수
- script_to_editor_view(): VideoScript → ScriptEditorView 변환
- apply_scene_updates(): 씬 업데이트 적용 + 산출물 무효화
- _invalidate_audio_outputs(): 오디오 필드 무효화
- _invalidate_caption_outputs(): 캡션 필드 무효화
```

#### app/api/v1/script_editor.py

API 엔드포인트:

```
GET  /api/scripts/{script_id}/editor  # 편집용 뷰 조회
PATCH /api/scripts/{script_id}/editor  # 씬 부분 수정
```

### 2. API 상세

#### GET /api/scripts/{script_id}/editor

**응답** (ScriptEditorView):
```json
{
  "script_id": "script-001",
  "video_id": "video-001",
  "title": "소개",
  "language": "ko",
  "status": "DRAFT",
  "scenes": [
    {
      "scene_id": 1,
      "order": 1,
      "chapter_id": 1,
      "chapter_title": "소개",
      "scene_title": "첫 씬",
      "narration_text": "안녕하세요",
      "subtitle_text": "안녕하세요",
      "has_audio": true,
      "has_captions": true
    }
  ],
  "total_scenes": 3,
  "editable": true
}
```

**에러**:
- 404: `SCRIPT_NOT_FOUND`

#### PATCH /api/scripts/{script_id}/editor

**요청** (ScriptEditorPatchRequest):
```json
{
  "updates": [
    {
      "scene_id": 1,
      "narration_text": "새로운 나레이션",
      "subtitle_text": "새로운 자막"
    }
  ]
}
```

**응답** (ScriptEditorPatchResponse):
```json
{
  "success": true,
  "updated_scene_ids": [1],
  "invalidated_audio_count": 1,
  "invalidated_caption_count": 1,
  "message": "1개 씬이 수정되었습니다. 다음 렌더링 시 오디오/캡션이 재생성됩니다."
}
```

**에러**:
- 404: `SCRIPT_NOT_FOUND`
- 409: `NOT_EDITABLE` (DRAFT 상태가 아닌 경우)

### 3. 무효화 규칙

| 변경 유형 | 오디오 무효화 | 캡션 무효화 |
|----------|-------------|------------|
| narration_text 변경 | O | O |
| subtitle_text만 변경 | X | O |
| 둘 다 변경 | O | O |

**무효화되는 필드**:

오디오 필드:
- `tts_audio_path`
- `audio_path`
- `audio_duration_sec`
- `duration_sec`

캡션 필드:
- `captions`
- `caption_timeline`
- `srt_path`

### 4. 상태 제약

- **DRAFT**: 편집 가능 (`editable: true`)
- **PENDING_REVIEW**: 편집 불가
- **APPROVED**: 편집 불가
- **REJECTED**: 편집 불가
- **PUBLISHED**: 편집 불가

DRAFT가 아닌 상태에서 PATCH 요청 시 409 Conflict 에러 반환.

### 5. 라우터 등록

`app/main.py`에 Phase 42 라우터 등록:

```python
# Phase 42: Script Editor API (스크립트 편집)
# - GET /api/scripts/{script_id}/editor: 편집용 뷰 조회
# - PATCH /api/scripts/{script_id}/editor: 씬 부분 수정
app.include_router(script_editor.router, tags=["Script Editor"])
```

## 테스트 결과

```
tests/test_phase42_script_editor.py - 29 passed

TestScriptToEditorView (9 tests):
- test_basic_conversion
- test_scene_order_is_sequential
- test_scene_details_extraction
- test_has_audio_detection
- test_has_captions_detection
- test_title_from_first_chapter
- test_approved_status_not_editable
- test_empty_chapters
- test_subtitle_fallback_to_on_screen_text

TestApplySceneUpdates (6 tests):
- test_narration_change_invalidates_audio_and_captions
- test_subtitle_only_change_invalidates_captions_only
- test_both_narration_and_subtitle_change
- test_multiple_scenes_update
- test_no_change_if_same_value
- test_nonexistent_scene_ignored

TestInvalidationHelpers (2 tests):
- test_invalidate_audio_outputs
- test_invalidate_caption_outputs

TestStatusConstraints (4 tests):
- test_draft_is_editable
- test_approved_not_editable
- test_published_not_editable
- test_pending_review_not_editable

TestPydanticModels (5 tests):
- test_scene_editor_item_creation
- test_script_editor_view_creation
- test_scene_update_item_optional_fields
- test_patch_request_requires_updates
- test_patch_response_fields

TestEdgeCases (3 tests):
- test_scene_without_audio_fields
- test_multiple_chapters
- test_empty_narration
```

## 파일 변경 요약

| 파일 | 변경 |
|------|------|
| `app/models/script_editor.py` | 신규 생성 (DTO 모델 + 변환 함수) |
| `app/api/v1/script_editor.py` | 신규 생성 (GET/PATCH 엔드포인트) |
| `app/main.py` | script_editor 라우터 등록 추가 |
| `tests/test_phase42_script_editor.py` | 신규 생성 (29개 테스트) |

## 사용 예시

### 프론트엔드 편집 플로우

```javascript
// 1. 편집용 뷰 조회
const response = await fetch(`/api/scripts/${scriptId}/editor`);
const editorView = await response.json();

// editable 체크
if (!editorView.editable) {
  alert("DRAFT 상태의 스크립트만 편집할 수 있습니다.");
  return;
}

// 2. 씬 편집 UI 렌더링
editorView.scenes.forEach(scene => {
  renderSceneEditor(scene);
});

// 3. 씬 수정 요청
const patchResponse = await fetch(`/api/scripts/${scriptId}/editor`, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    updates: [
      { scene_id: 1, narration_text: "새로운 나레이션" },
      { scene_id: 2, subtitle_text: "새로운 자막" }
    ]
  })
});

const result = await patchResponse.json();
console.log(`수정된 씬: ${result.updated_scene_ids}`);
console.log(`오디오 재생성 필요: ${result.invalidated_audio_count}개`);
console.log(`캡션 재생성 필요: ${result.invalidated_caption_count}개`);

// 4. 다음 렌더 잡 실행 시 자동으로 무효화된 산출물 재생성됨
```

## 향후 고려사항

1. **실시간 미리보기**: 편집 중 TTS 미리보기 기능
2. **히스토리 관리**: 편집 이력 추적 (undo/redo)
3. **동시 편집 방지**: 낙관적 락 또는 WebSocket 기반 실시간 동기화
4. **벌크 편집**: 여러 스크립트 동시 편집 지원
