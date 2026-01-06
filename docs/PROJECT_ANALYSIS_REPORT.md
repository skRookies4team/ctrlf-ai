# CTRL+F AI Gateway 프로젝트 종합 분석 보고서

**작성일**: 2025-12-23
**분석 도구**: Claude Code

---

## 1. 프로젝트 개요

**프로젝트명**: ctrlf-ai-gateway
**버전**: 0.1.0
**언어**: Python 3.12
**프레임워크**: FastAPI

### 1.1 프로젝트 목적
CTRL+F AI Gateway는 **기업 내부 교육 및 정책 관리 플랫폼**의 AI 백엔드 서비스로, 다음 기능을 제공합니다:
- RAG 기반 채팅 (정책/교육/사고 문의)
- 교육 영상 자동 생성 파이프라인
- FAQ/퀴즈 자동 생성
- 개인화된 HR 정보 제공

### 1.2 연동 서비스
```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│ ctrlf-front │────▶│ ctrlf-ai-gateway│────▶│ ctrlf-back  │
│   (React)   │     │    (FastAPI)    │     │  (Spring)   │
└─────────────┘     └────────┬────────┘     └─────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ RAGFlow  │   │   LLM    │   │  Milvus  │
        │ (검색)   │   │  (생성)  │   │ (벡터DB) │
        └──────────┘   └──────────┘   └──────────┘
```

---

## 2. 디렉토리 구조

```
ctrlf-ai/
├── app/
│   ├── api/v1/           # API 엔드포인트 (12개 모듈)
│   ├── clients/          # 외부 서비스 클라이언트 (10개)
│   ├── core/             # 설정, 예외, 로깅, 메트릭 (5개)
│   ├── models/           # Pydantic 도메인 모델 (18개)
│   ├── repositories/     # 데이터 저장소
│   ├── services/         # 비즈니스 로직 (30+개)
│   │   └── chat/         # 채팅 파이프라인 (6개)
│   ├── utils/            # 유틸리티
│   └── main.py           # 진입점
├── tests/
│   ├── unit/             # 단위 테스트 (48개 파일)
│   └── integration/      # 통합 테스트 (4개 파일)
├── mock_backend/         # Mock Spring 서버
├── mock_llm/             # Mock LLM 서버
├── mock_ragflow/         # Mock RAGFlow 서버
├── docs/                 # 개발 문서 (42개 Phase 보고서)
└── .github/workflows/    # CI/CD
```

---

## 3. 핵심 아키텍처

### 3.1 Phase 기반 개발 (42개 Phase)

| Phase 그룹 | Phase | 주요 기능 |
|-----------|-------|---------|
| 기반 구축 | 1-9 | FastAPI 설정, Mock 서버, AI_ENV 모드 |
| 채팅 파이프라인 | 10-14 | 의도 분류, PII 마스킹, RAG 검색 |
| 콘텐츠 생성 | 15-20 | Gap 분석, 퀴즈/FAQ 생성 |
| 라우팅 고도화 | 21-23 | Intent Router, 되묻기/확인 플로우 |
| 벡터 DB | 24-29 | Milvus 통합, 문서 인덱싱 |
| 영상 파이프라인 | 30-42 | 스크립트 생성, TTS, 렌더링, 업로드 |

### 3.2 채팅 파이프라인 아키텍처

```
요청 → PII 마스킹(INPUT) → 의도 분류 → 라우팅 결정
                                          │
        ┌─────────────────────────────────┼─────────────────────────────┐
        │                                 │                             │
        ▼                                 ▼                             ▼
   RAG_INTERNAL                     BACKEND_API                    LLM_ONLY
   (RAGFlow 검색)                (개인화 데이터 조회)            (일반 대화)
        │                                 │                             │
        └─────────────────────────────────┼─────────────────────────────┘
                                          ▼
                              LLM 응답 생성 → PII 마스킹(OUTPUT)
                                          ▼
                              AI 로그 전송 → 응답 반환
```

### 3.3 영상 생성 파이프라인

```
SourceSet 시작
     ↓
문서 Ingest (RAGFlow)
     ↓
청크 추출 → Backend DB 저장
     ↓
스크립트 생성 (LLM)
     ↓
스크립트 승인 (APPROVED)
     ↓
렌더 잡 생성
     ↓
┌─────────────────────────────────────┐
│ VALIDATE → TTS → SUBTITLE →        │
│ SLIDES → COMPOSE → UPLOAD          │
└─────────────────────────────────────┘
     ↓
WebSocket 진행률 알림
     ↓
완료 콜백 → 발행
```

---

## 4. API 엔드포인트 요약

### 4.1 공개 API (12개)

| 경로 | 메서드 | 기능 |
|------|--------|------|
| `/health` | GET | Liveness 체크 |
| `/health/ready` | GET | Readiness 체크 |
| `/ai/chat/messages` | POST | AI 채팅 응답 |
| `/ai/chat/stream` | POST | 스트리밍 채팅 (NDJSON) |
| `/ai/faq/generate` | POST | FAQ 초안 생성 |
| `/ai/faq/generate/batch` | POST | 배치 FAQ 생성 |
| `/ai/gap/policy-edu/suggestions` | POST | RAG Gap 분석 |
| `/ai/quiz/generate` | POST | 퀴즈 자동 생성 |
| `/api/scripts/*` | CRUD | 스크립트 관리 |
| `/api/v2/videos/*/render-jobs/*` | CRUD | 렌더 잡 관리 |
| `WS /ws/videos/*/render-progress` | WS | 실시간 진행률 |

### 4.2 내부 API (Backend → AI)

| 경로 | 기능 |
|------|------|
| `/ai/video/job/{id}/start` | 영상 생성 시작 |
| `/ai/video/job/{id}/retry` | 영상 생성 재시도 |
| `/internal/ai/source-sets/{id}/start` | 소스셋 처리 시작 |
| `/internal/ai/source-sets/{id}/status` | 상태 조회 |

---

## 5. 핵심 서비스 분석

### 5.1 채팅 서비스 (`app/services/`)

| 서비스 | 역할 |
|--------|------|
| **ChatService** | 채팅 파이프라인 오케스트레이션 |
| **ChatStreamService** | NDJSON 스트리밍 |
| **IntentService** | 의도 분류 (규칙 기반) |
| **PiiService** | PII 마스킹 (3단계) |
| **RagService** | RAGFlow/Milvus 검색 |
| **AnswerGeneratorService** | LLM 응답 생성 |
| **RouterOrchestrator** | 라우팅 + 되묻기/확인 |

### 5.2 영상 서비스

| 서비스 | 역할 |
|--------|------|
| **VideoScriptGenerationService** | 스크립트 자동 생성 |
| **VideoRenderService** | 렌더 파이프라인 |
| **SceneAudioService** | 문장별 TTS + 캡션 |
| **ImageAssetService** | 씬 이미지 생성 |
| **StorageAdapter** | S3/로컬 저장 |
| **SourceSetOrchestrator** | 문서 처리 오케스트레이션 |

### 5.3 콘텐츠 생성 서비스

| 서비스 | 역할 |
|--------|------|
| **FaqDraftService** | FAQ 초안 생성 |
| **QuizGenerateService** | 퀴즈 자동 생성 |
| **QuizQualityService** | 퀴즈 품질 검증 (3단계) |
| **GapSuggestionService** | RAG Gap 분석 |

---

## 6. 외부 클라이언트 (`app/clients/`)

| 클라이언트 | 대상 서비스 | 주요 기능 |
|-----------|-----------|---------|
| **BackendClient** | ctrlf-back (Spring) | 로그, 콜백, 데이터 조회 |
| **RagflowClient** | RAGFlow | 문서 검색 |
| **RagflowSearchClient** | RAGFlow | /v1/chunk/search 전용 |
| **LLMClient** | vLLM/OpenAI | ChatCompletion |
| **MilvusClient** | Milvus | 벡터 검색 |
| **PersonalizationClient** | Spring | 개인화 데이터 |
| **TTSProvider** | gTTS/Polly/GCP | TTS |
| **StorageAdapter** | S3/로컬 | 파일 저장 |

### 6.1 재시도 정책

```python
RAGFLOW_RETRY_CONFIG: max_retries=1, base_delay=0.2s
LLM_RETRY_CONFIG: max_retries=1, base_delay=0.5s
BACKEND_RETRY_CONFIG: max_retries=1, base_delay=0.2s
STORAGE_UPLOAD_RETRY: max=3, exponential backoff
```

---

## 7. 도메인 모델 (`app/models/`)

### 7.1 주요 Enum

| Enum | 값 |
|------|-----|
| **Domain** | POLICY, INCIDENT, EDU |
| **IntentType** | POLICY_QA, INCIDENT_REPORT, EDUCATION_QA, ... |
| **RouteType** | RAG_INTERNAL, BACKEND_API, LLM_ONLY, ... |
| **ScriptStatus** | DRAFT → APPROVED → PUBLISHED |
| **RenderJobStatus** | QUEUED → PROCESSING → COMPLETED/FAILED |
| **RenderStep** | VALIDATE → TTS → SUBTITLE → SLIDES → COMPOSE → UPLOAD |

### 7.2 주요 모델

| 모델 | 필드 수 | 용도 |
|------|--------|------|
| **ChatRequest/Response** | 7/3 | 채팅 API |
| **AILogEntry** | 23 | 턴 단위 로그 |
| **VideoScript** | 9 | 스크립트 데이터 |
| **VideoRenderJob** | 11 | 렌더 잡 상태 |
| **RenderSpec** | 5 | 렌더링 사양 |
| **GeneratedScript** | 9 | 자동 생성 스크립트 |

---

## 8. 설정 시스템 (`app/core/config.py`)

### 8.1 환경 모드
```python
AI_ENV = "mock" | "real"
# mock: Docker Compose 내부 서비스
# real: 실제 프로덕션 서비스
```

### 8.2 주요 설정 그룹

| 그룹 | 설정 예시 |
|------|---------|
| **RAGFlow** | RAGFLOW_BASE_URL, RAGFLOW_TIMEOUT_SEC |
| **LLM** | LLM_BASE_URL, LLM_MODEL_NAME |
| **Milvus** | MILVUS_HOST, MILVUS_ENABLED |
| **TTS** | TTS_PROVIDER (gtts, polly, gcp) |
| **Storage** | STORAGE_PROVIDER (local, s3, backend_presigned) |
| **Video** | VIDEO_VISUAL_STYLE, VIDEO_FPS |
| **FAQ** | FAQ_RAG_CACHE_ENABLED, FAQ_BATCH_CONCURRENCY |
| **Router** | ROUTER_USE_LLM, ROUTER_RULE_CONFIDENCE_THRESHOLD |

---

## 9. 테스트 현황

### 9.1 테스트 구조
```
tests/
├── conftest.py              # 공용 픽스처
├── unit/                    # 48개 테스트 파일
│   ├── conftest.py          # 단위 테스트 픽스처
│   ├── test_chat_api.py     # 채팅 API 테스트
│   ├── test_phase10_*.py    # Phase별 테스트
│   └── ...
└── integration/             # 4개 테스트 파일
    ├── conftest.py          # 통합 테스트 픽스처
    ├── test_e2e_smoke.py    # E2E 스모크 테스트
    └── test_docker_e2e.py   # Docker Compose 테스트
```

### 9.2 테스트 패턴
- **AsyncMock/MagicMock**: 외부 서비스 모킹
- **httpx.MockTransport**: HTTP 클라이언트 모킹
- **pytest-asyncio**: 비동기 테스트 지원
- **픽스처 기반 설정 주입**

---

## 10. CI/CD

### 10.1 배포 파이프라인
```yaml
# .github/workflows/ai-deploy.yml
on: push to main
runs-on: self-hosted

steps:
  1. git fetch && git reset --hard origin/main
  2. source venv && pip install -r requirements.txt
  3. fuser -k 8080/tcp (기존 프로세스 종료)
  4. nohup uvicorn app.main:app --port 8000
```

---

## 11. 프로젝트 통계

| 항목 | 수량 |
|------|------|
| Python 파일 | 100+ |
| API 엔드포인트 | 20+ |
| 서비스 클래스 | 30+ |
| Pydantic 모델 | 100+ |
| Enum 정의 | 30+ |
| 테스트 파일 | 52+ |
| 개발 문서 | 60+ |
| 개발 Phase | 42 |

---

## 12. 핵심 설계 원칙

### 12.1 아키텍처 원칙
1. **Layer 분리**: API → Service → Client → External
2. **싱글톤 패턴**: 클라이언트 인스턴스 재사용
3. **의존성 주입**: FastAPI Depends 활용
4. **비동기 우선**: async/await 전면 사용

### 12.2 안정성 원칙
1. **재시도 정책**: 지수 백오프
2. **타임아웃 명시**: 모든 외부 호출
3. **에러 래핑**: UpstreamServiceError
4. **Fallback 메시지**: 서비스 장애 시

### 12.3 확장성 원칙
1. **Phase 기반 개발**: 점진적 기능 추가
2. **하위 호환성**: Optional 필드, alias
3. **Provider 패턴**: TTS, Storage 어댑터
4. **상태 머신**: 복잡한 워크플로우 관리

---

## 13. 주요 API 흐름 예시

### 13.1 채팅 요청 흐름
```
POST /ai/chat/messages
{
  "session_id": "sess-001",
  "user_id": "emp-001",
  "domain": "POLICY",
  "messages": [{"role": "user", "content": "연차 사용 방법은?"}]
}
                    ↓
             PII 마스킹 (INPUT)
                    ↓
             의도 분류 → POLICY_QA
                    ↓
             라우팅 → RAG_INTERNAL
                    ↓
             RAGFlow 검색 (정책 문서)
                    ↓
             LLM 응답 생성
                    ↓
             PII 마스킹 (OUTPUT)
                    ↓
             AI 로그 전송 (Backend)
                    ↓
{
  "answer": "연차 사용은 최소 3일 전에 신청해야 합니다...",
  "sources": [{"doc_id": "policy-001", "title": "근태관리규정", "score": 0.92}],
  "meta": {"route": "RAG_INTERNAL", "latency_ms": 1234}
}
```

### 13.2 영상 생성 흐름
```
POST /internal/ai/source-sets/{id}/start  (Backend → AI)
                    ↓
             문서 목록 조회 (Backend)
                    ↓
             RAGFlow Ingest
                    ↓
             청크 추출 → Backend DB 저장
                    ↓
             스크립트 생성 (LLM)
                    ↓
             완료 콜백 → SCRIPT_READY
                    ↓
             [관리자] 스크립트 승인 → APPROVED
                    ↓
POST /api/v2/videos/{id}/render-jobs  (FE → AI)
                    ↓
POST /ai/video/job/{id}/start  (Backend → AI)
                    ↓
             render-spec 스냅샷 저장
                    ↓
             ┌─ TTS 생성 ─┐
             │  자막 생성  │ → WebSocket 진행률
             │  슬라이드   │
             │  영상 합성  │
             └─ 업로드 ──┘
                    ↓
             완료 콜백 → Backend DB 업데이트
                    ↓
GET /api/v2/videos/{id}/assets/published  (FE → AI)
                    ↓
{
  "video_url": "https://cdn.../video.mp4",
  "subtitle_url": "https://cdn.../subtitle.vtt",
  "duration_sec": 180
}
```

---

## 14. 결론

**ctrlf-ai-gateway**는 다음과 같은 특징을 가진 **잘 설계된 AI Gateway 서비스**입니다:

1. **완성도 높은 아키텍처**: 42개 Phase를 통한 점진적 개발
2. **풍부한 기능**: 채팅, 영상 생성, FAQ/퀴즈, 개인화
3. **안정성**: 재시도, 타임아웃, 에러 처리 체계
4. **확장성**: Provider 패턴, 상태 머신, 모듈화
5. **테스트**: 단위/통합 테스트, Mock 서버
6. **문서화**: 60+ 개발 문서

프로젝트는 **기업 교육 플랫폼의 AI 백엔드**로서 필요한 모든 핵심 기능을 갖추고 있습니다.

---

## 부록: 파일 목록

### A. API 엔드포인트 파일
- `app/api/v1/health.py`
- `app/api/v1/chat.py`
- `app/api/v1/chat_stream.py`
- `app/api/v1/faq.py`
- `app/api/v1/gap_suggestions.py`
- `app/api/v1/quiz_generate.py`
- `app/api/v1/scripts.py`
- `app/api/v1/render_jobs.py`
- `app/api/v1/ws_render_progress.py`
- `app/api/v1/source_sets.py`
- `app/api/v1/internal_rag.py`

### B. 클라이언트 파일
- `app/clients/http_client.py`
- `app/clients/backend_client.py`
- `app/clients/ragflow_client.py`
- `app/clients/ragflow_search_client.py`
- `app/clients/llm_client.py`
- `app/clients/milvus_client.py`
- `app/clients/personalization_client.py`
- `app/clients/tts_provider.py`
- `app/clients/storage_adapter.py`

### C. 서비스 파일
- `app/services/chat_service.py`
- `app/services/chat_stream_service.py`
- `app/services/intent_service.py`
- `app/services/pii_service.py`
- `app/services/rag_service.py`
- `app/services/answer_generator.py`
- `app/services/router_orchestrator.py`
- `app/services/faq_service.py`
- `app/services/quiz_generate_service.py`
- `app/services/gap_suggestion_service.py`
- `app/services/video_render_service.py`
- `app/services/video_script_generation_service.py`
- `app/services/source_set_orchestrator.py`
- `app/services/scene_audio_service.py`
- `app/services/image_asset_service.py`

### D. 모델 파일
- `app/models/chat.py`
- `app/models/chat_stream.py`
- `app/models/ai_log.py`
- `app/models/intent.py`
- `app/models/rag.py`
- `app/models/faq.py`
- `app/models/quiz_generate.py`
- `app/models/quiz_qc.py`
- `app/models/gap_suggestion.py`
- `app/models/video_render.py`
- `app/models/render_spec.py`
- `app/models/source_set.py`
- `app/models/personalization.py`
- `app/models/router_types.py`
- `app/models/script_editor.py`
- `app/models/video_progress.py`
- `app/models/internal_rag.py`

### E. Core 파일
- `app/core/config.py`
- `app/core/exceptions.py`
- `app/core/logging.py`
- `app/core/metrics.py`
- `app/core/retry.py`
