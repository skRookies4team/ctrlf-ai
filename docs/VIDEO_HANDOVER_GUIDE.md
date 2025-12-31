# 영상 제작 파이프라인 인수인계 문서

> 작성일: 2025-12-23
> 대상: AI 팀원
> 목적: 영상 제작 관련 코드 및 API 인수인계

---

## 목차

1. [개요](#1-개요)
2. [파일 구조 및 역할](#2-파일-구조-및-역할)
3. [각 파일 상세 설명](#3-각-파일-상세-설명)
4. [변경 불가 API 명세](#4-변경-불가-api-명세)
5. [데이터 모델](#5-데이터-모델)
6. [외부 서비스 연동](#6-외부-서비스-연동)
7. [환경 설정](#7-환경-설정)
8. [개발 시 주의사항](#8-개발-시-주의사항)
9. [테스트 방법](#9-테스트-방법)

---

## 1. 개요

### 1.1 시스템 구성

영상 제작 파이프라인은 **2-Phase** 구조입니다:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CTRLF AI 영상 제작 파이프라인                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   Phase 1: 스크립트 생성              Phase 2: 영상 렌더링               │
│   ┌──────────────────────┐           ┌────────────────────────────┐    │
│   │ VideoScriptGeneration │    →     │    RenderJobRunner         │    │
│   │ Service               │           │    (7-Step Pipeline)       │    │
│   └──────────────────────┘           └────────────────────────────┘    │
│            │                                    │                       │
│            ▼                                    ▼                       │
│      LLM (Qwen)                          TTS → FFmpeg → Storage        │
│            │                                    │                       │
│            ▼                                    ▼                       │
│   VideoScript JSON                    MP4 + SRT + Thumbnail            │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 핵심 개념

| 개념 | 설명 |
|------|------|
| **VideoScript** | LLM이 생성한 스크립트 JSON (chapters/scenes 구조) |
| **RenderJob** | 영상 렌더링 작업 단위 (QUEUED → PROCESSING → COMPLETED/FAILED) |
| **RenderSpec** | 백엔드에서 조회한 렌더링 스냅샷 (Phase 38) |
| **SceneInfo** | 개별 씬 정보 (narration, caption, duration 등) |

---

## 2. 파일 구조 및 역할

### 2.1 전체 파일 맵

```
ctrlf-ai/
├── app/
│   ├── api/v1/                          # API 엔드포인트
│   │   ├── scripts.py                   # ★ 스크립트 CRUD/생성 API
│   │   ├── render_jobs.py               # ★ 렌더 잡 CRUD/실행 API
│   │   └── ws_render_progress.py        # ★ WebSocket 진행률 브로드캐스트
│   │
│   ├── services/                        # 비즈니스 로직
│   │   ├── video_script_generation_service.py  # ★ LLM 스크립트 생성
│   │   ├── render_job_runner.py               # ★ 렌더 잡 오케스트레이터
│   │   ├── video_renderer_real.py             # ★ 실제 렌더러 (7-Step)
│   │   ├── video_renderer_mvp.py              # MVP 렌더러 (테스트용)
│   │   ├── video_render_service.py            # 렌더 서비스 레거시
│   │   ├── video_composer.py                  # ★ FFmpeg 영상 합성
│   │   ├── video_progress_service.py          # 진행률 관리
│   │   ├── visual_plan.py                     # 시각적 계획 추출 (Phase 37)
│   │   └── image_asset_service.py             # 이미지 에셋 생성 (Phase 37)
│   │
│   ├── clients/                         # 외부 서비스 클라이언트
│   │   ├── tts_provider.py              # ★ TTS 제공자 (Polly/GCP/gTTS)
│   │   ├── storage_adapter.py           # ★ 스토리지 (Local/S3/Presigned)
│   │   ├── backend_client.py            # ★ 백엔드 통신 클라이언트
│   │   └── llm_client.py                # LLM API 클라이언트
│   │
│   ├── models/                          # 데이터 모델
│   │   ├── video_render.py              # ★ 렌더 관련 Pydantic 모델
│   │   ├── render_spec.py               # ★ RenderSpec 모델 (Phase 38)
│   │   └── video_progress.py            # 진행률 모델
│   │
│   └── repositories/                    # 데이터 저장소
│       └── render_job_repository.py     # ★ 렌더 잡 Repository
│
└── docs/
    ├── VIDEO_PIPELINE_ARCHITECTURE.md   # 파이프라인 아키텍처 문서
    └── VIDEO_HANDOVER_GUIDE.md          # 이 문서
```

> ★ 표시: 핵심 파일 (반드시 이해 필요)

---

## 3. 각 파일 상세 설명

### 3.1 API 엔드포인트

#### `app/api/v1/scripts.py`

**역할**: 스크립트 CRUD 및 자동 생성 API

**주요 엔드포인트**:

| Method | URL | 설명 | 변경 가능 |
|--------|-----|------|----------|
| `POST` | `/api/scripts` | 스크립트 생성 | O |
| `GET` | `/api/scripts/{script_id}` | 스크립트 조회 | O |
| `POST` | `/api/videos/{video_id}/scripts/generate` | **스크립트 자동 생성 (LLM)** | O |
| `GET` | `/api/scripts/{script_id}/editor` | 편집용 뷰 조회 | O |
| `PATCH` | `/api/scripts/{script_id}/editor` | 씬 부분 수정 | O |

**핵심 함수**:
```python
# 스크립트 자동 생성 (LLM 호출)
@router.post("/videos/{video_id}/scripts/generate")
async def generate_script(video_id: str, request: ScriptGenerateRequest):
    # 1. 교육 만료 여부 확인
    ensure_education_not_expired(video_id)

    # 2. LLM으로 스크립트 생성
    gen_service = get_video_script_generation_service()
    raw_json = await gen_service.generate_script(video_id, request.source_text, options)

    # 3. DRAFT로 저장
    script = service.create_script(video_id, raw_json, user_id)

    # 4. 백엔드 콜백 (비동기)
    asyncio.create_task(_notify_script_complete(...))
```

---

#### `app/api/v1/render_jobs.py`

**역할**: 렌더 잡 CRUD 및 실행 API

**주요 엔드포인트**:

| Method | URL | 설명 | 변경 가능 |
|--------|-----|------|----------|
| `POST` | `/api/v2/videos/{video_id}/render-jobs` | **렌더 잡 생성 (idempotent)** | X (API 스펙 고정) |
| `GET` | `/api/v2/videos/{video_id}/render-jobs` | 잡 목록 조회 | X |
| `GET` | `/api/v2/videos/{video_id}/render-jobs/{job_id}` | 잡 상세 조회 | X |
| `POST` | `/api/v2/videos/{video_id}/render-jobs/{job_id}/cancel` | 잡 취소 | X |
| `GET` | `/api/v2/videos/{video_id}/assets/published` | 발행된 에셋 조회 | X |
| `POST` | `/ai/video/job/{job_id}/start` | **잡 시작 (Backend → AI)** | X |
| `POST` | `/ai/video/job/{job_id}/retry` | **잡 재시도 (Backend → AI)** | X |

**중요 정책**:
- **Idempotency**: 동일 video_id에 PROCESSING/QUEUED 잡이 있으면 기존 잡 반환
- **상태 머신**: `QUEUED → PROCESSING → COMPLETED | FAILED`
- **APPROVED 스크립트만** 렌더 가능

---

#### `app/api/v1/ws_render_progress.py`

**역할**: WebSocket을 통한 실시간 렌더링 진행률 브로드캐스트

**엔드포인트**: `WS /ws/videos/{video_id}/render-progress`

**이벤트 형태**:
```json
{
  "job_id": "uuid",
  "video_id": "uuid",
  "status": "PROCESSING",
  "step": "GENERATE_TTS",
  "progress": 45,
  "message": "TTS 생성 중...",
  "timestamp": "2025-12-23T..."
}
```

---

### 3.2 서비스 레이어

#### `app/services/video_script_generation_service.py`

**역할**: LLM을 사용한 스크립트 자동 생성

**핵심 클래스**: `VideoScriptGenerationService`

**주요 메서드**:
```python
async def generate_script(
    self,
    video_id: str,
    source_text: str,
    options: ScriptGenerationOptions
) -> Dict[str, Any]:
    """
    1. LLM 프롬프트 구성 (system + user)
    2. LLM 호출 (temperature=0.3, max_tokens=4096)
    3. JSON 파싱 + Pydantic 검증
    4. 실패 시 최대 2회 재시도 (fix prompt)
    """
```

**입력 옵션** (`ScriptGenerationOptions`):
```python
@dataclass
class ScriptGenerationOptions:
    language: str = "ko"
    target_minutes: int = 3
    max_chapters: int = 5
    max_scenes_per_chapter: int = 6
    style: str = "friendly_security_training"
```

**출력 스키마** (`VideoScriptSchema`):
```python
class VideoScriptSchema(BaseModel):
    chapters: List[ChapterSchema]  # 챕터 목록

class ChapterSchema(BaseModel):
    chapter_id: int
    title: str
    scenes: List[SceneSchema]      # 씬 목록

class SceneSchema(BaseModel):
    scene_id: int
    narration: str                 # TTS 입력
    on_screen_text: Optional[str]  # 화면 자막
    duration_sec: Optional[float]  # 씬 길이
```

---

#### `app/services/render_job_runner.py`

**역할**: 렌더 잡 오케스트레이터 (핵심 컨트롤러)

**핵심 클래스**: `RenderJobRunner`

**상태 머신**:
```
              create_job()
                   │
                   ▼
             ┌──────────┐
             │  QUEUED  │
             └────┬─────┘
                  │ start_job()
                  ▼
           ┌────────────┐
           │ PROCESSING │◄──────── retry_job()
           └─────┬──────┘
                 │
        ┌────────┼────────┐
        ▼                 ▼
   ┌──────────┐      ┌────────┐
   │COMPLETED │      │ FAILED │
   └──────────┘      └────────┘
```

**주요 메서드**:

| 메서드 | 역할 |
|--------|------|
| `create_job()` | 잡 생성 (idempotent) |
| `start_job()` | 백엔드에서 render-spec 조회 후 실행 시작 |
| `retry_job()` | 기존 render-spec 스냅샷으로 재시도 |
| `cancel_job()` | 진행 중인 잡 취소 |
| `_execute_job_with_spec()` | 7-Step 파이프라인 실행 |

**Phase 38 스냅샷 정책**:
- `start_job()`: 백엔드에서 render-spec 조회 → DB에 스냅샷 저장
- `retry_job()`: 기존 스냅샷 재사용 (백엔드 재호출 X)

---

#### `app/services/video_renderer_real.py`

**역할**: 실제 영상 렌더링 (7-Step 파이프라인)

**핵심 클래스**: `RealVideoRenderer`

**7-Step 파이프라인**:

| Step | Enum | 진행률 | 설명 |
|------|------|--------|------|
| 1 | `VALIDATE_SCRIPT` | 0% → 15% | 스크립트 검증, SceneInfo 추출 |
| 2 | `GENERATE_TTS` | 15% → 40% | narration → MP3 합성 |
| 3 | `GENERATE_SUBTITLE` | 40% → 50% | SRT 자막 생성 |
| 4 | `RENDER_SLIDES` | 50% → 60% | 이미지 생성 (animated 모드) |
| 5 | `COMPOSE_VIDEO` | 60% → 85% | FFmpeg 영상 합성 |
| 6 | `UPLOAD_ASSETS` | 85% → 95% | 스토리지 업로드 |
| 7 | `FINALIZE` | 95% → 100% | 완료 처리, 콜백 |

**핵심 메서드**:
```python
async def execute_step(self, job_id: str, step: RenderStep) -> RenderStepResult:
    """단일 스텝 실행"""

async def _generate_tts(self, ctx: RealRenderJobContext) -> None:
    """TTS 합성 (전체 narration → 단일 MP3)"""

async def _compose_video(self, ctx: RealRenderJobContext) -> ComposedVideo:
    """FFmpeg 영상 합성"""

async def _upload_assets(self, ctx: RealRenderJobContext) -> Dict[str, str]:
    """스토리지 업로드 (MP4, SRT, JPG)"""
```

---

#### `app/services/video_composer.py`

**역할**: FFmpeg 기반 영상 합성

**핵심 클래스**: `VideoComposer`

**주요 메서드**:
```python
async def compose(
    self,
    scenes: List[SceneInfo],
    audio_path: str,
    output_dir: Path
) -> ComposedVideo:
    """
    1. 오디오 길이 조회 (ffprobe)
    2. 씬별 duration 계산
    3. SRT 자막 생성
    4. FFmpeg 영상 합성
    5. 썸네일 추출
    """
```

**FFmpeg 설정**:
```python
# Basic 모드
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 24
VIDEO_CODEC = "libx264"
VIDEO_BITRATE = "2M"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "128k"

# Animated 모드 (Phase 37)
FADE_DURATION = 0.5  # 초
KENBURNS_ZOOM = 1.1  # 줌 비율
```

---

### 3.3 클라이언트

#### `app/clients/tts_provider.py`

**역할**: Text-to-Speech 제공자 추상화

**지원 Provider**:

| Provider | 환경변수 | 음성 | 용도 |
|----------|----------|------|------|
| `mock` | `TTS_PROVIDER=mock` | 무음 | 테스트 |
| `gtts` | `TTS_PROVIDER=gtts` | Google | 무료 |
| `polly` | `TTS_PROVIDER=polly` | Seoyeon (AWS) | 운영 |
| `gcp` | `TTS_PROVIDER=gcp` | ko-KR-Wavenet-A | 운영 |

**인터페이스**:
```python
class BaseTTSProvider(ABC):
    @abstractmethod
    async def synthesize(
        self, text: str, voice: str = None, speed: float = 1.0, language: str = "ko"
    ) -> TTSResult:
        pass

    async def synthesize_to_file(
        self, text: str, output_path: Path, ...
    ) -> float:  # duration_sec 반환
        pass
```

---

#### `app/clients/storage_adapter.py`

**역할**: 파일 스토리지 추상화

**지원 Provider**:

| Provider | 환경변수 | 용도 |
|----------|----------|------|
| `local` | `STORAGE_PROVIDER=local` | 개발 |
| `s3` | `STORAGE_PROVIDER=s3` | 운영 (AWS S3) |
| `backend_presigned` | `STORAGE_PROVIDER=backend_presigned` | 운영 (백엔드 위임) |

**인터페이스**:
```python
class BaseStorageProvider(ABC):
    @abstractmethod
    async def put_object(
        self, data: Union[bytes, Path], key: str, content_type: str = None
    ) -> StorageResult:
        pass

    @abstractmethod
    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        pass
```

**Object Key 규칙**:
```
videos/{video_id}/{script_id}/{job_id}/video.mp4
videos/{video_id}/{script_id}/{job_id}/subtitles.srt
videos/{video_id}/{script_id}/{job_id}/thumb.jpg
```

---

#### `app/clients/backend_client.py`

**역할**: Spring 백엔드 통신

**주요 메서드**:

| 메서드 | 용도 | 방향 |
|--------|------|------|
| `fetch_render_spec()` | render-spec 조회 | AI → Backend |
| `notify_script_complete()` | 스크립트 생성 완료 콜백 | AI → Backend |
| `notify_job_complete()` | 렌더링 완료 콜백 | AI → Backend |

---

### 3.4 데이터 모델

#### `app/models/video_render.py`

**주요 모델**:

```python
# 스크립트 상태
class ScriptStatus(str, Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"

# 렌더 잡 상태 (DB 정렬됨)
class RenderJobStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

# 렌더 스텝
class RenderStep(str, Enum):
    VALIDATE_SCRIPT = "VALIDATE_SCRIPT"
    GENERATE_TTS = "GENERATE_TTS"
    GENERATE_SUBTITLE = "GENERATE_SUBTITLE"
    RENDER_SLIDES = "RENDER_SLIDES"
    COMPOSE_VIDEO = "COMPOSE_VIDEO"
    UPLOAD_ASSETS = "UPLOAD_ASSETS"
    FINALIZE = "FINALIZE"
```

#### `app/models/render_spec.py`

**Phase 38 RenderSpec 모델**:

```python
class RenderScene(BaseModel):
    scene_id: str
    scene_order: int
    chapter_title: str
    purpose: str               # hook, explanation, example, summary
    narration: str             # TTS 입력
    caption: str               # 화면 자막
    duration_sec: float
    visual_spec: Optional[VisualSpec]

class RenderSpec(BaseModel):
    script_id: str
    video_id: str
    title: str
    total_duration_sec: float
    scenes: List[RenderScene]
```

---

## 4. 변경 불가 API 명세

> **중요**: 아래 API들은 백엔드(Spring)와 협의된 스펙이므로 **변경 불가**입니다.
> 참조 문서: `docs/ctrlf_api_spec_fastapi_orchestrator_v2_3_db_aligned.md`

### 4.1 백엔드 → AI 호출 API

#### (1) 영상 생성 시작
```
POST /ai/video/job/{jobId}/start
```

**요청**: 없음 (Path Parameter만)

**응답**:
```json
{
  "job_id": "uuid",
  "status": "PROCESSING",
  "started": true,
  "message": "렌더링 시작됨",
  "error_code": null
}
```

**에러 코드**:
- `JOB_NOT_FOUND`: 잡 없음 (404)
- `SCRIPT_FETCH_FAILED`: render-spec 조회 실패 (502)
- `EMPTY_RENDER_SPEC`: 씬이 없음 (422)

---

#### (2) 영상 생성 재시도
```
POST /ai/video/job/{jobId}/retry
```

**정책**: 기존 render-spec 스냅샷 재사용 (백엔드 재호출 X)

**응답**: 동일

**에러 코드**:
- `JOB_NOT_FOUND`: 잡 없음 (404)
- `NO_RENDER_SPEC_FOR_RETRY`: 스냅샷 없음 (409)

---

### 4.2 AI → 백엔드 콜백 API

#### (1) 스크립트 생성 완료
```
POST /internal/callbacks/source-sets/{sourceSetId}/complete
```

**Body**:
```json
{
  "videoId": "uuid",
  "status": "COMPLETED | FAILED",
  "sourceSetStatus": "SCRIPT_READY | FAILED",
  "documents": [
    { "documentId": "uuid", "status": "COMPLETED", "failReason": null }
  ],
  "script": { /* 생성된 스크립트 JSON */ },
  "errorCode": null,
  "errorMessage": null
}
```

---

#### (2) 렌더링 완료
```
POST /video/job/{jobId}/complete
```

**Body**:
```json
{
  "jobId": "uuid",
  "videoUrl": "https://cdn.example.com/.../video.mp4",
  "duration": 180,
  "status": "COMPLETED"
}
```

---

### 4.3 AI가 백엔드에서 조회하는 API

#### (1) Render-Spec 조회
```
GET /internal/scripts/{scriptId}/render-spec
```

**응답**:
```json
{
  "script_id": "uuid",
  "video_id": "uuid",
  "title": "교육 제목",
  "total_duration_sec": 180,
  "scenes": [
    {
      "scene_id": "uuid",
      "scene_order": 1,
      "chapter_title": "Chapter 1",
      "purpose": "hook",
      "narration": "안녕하세요...",
      "caption": "피싱 공격이란",
      "duration_sec": 15.0
    }
  ]
}
```

---

### 4.4 DB 상태값 정렬 (필수)

> **변경 불가**: DB 스키마와 동일하게 유지

**Job 상태**:
```
QUEUED → PROCESSING → COMPLETED | FAILED
```
- ~~RENDERING~~ 대신 `PROCESSING` 사용 (DB 정렬)

**Source Set 상태**:
```
CREATED → LOCKED → SCRIPT_READY | FAILED
```

---

## 5. 데이터 모델

### 5.1 데이터 흐름 (크기 변환)

```
┌─────────────────────────────────────────────────────────────────┐
│                    데이터 크기 변환 (3분 영상 기준)               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Phase 1: 스크립트 생성                                          │
│  source_text → LLM Prompt → raw_output → raw_json               │
│    ~2KB    →    ~6KB     →   ~5KB     →   ~5KB                 │
│                                                                 │
│  Phase 2: 영상 렌더링                                            │
│  raw_json → SceneInfo[] → TTS(MP3) → Video(MP4)                │
│    ~5KB   →    ~3KB     →  ~1.5MB  →   ~25MB                   │
│                                                                 │
│  + SRT(~1KB) + Thumbnail(~50KB) → Upload → URLs                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 스크립트 JSON 구조

```json
{
  "chapters": [
    {
      "chapter_id": 1,
      "title": "피싱 공격이란?",
      "scenes": [
        {
          "scene_id": 1,
          "narration": "안녕하세요, 오늘은 피싱 공격에 대해...",
          "on_screen_text": "피싱 공격의 정의",
          "duration_sec": 15.0
        }
      ]
    }
  ]
}
```

---

## 6. 외부 서비스 연동

### 6.1 연동 다이어그램

```
┌──────────────────────────────────────────────────────────────────┐
│                      External Services                           │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌─────────────────────────────┐│
│  │   LLM    │    │   TTS    │    │         Storage             ││
│  │  (Qwen)  │    │ Provider │    │                             ││
│  ├──────────┤    ├──────────┤    ├─────────────────────────────┤│
│  │ OpenAI   │    │ • Polly  │    │ • Local (./data/assets)     ││
│  │ 호환 API │    │ • GCP    │    │ • S3 (운영)                  ││
│  │          │    │ • gTTS   │    │ • Backend Presigned         ││
│  └────┬─────┘    └────┬─────┘    └──────────────┬──────────────┘│
│       │               │                         │                │
│       ▼               ▼                         ▼                │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                  Backend (Spring)                         │  │
│  │  ─────────────────────────────────────────────────────── │  │
│  │  GET  /internal/scripts/{id}/render-spec                 │  │
│  │  POST /internal/callbacks/source-sets/{id}/complete      │  │
│  │  POST /video/job/{id}/complete                           │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 LLM 설정

| 설정 | 값 |
|------|-----|
| API | OpenAI-compatible |
| Model | `Qwen/Qwen2.5-7B-Instruct` |
| Temperature | 0.3 (일관성) |
| Max Tokens | 4096 |
| Timeout | 30초 |

### 6.3 TTS 설정

| Provider | Voice | 특징 |
|----------|-------|------|
| AWS Polly | Seoyeon | Neural, 고품질 |
| GCP | ko-KR-Wavenet-A | Wavenet, 고품질 |
| gTTS | - | 무료, 기본 품질 |

### 6.4 FFmpeg 설정

```bash
# 기본 영상 합성
ffmpeg -y \
  -f lavfi -i "color=c=0x1E1E1E:s=1280x720:d=180:r=24" \
  -i audio.mp3 \
  -vf "drawtext=text='자막':fontfile=...:fontsize=48:fontcolor=white:x=(w-text_w)/2:y=h-100" \
  -c:v libx264 -preset medium -b:v 2M \
  -c:a aac -b:a 128k \
  -shortest -movflags +faststart \
  output.mp4
```

---

## 7. 환경 설정

### 7.1 필수 환경변수

```bash
# LLM
LLM_BASE_URL=http://58.127.241.84:1237/
LLM_MODEL_NAME=Qwen/Qwen2.5-7B-Instruct

# TTS
TTS_PROVIDER=mock  # mock | gtts | polly | gcp

# Storage
STORAGE_PROVIDER=local  # local | s3 | backend_presigned
STORAGE_LOCAL_DIR=./data/assets
STORAGE_PUBLIC_BASE_URL=/assets

# Backend
BACKEND_BASE_URL=http://localhost:8080
BACKEND_API_TOKEN=<token>
BACKEND_INTERNAL_TOKEN=<token>

# 렌더링
RENDER_OUTPUT_DIR=./video_output
VIDEO_VISUAL_STYLE=basic  # basic | animated
```

### 7.2 선택 환경변수

```bash
# S3 (STORAGE_PROVIDER=s3)
AWS_S3_BUCKET=<bucket>
AWS_S3_REGION=ap-northeast-2
S3_ENDPOINT_URL=<minio-url>  # MinIO 호환

# AWS Polly (TTS_PROVIDER=polly)
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_REGION=ap-northeast-2

# GCP TTS (TTS_PROVIDER=gcp)
GOOGLE_APPLICATION_CREDENTIALS=<path>

# Animated 모드 (VIDEO_VISUAL_STYLE=animated)
VIDEO_FADE_DURATION=0.5
VIDEO_KENBURNS_ZOOM=1.1
```

---

## 8. 개발 시 주의사항

### 8.1 절대 변경 금지

1. **API URL 경로**: 백엔드와 협의된 스펙
   - `/ai/video/job/{jobId}/start`
   - `/ai/video/job/{jobId}/retry`
   - `/api/v2/videos/{video_id}/render-jobs`

2. **상태값 이름**: DB 스키마와 동일
   - `QUEUED`, `PROCESSING`, `COMPLETED`, `FAILED`
   - ~~RENDERING~~ 사용 금지

3. **콜백 페이로드**: 백엔드가 기대하는 필드명
   - `jobId`, `videoUrl`, `duration`, `status`

### 8.2 변경 시 백엔드 협의 필요

1. 새로운 에러 코드 추가
2. 콜백 페이로드 필드 추가/변경
3. render-spec 필드 추가 요청

### 8.3 자유롭게 변경 가능

1. 내부 서비스 로직
2. LLM 프롬프트 개선
3. FFmpeg 파라미터 튜닝
4. TTS Provider 추가
5. 로깅/모니터링

### 8.4 코딩 컨벤션

```python
# 1. 비동기 함수는 async def 사용
async def generate_tts(...):
    pass

# 2. 외부 서비스 호출은 try-except로 감싸기
try:
    result = await tts_provider.synthesize(text)
except Exception as e:
    logger.error(f"TTS failed: {e}")
    raise

# 3. 로깅 필수
logger.info(f"Starting render job: job_id={job_id}")
logger.error(f"Render failed: job_id={job_id}, error={e}")

# 4. 진행률 알림 (WebSocket)
await notify_render_progress(
    job_id=job_id,
    status=RenderJobStatus.PROCESSING,
    step=RenderStep.GENERATE_TTS,
    progress=25,
    message="TTS 생성 중..."
)
```

---

## 9. 테스트 방법

### 9.1 로컬 서버 실행

```bash
# 서버 시작
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 환경변수 설정 (테스트용)
export TTS_PROVIDER=mock
export STORAGE_PROVIDER=local
export AI_ENV=mock  # LLM mock 모드
```

### 9.2 API 테스트

```bash
# 스크립트 생성
curl -X POST http://localhost:8000/api/videos/test-video/scripts/generate \
  -H "Content-Type: application/json" \
  -d '{
    "source_text": "피싱 공격은 사회공학적 기법을 활용한...",
    "target_minutes": 3
  }'

# 렌더 잡 생성
curl -X POST http://localhost:8000/api/v2/videos/test-video/render-jobs \
  -H "Content-Type: application/json" \
  -d '{"script_id": "<script_id>"}'

# 잡 시작 (Backend → AI 시뮬레이션)
curl -X POST http://localhost:8000/ai/video/job/<job_id>/start
```

### 9.3 WebSocket 테스트

```javascript
// 브라우저 콘솔
const ws = new WebSocket('ws://localhost:8000/ws/videos/test-video/render-progress');
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

### 9.4 pytest 실행

```bash
# 전체 테스트
python -m pytest tests/ -v

# 영상 관련 테스트만
python -m pytest tests/test_phase32*.py tests/test_phase33*.py -v
```

---

## 부록: 파일별 의존성

```
render_jobs.py (API)
    └── render_job_runner.py (Service)
        ├── video_renderer_real.py (Renderer)
        │   ├── tts_provider.py (TTS)
        │   ├── video_composer.py (FFmpeg)
        │   └── storage_adapter.py (Storage)
        ├── render_job_repository.py (DB)
        └── backend_client.py (Callback)

scripts.py (API)
    └── video_script_generation_service.py (Service)
        └── llm_client.py (LLM)
```

---

## 연락처

인수인계 관련 문의사항이 있으시면 언제든 연락주세요.

---

*이 문서는 실제 코드 분석을 기반으로 작성되었습니다.*
