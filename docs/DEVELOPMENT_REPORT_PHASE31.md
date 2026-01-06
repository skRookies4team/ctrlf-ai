# Phase 31: 교육 원문 → 영상 스크립트 자동 생성 (LLM)

**작성일**: 2025-12-19
**Phase**: 31
**상태**: 완료
**테스트 결과**: 24 passed

---

## 1. 개요

Phase 31에서는 교육 원문 텍스트를 입력받아 LLM을 통해 VideoScript JSON을 자동 생성하는 기능을 구현했습니다.

### 1.1 목표

- **A) 스크립트 자동 생성**: 교육 원문 → VideoScript JSON 변환 (LLM 호출)
- **B) JSON 파싱 + 검증**: Pydantic 스키마로 검증, 실패 시 자동 재시도
- **C) 기존 파이프라인 호환**: Phase 27/28과 100% 호환 (렌더러, KB 인덱서)
- **D) 하위호환 유지**: 기존 /api/scripts 수동 경로 그대로 동작

---

## 2. 구현 상세

### 2.1 새 API 엔드포인트

#### POST /api/videos/{video_id}/scripts/generate

교육 원문을 LLM으로 분석하여 VideoScript JSON을 자동 생성합니다.

**파일**: `app/api/v1/video_render.py:229-326`

```python
@router.post(
    "/videos/{video_id}/scripts/generate",
    response_model=ScriptGenerateResponse,
)
async def generate_script(
    video_id: str,
    request: ScriptGenerateRequest,
    user_id: str = "anonymous",
    service=Depends(get_render_service),
):
    """교육 원문에서 스크립트 자동 생성 (Phase 31)."""
```

**Request 예시:**
```json
{
  "source_text": "교육 원문 텍스트 ...",
  "language": "ko",
  "target_minutes": 3,
  "max_chapters": 5,
  "max_scenes_per_chapter": 6,
  "style": "friendly_security_training"
}
```

**Response 예시:**
```json
{
  "script_id": "script-abc123def456",
  "video_id": "video-001",
  "status": "DRAFT",
  "raw_json": {
    "chapters": [
      {
        "chapter_id": 1,
        "title": "보안교육 개요",
        "scenes": [
          {
            "scene_id": 1,
            "narration": "안녕하세요. 보안교육을 시작하겠습니다.",
            "on_screen_text": "보안교육 시작",
            "duration_sec": 30.0
          }
        ]
      }
    ]
  }
}
```

### 2.2 VideoScript JSON 스키마

**파일**: `app/services/video_script_generation_service.py:44-67`

```python
class SceneSchema(BaseModel):
    scene_id: int          # 필수
    narration: str         # 필수
    on_screen_text: str    # 선택
    duration_sec: float    # 선택

class ChapterSchema(BaseModel):
    chapter_id: int        # 필수
    title: str             # 필수
    scenes: List[SceneSchema]  # 필수 (1개 이상)

class VideoScriptSchema(BaseModel):
    chapters: List[ChapterSchema]  # 필수 (1개 이상)
```

### 2.3 LLM 호출 + JSON 복구 로직

**파일**: `app/services/video_script_generation_service.py:100-160`

```python
async def generate_script(self, video_id, source_text, options):
    for attempt in range(self.MAX_RETRIES):  # 최대 2회
        if attempt == 0:
            # 1차: 스키마 + 예시 포함해서 생성 요청
            prompt = self._build_generation_prompt(source_text, opts)
        else:
            # 재시도: fix 프롬프트 ("오직 JSON만 다시 출력")
            prompt = self._build_fix_prompt(raw_output, str(last_error))

        raw_output = await self._llm_client.generate_chat_completion(...)
        parsed_json = self._extract_json(raw_output)
        validated = VideoScriptSchema.model_validate(parsed_json)
        return validated.to_raw_json()
```

**JSON 추출 방법:**
1. 코드블록 내 JSON (`\`\`\`json ... \`\`\``)
2. { } 괄호로 둘러싸인 부분
3. 전체를 JSON으로 시도

### 2.4 요청/응답 모델

**파일**: `app/models/video_render.py:377-399`

```python
class ScriptGenerateRequest(BaseModel):
    source_text: str           # 필수, 최소 10자
    language: str = "ko"
    target_minutes: float = 3  # 1-30분
    max_chapters: int = 5      # 1-10개
    max_scenes_per_chapter: int = 6  # 1-15개
    style: str = "friendly_security_training"

class ScriptGenerateResponse(BaseModel):
    script_id: str
    video_id: str
    status: str  # "DRAFT"
    raw_json: Dict[str, Any]
```

---

## 3. 정책

### 3.1 EXPIRED 교육 차단

- video_id 기준으로 EXPIRED 교육이면 404 반환
- `reason_code: "EDU_EXPIRED"`

### 3.2 생성 실패 처리

- JSON 파싱/스키마 검증 실패 시 자동 재시도 (최대 2회)
- 모든 시도 실패 시 422 반환
- `reason_code: "SCRIPT_GENERATION_FAILED"`

### 3.3 하위호환

- 기존 `POST /api/scripts` (수동 raw_json 입력) 경로 그대로 동작
- 기존 approve/render/publish 플로우 영향 없음

---

## 4. 사용 예시 (curl)

### 4.1 스크립트 자동 생성

```bash
curl -X POST http://localhost:8000/api/videos/video-001/scripts/generate \
  -H "Content-Type: application/json" \
  -d '{
    "source_text": "피싱 메일은 악의적인 공격자가 보내는 가짜 이메일입니다. 의심스러운 링크를 클릭하지 마세요. 발신자 주소를 확인하고, 긴급함을 강조하는 메일은 주의하세요.",
    "language": "ko",
    "target_minutes": 3,
    "max_chapters": 3,
    "style": "friendly_security_training"
  }'
```

**응답:**
```json
{
  "script_id": "script-abc123def456",
  "video_id": "video-001",
  "status": "DRAFT",
  "raw_json": {
    "chapters": [
      {
        "chapter_id": 1,
        "title": "피싱 메일이란?",
        "scenes": [
          {
            "scene_id": 1,
            "narration": "안녕하세요. 오늘은 피싱 메일에 대해 알아보겠습니다.",
            "on_screen_text": "피싱 메일 교육",
            "duration_sec": 20.0
          }
        ]
      }
    ]
  }
}
```

### 4.2 생성된 스크립트 승인

```bash
curl -X POST http://localhost:8000/api/scripts/script-abc123def456/approve
```

### 4.3 렌더 잡 생성

```bash
curl -X POST http://localhost:8000/api/videos/video-001/render-jobs \
  -H "Content-Type: application/json" \
  -d '{"script_id": "script-abc123def456"}'
```

---

## 5. 테스트 케이스

**파일**: `tests/test_phase31_script_generation.py`

| 테스트 클래스 | 테스트 수 | 설명 |
|-------------|----------|------|
| `TestVideoScriptGenerationService` | 5 | 서비스 단위 테스트 |
| `TestJsonExtraction` | 5 | JSON 추출 로직 테스트 |
| `TestVideoScriptSchema` | 5 | Pydantic 스키마 검증 테스트 |
| `TestScriptGenerateAPI` | 3 | API 엔드포인트 테스트 |
| `TestManualScriptAPI` | 2 | 기존 API 회귀 테스트 |
| `TestE2EScriptGenerationFlow` | 1 | E2E 통합 테스트 |
| `TestScriptGenerationOptions` | 3 | 생성 옵션 테스트 |

```bash
# 테스트 실행
python -m pytest tests/test_phase31_script_generation.py -v

# 결과
24 passed in 14.45s
```

### 5.1 핵심 테스트 케이스

#### Happy Path: 생성 → 승인 → 렌더 잡

```python
async def test_full_flow_generate_approve_render(self):
    # Step 1: 스크립트 생성
    gen_response = client.post(
        "/api/videos/video-e2e/scripts/generate",
        json={"source_text": "보안교육 원문 텍스트입니다."},
    )
    assert gen_response.json()["status"] == "DRAFT"

    # Step 2: 스크립트 승인
    approve_response = client.post(f"/api/scripts/{script_id}/approve")
    assert approve_response.json()["status"] == "APPROVED"

    # Step 3: 렌더 잡 생성
    render_response = client.post(
        "/api/videos/video-e2e/render-jobs",
        json={"script_id": script_id},
    )
    assert render_response.json()["status"] == "PENDING"
```

#### Invalid JSON 복구

```python
async def test_generate_script_retry_on_invalid_json(self):
    # 1차: 잘못된 JSON, 2차: 유효한 JSON
    mock_client.generate_chat_completion = AsyncMock(
        side_effect=[
            "이것은 JSON이 아닙니다. {broken}",
            json.dumps(valid_script_json),
        ]
    )
    result = await service.generate_script(...)
    assert mock_client.generate_chat_completion.call_count == 2
```

---

## 6. 변경된 파일

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `app/services/video_script_generation_service.py` | 신규 | 스크립트 자동 생성 서비스 |
| `app/models/video_render.py` | 수정 | ScriptGenerateRequest/Response 추가 |
| `app/api/v1/video_render.py` | 수정 | POST /api/videos/{video_id}/scripts/generate 엔드포인트 추가 |
| `tests/test_phase31_script_generation.py` | 신규 | Phase 31 테스트 24개 |
| `docs/DEVELOPMENT_REPORT_PHASE31.md` | 신규 | Phase 31 개발 리포트 |

---

## 7. 스타일 옵션

현재 지원하는 스크립트 스타일:

| style | 설명 |
|-------|------|
| `friendly_security_training` | 친근하고 이해하기 쉬운 보안 교육 스타일 (기본값) |
| `formal_compliance` | 공식적이고 정확한 컴플라이언스 교육 스타일 |
| `engaging_awareness` | 흥미롭고 참여를 유도하는 인식 제고 스타일 |

---

## 8. 향후 계획

1. **스트리밍 생성**: 긴 스크립트의 경우 청크 단위로 스트리밍 반환
2. **다국어 지원 강화**: 영어, 일본어 등 다국어 프롬프트 최적화
3. **템플릿 시스템**: 재사용 가능한 스크립트 템플릿 관리
4. **A/B 테스트**: 다양한 프롬프트 버전 비교 테스트

---

**작성자**: Claude Code
**검토자**: -
