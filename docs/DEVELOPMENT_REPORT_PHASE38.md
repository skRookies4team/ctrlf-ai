# Phase 38: Script Snapshot on Job Start

## 개요

영상 생성 Job이 시작(`/start`)될 때마다 백엔드에서 scriptId 기준 최신 렌더 스펙(render-spec)을 가져와서 "스냅샷으로 고정"하고, 이후 TTS/렌더/업로드/재시도(retry)는 그 스냅샷만 사용하게 만드는 기능입니다.

## 변경된 파일 목록

### 새로 생성된 파일
| 파일 | 설명 |
|------|------|
| `app/models/render_spec.py` | RenderSpec, RenderScene, VisualSpec 모델 + 검증 유틸리티 |
| `app/clients/backend_script_client.py` | 백엔드 render-spec 조회 클라이언트 |
| `tests/test_phase38_script_snapshot.py` | Phase 38 테스트 (22개) |

### 수정된 파일
| 파일 | 변경 내용 |
|------|----------|
| `app/core/config.py` | BACKEND_INTERNAL_TOKEN, BACKEND_TIMEOUT_SEC, SCENE_DEFAULT_DURATION_SEC 추가 |
| `app/repositories/render_job_repository.py` | render_spec_json 필드 + DB 마이그레이션 + update_render_spec() |
| `app/services/render_job_runner.py` | start_job(), retry_job(), _execute_job_with_spec() 추가 |
| `app/api/v1/video_render.py` | POST /api/render-jobs/{job_id}/start, /retry 엔드포인트 추가 |
| `app/models/video_render.py` | RenderJobStartResponse 모델 추가 |

## 핵심 로직 설명

### 1. Job 시작 플로우 (`start_job`)
```
POST /api/render-jobs/{job_id}/start
    │
    ├─ 1. job 조회
    │
    ├─ 2. Idempotency 체크
    │      └─ render_spec_json 있고 RUNNING/SUCCEEDED/FAILED면 no-op
    │
    ├─ 3. render_spec_json 없으면
    │      └─ BackendScriptClient.get_render_spec(script_id) 호출
    │      └─ validate_render_spec()으로 검증/정규화
    │      └─ render_spec_json 저장 (스냅샷)
    │
    └─ 4. _execute_job_with_spec() 백그라운드 실행
```

### 2. 재시도 플로우 (`retry_job`)
```
POST /api/render-jobs/{job_id}/retry
    │
    ├─ 1. job 조회
    │
    ├─ 2. render_spec_json 없으면 에러 (NO_RENDER_SPEC_FOR_RETRY)
    │
    ├─ 3. RUNNING 상태면 에러
    │
    └─ 4. 기존 스냅샷 그대로 사용하여 _execute_job_with_spec() 실행
         (백엔드 재호출 없음!)
```

### 3. 데이터 모델

```python
# RenderSpec (백엔드에서 조회)
{
    "script_id": "uuid",
    "video_id": "uuid",
    "title": "교육 제목",
    "total_duration_sec": 120,
    "scenes": [
        {
            "scene_id": "uuid",
            "scene_order": 1,
            "chapter_title": "챕터명",
            "purpose": "hook|explanation|example|summary",
            "narration": "나레이션 텍스트",
            "caption": "화면 캡션",
            "duration_sec": 15,
            "visual_spec": {...}
        }
    ]
}
```

### 4. 에러 처리

| 에러 코드 | 상황 | HTTP 상태 |
|-----------|------|-----------|
| `JOB_NOT_FOUND` | job이 존재하지 않음 | 404 |
| `SCRIPT_NOT_FOUND` | 스크립트 없음 (백엔드 404) | 502 |
| `SCRIPT_FETCH_UNAUTHORIZED` | 인증 실패 (백엔드 401) | 502 |
| `SCRIPT_FETCH_SERVER_ERROR` | 서버 에러 (백엔드 5xx) | 502 |
| `EMPTY_RENDER_SPEC` | 씬이 0개 | 422 |
| `NO_RENDER_SPEC_FOR_RETRY` | 스냅샷 없이 retry 시도 | 409 |

### 5. 씬 검증/정규화

- `narration` 빈 문자열 → WARNING 로그 (TTS 스킵)
- `duration_sec <= 0` → 기본값 5초로 보정

## 환경변수 (.env)

```env
# Phase 38: Backend Script Client
BACKEND_BASE_URL=http://localhost:8080      # 백엔드 서비스 URL
BACKEND_INTERNAL_TOKEN=your-internal-token  # X-Internal-Token 헤더
BACKEND_TIMEOUT_SEC=30                      # API 타임아웃 (초)
SCENE_DEFAULT_DURATION_SEC=5.0              # 씬 기본 duration
```

## 실행 방법

### 서버 실행
```bash
# .env 설정 후
uvicorn app.main:app --reload --port 8000
```

### API 사용 예시

```bash
# 1. 렌더 잡 생성 (기존)
POST /api/videos/{video_id}/render-jobs
{
    "script_id": "script-uuid"
}
# → job_id 반환

# 2. Phase 38: 잡 시작
POST /api/render-jobs/{job_id}/start
# → 백엔드에서 render-spec 조회 후 스냅샷 저장 & 파이프라인 시작

# 3. 상태 조회
GET /api/render-jobs/{job_id}

# 4. 실패 시 재시도
POST /api/render-jobs/{job_id}/retry
# → 기존 스냅샷 재사용 (백엔드 호출 없음)
```

## 테스트 결과

```
$ pytest tests/test_phase38_script_snapshot.py -v

============================= 22 passed in 4.40s ==============================

테스트 케이스:
- TestBackendScriptClient (6개): HTTP 응답 처리, 에러 처리
- TestRenderSpecValidation (3개): 빈 narration, duration 보정, raw_json 변환
- TestRenderJobRunnerStartJob (9개): start_job, retry_job, idempotency, 에러 케이스
- TestRenderJobRepository (4개): render_spec_json CRUD
```

전체 테스트:
```
$ pytest -q

11 failed, 1006 passed, 1 skipped, 12 deselected in 336.93s
(실패는 Phase 38과 무관한 기존 테스트)
```

## API 문서

### POST /api/render-jobs/{job_id}/start

렌더 잡을 시작합니다 (스냅샷 기반).

**동작:**
1. 백엔드에서 최신 render-spec 조회
2. render-spec을 잡에 스냅샷으로 저장
3. 파이프라인 실행 시작

**Idempotent:** 이미 시작된 잡에 대해 재호출해도 안전

**Response:**
```json
{
    "job_id": "job-xxx",
    "status": "RUNNING",
    "started": true,
    "message": "Job started",
    "error_code": null
}
```

### POST /api/render-jobs/{job_id}/retry

실패한 렌더 잡을 재시도합니다.

**정책:** 기존 스냅샷 재사용, 백엔드 재호출 없음

**Response:** 위와 동일

## DB 마이그레이션

기존 `render_jobs` 테이블에 `render_spec_json TEXT` 컬럼이 자동 추가됩니다.
(서버 시작 시 `_migrate_add_render_spec_json()` 실행)

## 완료 조건 (AC) 체크리스트

- [x] /start 시점에만 backend render-spec 조회가 발생한다
- [x] render-spec는 job에 저장되어 재시도에서도 불변이다
- [x] retry는 기존 스냅샷을 재사용한다 (백엔드 호출 없음)
- [x] AI 서버에 스크립트 편집 API는 추가되지 않는다
- [x] 테스트가 통과한다 (22개)
