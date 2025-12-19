# CTRL+F AI Gateway 리팩토링 인벤토리

## 1. 핵심 기능 플로우

### 1.1 RAG 채팅 플로우 (핵심)

```
POST /ai/chat/messages
  → ChatService.handle_chat()
    → PiiService.mask() (입력 마스킹)
    → RouterOrchestrator.route() (선택적)
      → RuleRouter → LLMRouter → Clarify/Confirm
    → IntentService.classify()
    → 라우팅 결과에 따라:
      ├─ RAG_INTERNAL: MilvusClient/RagflowClient → LLMClient
      ├─ BACKEND_API: BackendDataClient → LLMClient
      └─ MIXED: 둘 다 호출
    → AnswerGuardService.guard() (품질 검증)
    → PiiService.mask() (출력 마스킹)
    → AILogService.send_log_async() (로그 전송)
    → ChatResponse
```

**파일 경로:**
- API: `app/api/v1/chat.py`
- Service: `app/services/chat_service.py`
- Router: `app/services/router_orchestrator.py`, `app/services/rule_router.py`, `app/services/llm_router.py`
- Intent: `app/services/intent_service.py`
- RAG: `app/clients/ragflow_client.py`, `app/clients/milvus_client.py`
- LLM: `app/clients/llm_client.py`
- Guard: `app/services/answer_guard_service.py`
- PII: `app/services/pii_service.py`

### 1.2 영상 생성 플로우

```
POST /api/scripts (스크립트 생성)
  → VideoRenderService.create_script()
    → VideoScript(raw_json) 저장

POST /api/scripts/{script_id}/approve (승인)
  → VideoRenderService.approve_script()
    → status = APPROVED

POST /api/videos/{video_id}/render-jobs (렌더 잡 생성)
  → VideoRenderService.create_render_job()
    → RenderJobEntity(PENDING) 생성

POST /api/render-jobs/{job_id}/start (잡 실행)
  → RenderJobRunner.start_job()
    → BackendScriptClient.get_render_spec() (스냅샷)
    → MVPVideoRenderer.render_video()
      → SceneAudioService (TTS + 자막)
      → StorageAdapter (에셋 업로드)
    → RenderJobEntity(SUCCEEDED/FAILED)

POST /api/videos/{video_id}/publish (발행)
  → VideoRenderService → KBIndexService
    → MilvusClient.upsert() (KB 적재)
    → status = PUBLISHED
```

**파일 경로:**
- API: `app/api/v1/video_render.py`
- Service: `app/services/video_render_service.py`
- Runner: `app/services/render_job_runner.py`
- Renderer: `app/services/video_renderer_mvp.py` (활성), `app/services/video_renderer_real.py` (비활성)
- Audio: `app/services/scene_audio_service.py`
- Storage: `app/clients/storage_adapter.py`
- KB: `app/services/kb_index_service.py`

---

## 2. 사용 여부 분석

### 2.1 활성 (사용 중)

| 모듈 | 용도 | 호출 위치 |
|------|------|-----------|
| `chat_service.py` | 채팅 핵심 | chat.py, chat_stream.py |
| `router_orchestrator.py` | 2차 라우팅 | chat_service.py (플래그 활성 시) |
| `intent_service.py` | 의도 분류 | chat_service.py |
| `pii_service.py` | PII 마스킹 | chat_service.py |
| `answer_guard_service.py` | 답변 품질 | chat_service.py |
| `video_render_service.py` | 영상 생성 | video_render.py |
| `video_renderer_mvp.py` | MVP 렌더러 | video_render.py, video_render_phase33.py |
| `scene_audio_service.py` | TTS/자막 | video_renderer_mvp.py |
| `storage_adapter.py` | 파일 저장 | video_renderer_mvp.py |
| `milvus_client.py` | 벡터 검색 | chat_service.py, kb_index_service.py |
| `llm_client.py` | LLM 호출 | chat_service.py, answer_generator.py |

### 2.2 비활성 (테스트만 존재)

| 모듈 | 이유 | 호출 위치 |
|------|------|-----------|
| `video_renderer_real.py` | get_real_video_renderer() 미호출 | 테스트에서만 사용 |
| `video_composer.py` | video_renderer_real.py에서만 import | 프로덕션 미사용 |
| `visual_plan.py` | video_renderer_real.py에서만 import | 프로덕션 미사용 |

### 2.3 레거시/중복 가능성

| 모듈 | 상태 | 설명 |
|------|------|------|
| `video_render_phase33.py` | 중복 | video_render.py와 유사, /api/v2 prefix |
| `video_catalog_service.py` | 미사용 | 자기 파일에서만 참조 |
| `quiz_qc.py` 모델 | 골격만 | Phase 17 미완성 |

---

## 3. 환경변수/설정 플래그

### 3.1 라우팅 관련

```env
ROUTER_ORCHESTRATOR_ENABLED=false  # RouterOrchestrator 활성화
ROUTER_USE_LLM=false               # LLM 기반 2차 라우팅
```

### 3.2 검색 관련

```env
MILVUS_ENABLED=true                # Milvus 직접 검색 (false면 RAGFlow)
RAGFLOW_BASE_URL=http://localhost:9380
RAGFLOW_API_KEY=xxx
```

### 3.3 영상 생성 관련

```env
TTS_PROVIDER=gtts                  # mock | gtts | polly | gcp
STORAGE_PROVIDER=local             # local | s3
VIDEO_VISUAL_STYLE=basic           # basic | animated
RENDER_OUTPUT_DIR=./video_output
```

---

## 4. API 엔드포인트 목록

### 4.1 핵심 (필수)

| 엔드포인트 | 설명 | 사용 |
|-----------|------|------|
| `POST /ai/chat/messages` | AI 채팅 | O |
| `POST /ai/chat/stream` | 스트리밍 채팅 | O |
| `POST /api/scripts` | 스크립트 생성 | O |
| `POST /api/scripts/{id}/approve` | 스크립트 승인 | O |
| `POST /api/videos/{id}/render-jobs` | 렌더 잡 생성 | O |
| `POST /api/render-jobs/{id}/start` | 잡 실행 | O |
| `GET /api/render-jobs/{id}` | 잡 상태 조회 | O |
| `POST /internal/rag/index` | 문서 인덱싱 | O |
| `POST /internal/rag/delete` | 문서 삭제 | O |
| `GET /health` | 헬스체크 | O |

### 4.2 부가 (선택적)

| 엔드포인트 | 설명 | 사용 |
|-----------|------|------|
| `POST /ai/faq/generate` | FAQ 생성 | ? |
| `POST /ai/quiz/generate` | 퀴즈 생성 | ? |
| `POST /ai/gap/policy-edu/suggestions` | Gap 제안 | ? |
| `POST /search` | RAG 검색 | ? |
| `POST /ingest` | 문서 인덱싱 (레거시) | ? |

### 4.3 영상 진행 (Phase 22)

| 엔드포인트 | 설명 | 사용 |
|-----------|------|------|
| `POST /api/video/play/start` | 재생 시작 | ? |
| `POST /api/video/progress` | 진행률 | ? |
| `POST /api/video/complete` | 완료 | ? |
| `GET /api/video/status` | 상태 | ? |
| `GET /api/video/quiz/check` | 퀴즈 가능 여부 | ? |

### 4.4 Phase 33 (V2 - 중복)

| 엔드포인트 | 설명 | 사용 |
|-----------|------|------|
| `POST /api/v2/videos/{id}/render-jobs` | 렌더 잡 (V2) | 중복 |
| `GET /api/v2/videos/{id}/render-jobs` | 잡 목록 (V2) | 중복 |

---

## 5. 테스트 현황

### 5.1 테스트 파일 분류

**핵심 기능 테스트:**
- `test_chat_api.py` - 채팅 API
- `test_internal_rag.py` - Internal RAG
- `test_phase27_video_render.py` - 영상 렌더링
- `test_phase42_script_editor.py` - 스크립트 편집

**Phase별 테스트:**
- `test_phase10_*` ~ `test_phase42_*` - 각 Phase 테스트

**통합 테스트:**
- `tests/integration/test_real_smoke.py` - E2E 스모크

---

## 6. 리팩토링 후보

### 6.1 제거 후보 (Dead Code)

1. **video_catalog_service.py** - 어디서도 import 안됨
2. **일부 테스트 파일** - 미사용 기능 테스트

### 6.2 통합 후보 (중복)

1. **video_render.py + video_render_phase33.py** - V1/V2 통합 검토
2. **ragflow_client.py + ragflow_search_client.py** - 검색 클라이언트 통합

### 6.3 정리 후보 (비활성)

1. **video_renderer_real.py, video_composer.py, visual_plan.py** - 활성화 계획 없으면 정리

---

## 7. 의존성 검토

### requirements.txt

```
fastapi>=0.115.0      # 필수
uvicorn[standard]     # 필수
pydantic>=2.9.0       # 필수
httpx>=0.27.0         # 필수
python-dotenv         # 필수
pytest                # 테스트
PyMuPDF               # 문서 처리
python-docx           # 문서 처리
gTTS                  # TTS
Pillow                # 이미지
```

모든 의존성이 사용됨. 불필요한 의존성 없음.

---

## 8. 테스트 호환성 업데이트 (2025-12-19)

### 8.1 Phase 39 AnswerGuardService 호환

**변경 내용:**
- Phase 39에서 `AnswerGuardService`가 도입되어 RAG 결과 없을 시 LLM 호출 전 차단
- 기존 테스트들이 영문 fallback 메시지를 기대했으나, 새 한국어 템플릿 반환

**수정된 테스트 파일:**
| 파일 | 수정 내용 |
|------|----------|
| `test_service_fallback.py` | 한국어 템플릿 "승인/인덱싱된" 패턴 허용 |
| `test_phase12_hardening.py` | `NO_RAG_EVIDENCE` 에러 타입 허용 |
| `test_chat_http_e2e.py` | `FakeMilvusClient` 추가 (프로덕션 동일) |

### 8.2 Milvus 테스트 정렬

**변경 내용:**
- Phase 24에서 `MILVUS_ENABLED=true`가 기본값으로 변경됨
- E2E 테스트가 RagflowClient 대신 MilvusSearchClient 사용하도록 수정

**추가된 테스트 헬퍼:**
```python
class FakeMilvusClient(MilvusSearchClient):
    """E2E 테스트용 Milvus 목 클라이언트"""
    def __init__(self, sources=None, should_fail=False):
        self._fake_sources = sources or []
        self._should_fail = should_fail
```

---

## 9. 다음 단계

1. **video_catalog_service.py 제거** - 미사용 확인 후 삭제
2. **API 경로 통일** - /api/v1 vs /api/v2 정리
3. **설정 플래그 정리** - 미사용 플래그 제거
4. **E2E 테스트 보강** - 핵심 플로우 검증
