# Phase 27: 영상 생성 파이프라인 E2E (Video Render Pipeline)

**작성일**: 2025-12-18
**작성자**: AI Assistant (Claude)
**버전**: Phase 27

---

## 1. 개요

### 1.1 목표
교육 영상 생성 파이프라인의 End-to-End 구현입니다.
- 렌더 잡 상태 기계 구현 (PENDING → RUNNING → SUCCEEDED/FAILED/CANCELED)
- 7단계 파이프라인 스텝 구현
- MVP 비디오 렌더러 (TTS, 자막, 썸네일 생성)
- REST API를 통한 렌더 잡 관리

### 1.2 배경
- 4대교육 콘텐츠의 자동 영상 생성 필요
- 스크립트 기반 TTS + 자막 + 슬라이드 합성
- 백그라운드 비동기 처리 지원
- Phase 26의 EXPIRED 교육 차단 정책 연동

### 1.3 핵심 요구사항
| 항목 | 설명 |
|------|------|
| 상태 기계 | PENDING → RUNNING → (SUCCEEDED \| FAILED \| CANCELED) |
| 파이프라인 | 7단계: VALIDATE → TTS → SUBTITLE → SLIDES → COMPOSE → UPLOAD → FINALIZE |
| 스크립트 | DRAFT → APPROVED 상태 관리, APPROVED만 렌더 가능 |
| 중복 방지 | 동일 video_id에 RUNNING/PENDING 잡이 있으면 409 |
| Phase 26 연동 | EXPIRED 교육은 404 차단 |
| 권한 | 렌더 잡 생성은 REVIEWER 역할만 가능 |

---

## 2. 구현 내용

### 2.1 파일 변경 요약

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/models/video_render.py` | **신규** | 도메인 모델 + API 스키마 |
| `app/services/video_render_service.py` | **신규** | 렌더 잡 서비스 (상태 관리, 파이프라인) |
| `app/services/video_renderer_mvp.py` | **신규** | MVP 비디오 렌더러 구현 |
| `app/api/v1/video_render.py` | **신규** | REST API 엔드포인트 (7개) |
| `app/main.py` | 수정 | video_render 라우터 등록 |
| `tests/test_phase27_video_render.py` | **신규** | 테스트 19개 |

### 2.2 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Video Render API Layer                           │
│                                                                          │
│   POST /api/scripts              → 스크립트 생성 (DRAFT)                 │
│   POST /api/scripts/{id}/approve → 스크립트 승인 (APPROVED)              │
│   GET  /api/scripts/{id}         → 스크립트 조회                         │
│   POST /api/videos/{id}/render-jobs → 렌더 잡 생성 ─┬→ ensure_not_expired│
│   GET  /api/render-jobs/{id}     → 잡 상태 조회    │  verify_reviewer   │
│   POST /api/render-jobs/{id}/cancel → 잡 취소      │                    │
│   GET  /api/videos/{id}/asset    → 에셋 조회 ──────┴→ ensure_not_expired│
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      VideoRenderService                                  │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    In-Memory Stores                              │    │
│  │                                                                  │    │
│  │  VideoScriptStore   → script_id → VideoScript                   │    │
│  │  VideoRenderJobStore → job_id → VideoRenderJob                  │    │
│  │  VideoAssetStore    → video_asset_id → VideoAsset               │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  Methods:                                                                │
│  ├── create_script(video_id, raw_json) → VideoScript                    │
│  ├── approve_script(script_id) → VideoScript                            │
│  ├── create_render_job(video_id, script_id) → VideoRenderJob            │
│  ├── cancel_job(job_id) → VideoRenderJob                                │
│  ├── get_job_with_asset(job_id) → (job, asset)                          │
│  └── _execute_render_pipeline(job) → async background task              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      MVPVideoRenderer                                    │
│                                                                          │
│  7-Step Pipeline:                                                        │
│  ┌────────────┐  ┌────────────┐  ┌────────────────┐  ┌─────────────┐   │
│  │ VALIDATE   │→│ GENERATE   │→│ GENERATE       │→│ RENDER      │   │
│  │ SCRIPT     │  │ TTS        │  │ SUBTITLE       │  │ SLIDES      │   │
│  └────────────┘  └────────────┘  └────────────────┘  └─────────────┘   │
│         │                                                    │          │
│         ▼                                                    ▼          │
│  ┌────────────┐  ┌────────────┐  ┌────────────────┐                    │
│  │ FINALIZE   │←│ UPLOAD     │←│ COMPOSE        │                    │
│  │            │  │ ASSETS     │  │ VIDEO          │                    │
│  └────────────┘  └────────────┘  └────────────────┘                    │
│                                                                          │
│  Mock Mode: 의존성 없이 테스트 가능 (gTTS, moviepy, Pillow 선택적)       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.3 상태 기계

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      RenderJob State Machine                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                          ┌──────────┐                                   │
│                          │ PENDING  │ ← 생성 직후                       │
│                          └────┬─────┘                                   │
│                               │                                          │
│                   ┌───────────┴───────────┐                             │
│                   ▼                       ▼                              │
│            ┌──────────┐           ┌───────────┐                         │
│            │ CANCELED │           │  RUNNING  │                         │
│            └──────────┘           └─────┬─────┘                         │
│                                         │                                │
│                        ┌────────────────┼────────────────┐              │
│                        ▼                ▼                ▼               │
│                 ┌───────────┐   ┌───────────┐   ┌───────────┐          │
│                 │ SUCCEEDED │   │  FAILED   │   │ CANCELED  │          │
│                 └───────────┘   └───────────┘   └───────────┘          │
│                                                                          │
│  Valid Transitions:                                                      │
│  ├── PENDING → RUNNING    (파이프라인 시작)                             │
│  ├── PENDING → CANCELED   (시작 전 취소)                                │
│  ├── RUNNING → SUCCEEDED  (모든 스텝 성공)                              │
│  ├── RUNNING → FAILED     (스텝 실패)                                   │
│  └── RUNNING → CANCELED   (실행 중 취소)                                │
│                                                                          │
│  Terminal States: SUCCEEDED, FAILED, CANCELED (전이 불가)               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.4 파이프라인 스텝

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      7-Step Render Pipeline                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Step 1: VALIDATE_SCRIPT (0-10%)                                        │
│  ├── 스크립트 JSON 파싱                                                  │
│  └── 필수 필드 검증 (scenes, texts)                                     │
│                                                                          │
│  Step 2: GENERATE_TTS (10-30%)                                          │
│  ├── 텍스트 → 음성 변환 (gTTS)                                          │
│  └── audio.mp3 생성                                                     │
│                                                                          │
│  Step 3: GENERATE_SUBTITLE (30-40%)                                     │
│  ├── 스크립트에서 자막 추출                                              │
│  └── subtitles.srt 생성 (SRT 포맷)                                      │
│                                                                          │
│  Step 4: RENDER_SLIDES (40-60%)                                         │
│  ├── 각 씬별 슬라이드 이미지 생성 (Pillow)                              │
│  └── slides/scene_N.png 생성                                            │
│                                                                          │
│  Step 5: COMPOSE_VIDEO (60-80%)                                         │
│  ├── 슬라이드 + 오디오 합성 (moviepy)                                   │
│  └── output.mp4 생성                                                    │
│                                                                          │
│  Step 6: UPLOAD_ASSETS (80-90%)                                         │
│  ├── 생성된 파일을 스토리지에 업로드                                     │
│  └── URL 생성 (video_url, subtitle_url, thumbnail_url)                  │
│                                                                          │
│  Step 7: FINALIZE (90-100%)                                             │
│  ├── VideoAsset 레코드 생성                                              │
│  ├── job.asset_id 연결                                                   │
│  └── 상태 SUCCEEDED 전환                                                 │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. API 상세

### 3.1 스크립트 관리 API

#### POST /api/scripts
스크립트를 생성합니다 (DRAFT 상태).

**Request:**
```json
{
  "video_id": "video-001",
  "raw_json": {
    "scenes": [
      {"scene_id": 1, "text": "안녕하세요. 보안교육입니다."},
      {"scene_id": 2, "text": "피싱 메일에 주의하세요."}
    ]
  }
}
```

**Response (200 OK):**
```json
{
  "script_id": "script-uuid-12345",
  "video_id": "video-001",
  "status": "DRAFT",
  "raw_json": {...},
  "created_by": "anonymous",
  "created_at": "2025-12-18T10:30:00"
}
```

#### POST /api/scripts/{script_id}/approve
스크립트를 승인합니다 (APPROVED 상태로 전환).

**권한**: REVIEWER 또는 ADMIN만 가능

**Response (200 OK):**
```json
{
  "script_id": "script-uuid-12345",
  "video_id": "video-001",
  "status": "APPROVED",
  "raw_json": {...},
  "created_by": "reviewer",
  "created_at": "2025-12-18T10:30:00"
}
```

#### GET /api/scripts/{script_id}
스크립트 정보를 조회합니다.

### 3.2 렌더 잡 관리 API

#### POST /api/videos/{video_id}/render-jobs
새 렌더 잡을 생성합니다.

**권한**: REVIEWER만 가능
**검증**:
- 스크립트가 APPROVED 상태여야 함
- 해당 교육이 EXPIRED면 404
- 동일 video_id에 RUNNING/PENDING 잡이 있으면 409

**Request:**
```json
{
  "script_id": "script-uuid-12345"
}
```

**Response (200 OK):**
```json
{
  "job_id": "job-uuid-67890",
  "status": "PENDING"
}
```

**에러 응답:**

| 상태 코드 | reason_code | 설명 |
|----------|-------------|------|
| 400 | INVALID_SCRIPT | 스크립트 없음/미승인/video_id 불일치 |
| 403 | PERMISSION_DENIED | REVIEWER가 아님 |
| 404 | EDU_EXPIRED | 교육 만료 |
| 409 | DUPLICATE_JOB | 진행 중인 잡 존재 |

#### GET /api/render-jobs/{job_id}
렌더 잡 상태를 조회합니다.

**Response (200 OK):**
```json
{
  "job_id": "job-uuid-67890",
  "video_id": "video-001",
  "script_id": "script-uuid-12345",
  "status": "RUNNING",
  "step": "GENERATE_TTS",
  "progress": 25,
  "error_message": null,
  "started_at": "2025-12-18T10:31:00",
  "finished_at": null,
  "asset": null
}
```

**완료 시 Response:**
```json
{
  "job_id": "job-uuid-67890",
  "status": "SUCCEEDED",
  "step": "FINALIZE",
  "progress": 100,
  "asset": {
    "video_asset_id": "asset-uuid-11111",
    "video_id": "video-001",
    "video_url": "http://storage/videos/video-001.mp4",
    "thumbnail_url": "http://storage/thumbnails/video-001.jpg",
    "subtitle_url": "http://storage/subtitles/video-001.srt",
    "duration_sec": 120.5,
    "created_at": "2025-12-18T10:35:00"
  }
}
```

#### POST /api/render-jobs/{job_id}/cancel
진행 중인 렌더 잡을 취소합니다 (PENDING/RUNNING만 가능).

**Response (200 OK):**
```json
{
  "job_id": "job-uuid-67890",
  "status": "CANCELED",
  "message": "렌더 잡이 취소되었습니다."
}
```

**에러 응답:**

| 상태 코드 | reason_code | 설명 |
|----------|-------------|------|
| 404 | JOB_NOT_FOUND | 잡이 없음 |
| 409 | CANNOT_CANCEL | 이미 종료된 잡 (SUCCEEDED/FAILED/CANCELED) |

#### GET /api/videos/{video_id}/asset
비디오의 최신 에셋을 조회합니다.

**검증**: EXPIRED 교육은 404 반환

**Response (200 OK):**
```json
{
  "video_asset_id": "asset-uuid-11111",
  "video_id": "video-001",
  "video_url": "http://storage/videos/video-001.mp4",
  "thumbnail_url": "http://storage/thumbnails/video-001.jpg",
  "subtitle_url": "http://storage/subtitles/video-001.srt",
  "duration_sec": 120.5,
  "created_at": "2025-12-18T10:35:00"
}
```

---

## 4. 주요 클래스 및 메서드

### 4.1 도메인 모델

```python
class ScriptStatus(str, Enum):
    DRAFT = "DRAFT"          # 초안
    APPROVED = "APPROVED"    # 승인됨 (렌더 가능)
    REJECTED = "REJECTED"    # 반려됨


class RenderJobStatus(str, Enum):
    PENDING = "PENDING"      # 대기 중
    RUNNING = "RUNNING"      # 실행 중
    SUCCEEDED = "SUCCEEDED"  # 성공
    FAILED = "FAILED"        # 실패
    CANCELED = "CANCELED"    # 취소됨


class RenderStep(str, Enum):
    VALIDATE_SCRIPT = "VALIDATE_SCRIPT"    # 스크립트 검증
    GENERATE_TTS = "GENERATE_TTS"          # TTS 음성 생성
    GENERATE_SUBTITLE = "GENERATE_SUBTITLE" # 자막 생성
    RENDER_SLIDES = "RENDER_SLIDES"        # 슬라이드 렌더링
    COMPOSE_VIDEO = "COMPOSE_VIDEO"        # 영상 합성
    UPLOAD_ASSETS = "UPLOAD_ASSETS"        # 에셋 업로드
    FINALIZE = "FINALIZE"                  # 최종 완료


@dataclass
class VideoScript:
    """비디오 스크립트."""
    script_id: str
    video_id: str
    status: ScriptStatus
    raw_json: Dict[str, Any]
    created_by: str
    created_at: datetime
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None


@dataclass
class VideoRenderJob:
    """비디오 렌더 잡."""
    job_id: str
    video_id: str
    script_id: str
    status: RenderJobStatus
    step: Optional[RenderStep] = None
    progress: int = 0
    error_message: Optional[str] = None
    asset_id: Optional[str] = None
    requested_by: str = "system"
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


@dataclass
class VideoAsset:
    """비디오 에셋 (결과물)."""
    video_asset_id: str
    video_id: str
    video_url: str
    thumbnail_url: Optional[str] = None
    subtitle_url: Optional[str] = None
    duration_sec: Optional[float] = None
    created_at: datetime = field(default_factory=datetime.now)
```

### 4.2 VideoRenderService

```python
class VideoRenderService:
    """비디오 렌더링 서비스."""

    def create_script(self, video_id: str, raw_json: Dict,
                      created_by: str = "system") -> VideoScript:
        """스크립트 생성 (DRAFT 상태)."""

    def approve_script(self, script_id: str, approved_by: str) -> Optional[VideoScript]:
        """스크립트 승인 (APPROVED 상태로 전환)."""

    async def create_render_job(self, video_id: str, script_id: str,
                                 requested_by: str = "system") -> VideoRenderJob:
        """렌더 잡 생성.

        검증:
        - 스크립트 존재 및 APPROVED 상태
        - video_id 일치
        - 중복 잡 방지 (RUNNING/PENDING)
        """

    async def cancel_job(self, job_id: str) -> Optional[VideoRenderJob]:
        """잡 취소 (PENDING/RUNNING만 가능)."""

    def get_job_with_asset(self, job_id: str) -> Tuple[Optional[VideoRenderJob],
                                                        Optional[VideoAsset]]:
        """잡과 연결된 에셋 함께 조회."""

    async def _execute_render_pipeline(self, job: VideoRenderJob) -> None:
        """파이프라인 실행 (백그라운드 태스크).

        7단계 순차 실행:
        1. VALIDATE_SCRIPT
        2. GENERATE_TTS
        3. GENERATE_SUBTITLE
        4. RENDER_SLIDES
        5. COMPOSE_VIDEO
        6. UPLOAD_ASSETS
        7. FINALIZE
        """
```

### 4.3 VideoRenderer 인터페이스

```python
class VideoRenderer(Protocol):
    """비디오 렌더러 인터페이스."""

    async def render(
        self,
        job: VideoRenderJob,
        script: VideoScript,
        on_progress: Callable[[RenderStep, int], None],
    ) -> RenderedAssets:
        """영상 렌더링 실행.

        Args:
            job: 렌더 잡
            script: 승인된 스크립트
            on_progress: 진행률 콜백 (step, progress)

        Returns:
            RenderedAssets: 생성된 에셋 정보 (URLs, duration)
        """
```

### 4.4 MVPVideoRenderer

```python
class MVPVideoRenderer:
    """MVP 비디오 렌더러.

    Mock 모드와 실제 모드 모두 지원:
    - gTTS: TTS 생성
    - moviepy: 영상 합성
    - Pillow: 슬라이드/썸네일 생성

    의존성 없으면 자동으로 Mock 모드로 동작.
    """

    def __init__(self, mock_mode: bool = None):
        """초기화.

        Args:
            mock_mode: Mock 모드 강제 설정 (None이면 자동 감지)
        """
        if mock_mode is None:
            mock_mode = not self._check_dependencies()
        self.mock_mode = mock_mode

    async def render(self, job, script, on_progress) -> RenderedAssets:
        """7단계 파이프라인 실행."""
        # Step 1: VALIDATE_SCRIPT
        on_progress(RenderStep.VALIDATE_SCRIPT, 5)
        await self._validate_script(script)
        on_progress(RenderStep.VALIDATE_SCRIPT, 10)

        # Step 2: GENERATE_TTS
        on_progress(RenderStep.GENERATE_TTS, 15)
        audio_path = await self._generate_tts(script)
        on_progress(RenderStep.GENERATE_TTS, 30)

        # ... (나머지 스텝)

        return RenderedAssets(
            video_url=video_url,
            thumbnail_url=thumbnail_url,
            subtitle_url=subtitle_url,
            duration_sec=duration,
        )

    def _generate_srt(self, script: VideoScript) -> str:
        """SRT 자막 파일 생성."""
        srt_content = []
        for i, scene in enumerate(scenes, 1):
            start_time = self._format_srt_time(i * 5 - 5)
            end_time = self._format_srt_time(i * 5)
            srt_content.append(f"{i}\n{start_time} --> {end_time}\n{scene['text']}\n")
        return "\n".join(srt_content)
```

---

## 5. 테스트 결과

### 5.1 Phase 27 테스트 (19개)

| 테스트 카테고리 | 테스트 수 | 상태 |
|---------------|----------|------|
| 스크립트 검증 | 2 | ✅ PASS |
| 중복 잡 방지 | 2 | ✅ PASS |
| EXPIRED 차단 | 2 | ✅ PASS |
| 상태 전이 | 4 | ✅ PASS |
| 잡 취소 | 3 | ✅ PASS |
| 에셋 조회 | 2 | ✅ PASS |
| 모델 테스트 | 4 | ✅ PASS |
| **합계** | **19** | ✅ **ALL PASS** |

### 5.2 주요 테스트 케이스

```python
# 1. 스크립트 검증
test_create_render_job_requires_approved_script
  - DRAFT 스크립트로 잡 생성 시 ValueError
  - APPROVED 스크립트만 렌더 가능

test_create_render_job_requires_matching_video_id
  - script.video_id != request.video_id → ValueError

# 2. 중복 잡 방지
test_create_render_job_prevents_duplicate_running_job
  - RUNNING 잡이 있으면 409 CONFLICT
test_create_render_job_allows_after_completion
  - SUCCEEDED 잡 이후에는 새 잡 생성 가능

# 3. EXPIRED 차단 (Phase 26 연동)
test_create_render_job_blocked_when_education_expired
  - EXPIRED 교육 → 404 EDU_EXPIRED
test_get_asset_blocked_when_education_expired
  - EXPIRED 교육 → 404 EDU_EXPIRED

# 4. 상태 전이
test_job_transitions_pending_to_running
  - 파이프라인 시작 시 RUNNING 전환
test_job_transitions_to_succeeded
  - 모든 스텝 완료 → SUCCEEDED, progress=100
test_job_transitions_to_failed_on_error
  - 스텝 실패 → FAILED, error_message 설정

# 5. 잡 취소
test_cancel_pending_job
  - PENDING 상태에서 취소 가능
test_cancel_running_job
  - RUNNING 상태에서 취소 가능
test_cannot_cancel_finished_job
  - SUCCEEDED/FAILED → 409 CANNOT_CANCEL
```

### 5.3 회귀 테스트

```
tests/test_phase22_video_progress.py: 20 passed
tests/test_phase26_education_expired.py: 21 passed
tests/test_phase22_chat_router_integration.py: 15 passed
tests/test_personalization.py: 8 passed
tests/test_phase27_video_render.py: 19 passed
────────────────────────────────────────────────
Total: 83 tests passed
```

모든 Phase 22/26 테스트가 Phase 27 변경 후에도 정상 통과합니다.

---

## 6. 설계 결정 사항

### 6.1 인메모리 저장소

**결정**: VideoScript, VideoRenderJob, VideoAsset을 인메모리 딕셔너리에 저장

**이유**:
- MVP 단계에서 빠른 개발 가능
- 테스트 용이성
- 향후 DB 연동으로 교체 용이 (Store 인터페이스 동일)

### 6.2 백그라운드 태스크

**결정**: `asyncio.create_task()`로 파이프라인 실행

**이유**:
- API 응답 즉시 반환 (비동기 처리)
- 클라이언트가 폴링으로 상태 확인
- 향후 Celery/Redis Queue로 확장 가능

### 6.3 Mock 모드 자동 감지

**결정**: gTTS/moviepy/Pillow 의존성 없으면 자동으로 Mock 모드

**이유**:
- 테스트 환경에서 무거운 의존성 불필요
- CI/CD 파이프라인 호환성
- 개발 환경 설정 간소화

### 6.4 SRT 자막 포맷

**결정**: SubRip Text (SRT) 형식 사용

**이유**:
- 표준 자막 포맷
- 대부분의 플레이어 지원
- 단순한 텍스트 형식

### 6.5 스크립트 승인 분리

**결정**: 스크립트 생성(DRAFT)과 승인(APPROVED) 분리

**이유**:
- 검토 워크플로우 지원
- 실수 방지 (미검토 스크립트로 렌더 불가)
- 권한 분리 (작성자 vs 승인자)

---

## 7. 체크리스트

- [x] 상태 기계 구현 (PENDING → RUNNING → SUCCEEDED/FAILED/CANCELED)
- [x] 7단계 파이프라인 구현
- [x] 스크립트 APPROVED 검증
- [x] 중복 잡 방지 (409 CONFLICT)
- [x] Phase 26 EXPIRED 차단 연동
- [x] REVIEWER 역할 검증
- [x] MVP 렌더러 구현 (Mock 모드)
- [x] SRT 자막 생성
- [x] API 7개 엔드포인트 구현
- [x] 테스트 19개 모두 통과
- [x] Phase 22/26 회귀 테스트 통과
- [x] 개발 문서 작성

---

## 8. 향후 개선 사항

### 8.1 단기
- [ ] 실제 TTS 서비스 연동 (Google Cloud TTS, AWS Polly)
- [ ] 실제 영상 합성 (moviepy + FFmpeg)
- [ ] 스토리지 연동 (S3, GCS)
- [ ] 프로그레스 WebSocket 알림

### 8.2 중기
- [ ] 잡 큐 시스템 (Celery + Redis)
- [ ] 잡 상태 DB 저장 (PostgreSQL)
- [ ] 동시 렌더 제한 (Rate Limiting)
- [ ] 렌더 결과 캐싱

### 8.3 장기
- [ ] Phase 28: RAG 인덱싱 연동 (on_video_published hook)
- [ ] AI 스크립트 자동 생성
- [ ] 다국어 TTS 지원
- [ ] 영상 품질 옵션 (720p, 1080p)

---

## 9. 사용 예시

### 9.1 스크립트 생성 및 승인

```bash
# 1. 스크립트 생성
curl -X POST http://localhost:8000/api/scripts \
  -H "Content-Type: application/json" \
  -d '{
    "video_id": "video-001",
    "raw_json": {
      "scenes": [
        {"scene_id": 1, "text": "안녕하세요. 보안교육입니다."},
        {"scene_id": 2, "text": "피싱 메일에 주의하세요."}
      ]
    }
  }'

# Response:
# {"script_id": "script-abc123", "status": "DRAFT", ...}

# 2. 스크립트 승인
curl -X POST http://localhost:8000/api/scripts/script-abc123/approve

# Response:
# {"script_id": "script-abc123", "status": "APPROVED", ...}
```

### 9.2 렌더 잡 생성 및 조회

```bash
# 1. 렌더 잡 생성
curl -X POST http://localhost:8000/api/videos/video-001/render-jobs \
  -H "Content-Type: application/json" \
  -d '{"script_id": "script-abc123"}'

# Response:
# {"job_id": "job-xyz789", "status": "PENDING"}

# 2. 잡 상태 조회 (폴링)
curl http://localhost:8000/api/render-jobs/job-xyz789

# Response (진행 중):
# {"job_id": "job-xyz789", "status": "RUNNING", "step": "GENERATE_TTS", "progress": 25}

# Response (완료):
# {"job_id": "job-xyz789", "status": "SUCCEEDED", "progress": 100, "asset": {...}}
```

### 9.3 에셋 조회

```bash
curl http://localhost:8000/api/videos/video-001/asset

# Response:
# {
#   "video_asset_id": "asset-111",
#   "video_url": "http://storage/videos/video-001.mp4",
#   "thumbnail_url": "http://storage/thumbnails/video-001.jpg",
#   "subtitle_url": "http://storage/subtitles/video-001.srt",
#   "duration_sec": 120.5
# }
```

### 9.4 잡 취소

```bash
curl -X POST http://localhost:8000/api/render-jobs/job-xyz789/cancel

# Response:
# {"job_id": "job-xyz789", "status": "CANCELED", "message": "렌더 잡이 취소되었습니다."}
```

---

## 10. 변경 파일 전체 목록

```
app/
├── api/v1/
│   └── video_render.py           # [신규] 렌더 API 엔드포인트
├── models/
│   └── video_render.py           # [신규] 도메인 모델 + API 스키마
├── services/
│   ├── video_render_service.py   # [신규] 렌더 잡 서비스
│   └── video_renderer_mvp.py     # [신규] MVP 렌더러
└── main.py                       # [수정] video_render 라우터 등록

tests/
└── test_phase27_video_render.py  # [신규] 19개 테스트

docs/
└── DEVELOPMENT_REPORT_PHASE27.md # [신규] 개발 보고서
```
