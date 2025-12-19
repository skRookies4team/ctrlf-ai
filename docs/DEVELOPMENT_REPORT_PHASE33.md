# Phase 33: Render Job 운영화 (지속성/상태/재시도) + FE 연동용 API 마감

**작성일**: 2025-12-19
**Phase**: 33
**상태**: 완료
**테스트 결과**: 22 passed

---

## 1. 개요

Phase 33에서는 Phase 32의 실제 렌더러(RealVideoRenderer)와 WS 진행률을 "데모가 아니라 서비스처럼" 쓰기 위해 렌더 잡의 영속화와 운영 API를 구현했습니다.

### 1.1 목표

- **A) DB/모델**: RenderJob 영속화 (SQLite)
- **B) 서비스**: RenderJobRunner (실행기 + 진행률 DB 저장)
- **C) API**: FE 연동용 API (idempotent 생성, 목록/상세 조회, published assets)
- **D) WebSocket**: job_id 기준 구독 안정화

---

## 2. 구현 상세

### 2.1 RenderJob DB 모델

**파일**: `app/repositories/render_job_repository.py`

```python
class RenderJobEntity:
    job_id: str          # UUID, PK
    video_id: str        # FK
    script_id: str       # FK
    status: str          # PENDING | RUNNING | SUCCEEDED | FAILED | CANCELED
    step: str            # VALIDATE_SCRIPT | GENERATE_TTS | ...
    progress: int        # 0-100
    message: str
    error_code: str      # nullable
    error_message: str   # nullable
    assets: Dict         # {video_url, subtitle_url, thumbnail_url}
    created_by: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime
    finished_at: datetime
```

**SQLite 스키마**:
```sql
CREATE TABLE render_jobs (
    job_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    step TEXT,
    progress INTEGER DEFAULT 0,
    message TEXT,
    error_code TEXT,
    error_message TEXT,
    assets TEXT,  -- JSON
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX idx_render_jobs_video_id ON render_jobs(video_id);
CREATE INDEX idx_render_jobs_status ON render_jobs(status);
```

### 2.2 RenderJobRunner 서비스

**파일**: `app/services/render_job_runner.py`

```python
class RenderJobRunner:
    async def create_job(video_id, script_id, script, created_by) -> JobCreationResult
    def get_job(job_id) -> Optional[RenderJobEntity]
    def list_jobs(video_id, limit, offset) -> List[RenderJobEntity]
    def get_published_assets(video_id) -> Optional[Dict]
    async def cancel_job(job_id) -> Optional[RenderJobEntity]
```

**핵심 기능**:

1. **Idempotent 잡 생성**:
   - RUNNING/PENDING 잡이 있으면 기존 잡 반환
   - 없으면 새 잡 생성 및 백그라운드 실행

2. **진행률 DB 동기화**:
   - 각 단계마다 DB 업데이트
   - WebSocket으로 실시간 브로드캐스트

3. **성공/실패 처리**:
   - 성공 시: assets 저장, status=SUCCEEDED
   - 실패 시: error_code/error_message 저장, 임시 파일 정리

### 2.3 API 엔드포인트

**파일**: `app/api/v1/video_render_phase33.py`

| 엔드포인트 | 메서드 | 설명 |
|------------|--------|------|
| `/api/v2/videos/{video_id}/render-jobs` | POST | 잡 생성 (idempotent) |
| `/api/v2/videos/{video_id}/render-jobs` | GET | 잡 목록 조회 |
| `/api/v2/videos/{video_id}/render-jobs/{job_id}` | GET | 잡 상세 조회 |
| `/api/v2/videos/{video_id}/render-jobs/{job_id}/cancel` | POST | 잡 취소 |
| `/api/v2/videos/{video_id}/assets/published` | GET | 발행된 에셋 조회 |

**잡 생성 응답 (Idempotent)**:
```json
// 새 잡 생성 (202 Accepted)
{
    "job_id": "job-xxx",
    "status": "PENDING",
    "progress": 0,
    "step": null,
    "message": "New job created and started",
    "created": true
}

// 기존 잡 반환 (200 OK)
{
    "job_id": "job-existing",
    "status": "RUNNING",
    "progress": 45,
    "step": "COMPOSE_VIDEO",
    "message": "Existing RUNNING job returned",
    "created": false
}
```

**발행된 에셋 응답**:
```json
{
    "video_id": "video-001",
    "published": true,
    "video_url": "http://example.com/video.mp4",
    "subtitle_url": "http://example.com/sub.srt",
    "thumbnail_url": "http://example.com/thumb.jpg",
    "duration_sec": 180.5,
    "published_at": "2025-12-19T10:30:00",
    "script_id": "script-001",
    "job_id": "job-xxx"
}
```

### 2.4 WebSocket job_id 필터링

**파일**: `app/api/v1/ws_render_progress.py`

**엔드포인트**: `WS /ws/videos/{video_id}/render-progress?job_id=xxx`

**필터링 동작**:
- `job_id` 지정 시: 해당 잡의 이벤트만 수신
- `job_id` 미지정 시: 최신 RUNNING 잡으로 자동 매핑, 없으면 모든 이벤트 수신

**연결 성공 메시지**:
```json
{
    "type": "connected",
    "video_id": "video-001",
    "job_id": "job-xxx",
    "message": "Connected to render progress stream",
    "timestamp": "2025-12-19T10:30:00Z"
}
```

---

## 3. 환경변수

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `RENDER_JOB_DB_PATH` | `./data/render_jobs.db` | RenderJob DB 경로 |

---

## 4. 사용 예시 (curl)

### 4.1 렌더 잡 생성 (Idempotent)

```bash
# 새 잡 생성
curl -X POST http://localhost:8000/api/v2/videos/video-001/render-jobs \
  -H "Content-Type: application/json" \
  -d '{"script_id": "script-001"}'

# 응답 (202 새 잡 / 200 기존 잡)
# {"job_id": "job-xxx", "status": "PENDING", "created": true}
```

### 4.2 잡 목록 조회

```bash
curl http://localhost:8000/api/v2/videos/video-001/render-jobs?limit=10

# 응답
# {"video_id": "video-001", "jobs": [...], "total": 5, "limit": 10, "offset": 0}
```

### 4.3 잡 상세 조회

```bash
curl http://localhost:8000/api/v2/videos/video-001/render-jobs/job-xxx

# 응답 (SUCCEEDED 시 assets 포함)
# {"job_id": "job-xxx", "status": "SUCCEEDED", "assets": {...}}
```

### 4.4 발행된 에셋 조회

```bash
curl http://localhost:8000/api/v2/videos/video-001/assets/published

# 응답
# {"published": true, "video_url": "...", "subtitle_url": "...", ...}
```

### 4.5 WebSocket 연결 (wscat)

```bash
# 특정 잡 구독
wscat -c "ws://localhost:8000/ws/videos/video-001/render-progress?job_id=job-xxx"

# 최신 RUNNING 잡 자동 매핑
wscat -c "ws://localhost:8000/ws/videos/video-001/render-progress"
```

---

## 5. FE 연동 시나리오

```javascript
// 1. 렌더 잡 생성
const response = await fetch('/api/v2/videos/video-001/render-jobs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({script_id: 'script-001'})
});
const {job_id, created} = await response.json();

// 2. WebSocket으로 진행률 구독
const ws = new WebSocket(`ws://localhost:8000/ws/videos/video-001/render-progress?job_id=${job_id}`);

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log(`Progress: ${data.progress}% - ${data.message}`);

    if (data.status === 'SUCCEEDED') {
        // 3. 완료 후 에셋 URL 획득
        fetchPublishedAssets(data.video_id);
        ws.close();
    }
};

// 3. 발행된 에셋 조회
async function fetchPublishedAssets(videoId) {
    const res = await fetch(`/api/v2/videos/${videoId}/assets/published`);
    const assets = await res.json();
    if (assets.published) {
        console.log('Video URL:', assets.video_url);
    }
}
```

---

## 6. 테스트 결과

**파일**: `tests/test_phase33_render_job_ops.py`

| 테스트 클래스 | 테스트 수 | 설명 |
|-------------|----------|------|
| `TestRenderJobRepository` | 7 | DB 영속화 (저장/조회/업데이트) |
| `TestRenderJobRunner` | 6 | 잡 생성/취소/에셋 조회 |
| `TestRenderJobAPI` | 5 | API 엔드포인트 |
| `TestWebSocketJobFilter` | 2 | job_id 필터링 |
| `TestIntegration` | 2 | 전체 흐름 + 회귀 테스트 |

```bash
# 테스트 실행
python -m pytest tests/test_phase33_render_job_ops.py -v

# 결과
22 passed in 3.41s
```

---

## 7. 변경된 파일

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `app/repositories/render_job_repository.py` | 신규 | RenderJob DB 저장소 |
| `app/services/render_job_runner.py` | 신규 | RenderJob 실행기 |
| `app/api/v1/video_render_phase33.py` | 신규 | Phase 33 API 엔드포인트 |
| `app/models/video_render.py` | 수정 | Phase 33 응답 모델 추가 |
| `app/api/v1/ws_render_progress.py` | 수정 | job_id 필터링 추가 |
| `app/main.py` | 수정 | Phase 33 라우터 등록 |
| `tests/test_phase33_render_job_ops.py` | 신규 | Phase 33 테스트 22개 |
| `docs/DEVELOPMENT_REPORT_PHASE33.md` | 신규 | Phase 33 개발 리포트 |

---

## 8. 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  1. POST /render-jobs (Idempotent)                       │   │
│  │  2. WS /render-progress?job_id=xxx                       │   │
│  │  3. GET /assets/published                                │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    RenderJobRunner                               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  create_job() ──► DB Save ──► Background Execute        │   │
│  │       │                              │                   │   │
│  │       │         ┌────────────────────┼────────────────┐  │   │
│  │       │         │                    ▼                │  │   │
│  │       │         │  ┌──────────────────────────────┐   │  │   │
│  │       │         │  │   RealVideoRenderer          │   │  │   │
│  │       │         │  │   (TTS + FFmpeg + Storage)   │   │  │   │
│  │       │         │  └──────────────────────────────┘   │  │   │
│  │       │         │                    │                │  │   │
│  │       ▼         │                    ▼                │  │   │
│  │  ┌─────────┐    │      ┌─────────────────────┐       │  │   │
│  │  │ SQLite  │◄───┼──────│ Progress Updates    │       │  │   │
│  │  │   DB    │    │      └─────────────────────┘       │  │   │
│  │  └─────────┘    │                    │                │  │   │
│  │       │         │                    ▼                │  │   │
│  │       │         │      ┌─────────────────────┐       │  │   │
│  │       │         │      │ WebSocket Broadcast │       │  │   │
│  │       │         │      │ (job_id filtered)   │       │  │   │
│  │       │         │      └─────────────────────┘       │  │   │
│  │       │         └─────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 9. 정책 요약

| 정책 | 설명 |
|------|------|
| **Idempotency** | 같은 video_id에 RUNNING/PENDING 잡이 있으면 기존 잡 반환 |
| **스크립트 검증** | APPROVED 스크립트만 렌더 가능 |
| **중복 방지** | 동시에 2개 이상 렌더 불가 (서버 자원 보호) |
| **영속성** | 서버 재시작 후에도 잡 상태 유지 |
| **에러 처리** | 실패 시 error_code/error_message 저장, 임시 파일 정리 |

---

## 10. 향후 계획

1. **Redis 기반 분산 잡 큐**: 멀티 인스턴스 환경 지원
2. **재시도 메커니즘**: 실패한 잡 자동 재시도 (max 3회)
3. **TTL 정책**: 오래된 FAILED 잡 자동 정리
4. **인증/권한**: JWT 기반 접근 제어
5. **Webhook 알림**: 잡 완료 시 외부 서비스 알림

---

**작성자**: Claude Code
**검토자**: -
