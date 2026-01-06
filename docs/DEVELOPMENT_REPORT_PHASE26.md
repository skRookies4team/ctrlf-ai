# Phase 26: 4대교육 연간 재발행 + EXPIRED(마감) 차단 게이트

**작성일**: 2025-12-18
**작성자**: AI Assistant (Claude)
**버전**: Phase 26

---

## 1. 개요

### 1.1 목표
4대교육(법정필수교육)의 연간 갱신 운영 정책을 코드로 구현합니다.
- 연간 재발행 시스템 구축
- EXPIRED 상태 동적 판정 및 차단 게이트 구현
- 관리자용 재발행 API 제공

### 1.2 배경
- 4대교육은 매년 새로운 education_id로 재발행(복제 발행)됨
- 마감일(due_date) 이후에는 해당 교육에 대한 접근을 완전히 차단해야 함
- Phase 22에서 구현한 `EducationCatalogService`를 확장하여 연간 운영 정책 지원

### 1.3 핵심 요구사항
| 항목 | 설명 |
|------|------|
| EXPIRED 판정 | `due_date 23:59:59` 이후 자동 만료 (동적 계산) |
| 접근 차단 | EXPIRED 교육은 목록/상세/시청/퀴즈 모두 404 |
| 재발행 | 새 education_id 생성, 자산/텍스트 복사 |
| 서버 신뢰 | 클라이언트 값 신뢰하지 않음 (서버 측 최종 차단) |

---

## 2. 구현 내용

### 2.1 파일 변경 요약

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/services/education_catalog_service.py` | 수정 | EducationMeta, is_expired(), reissue() 추가 |
| `app/api/v1/video.py` | 수정 | ensure_education_active() 게이트 추가 |
| `app/api/v1/admin.py` | **신규** | 재발행 API + 메타 조회 API |
| `app/main.py` | 수정 | admin 라우터 등록 |
| `tests/test_phase26_education_expired.py` | **신규** | Phase 26 테스트 21개 |

### 2.2 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Video/Quiz API Layer                             │
│                                                                          │
│   start_video ──┬── ensure_education_active() ──┬── 404 EDU_EXPIRED     │
│   update_progress│                               │                       │
│   complete_video ├── EducationCatalogService ────┤                       │
│   quiz/check    │   .is_expired(education_id)   │                       │
│   status        ──┘                              └── 200 OK (ACTIVE)     │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    EducationCatalogService                               │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    EducationMeta (dataclass)                     │    │
│  │                                                                  │    │
│  │  education_id: str          # EDU-SEC-2025-001                  │    │
│  │  year: int                  # 2025                               │    │
│  │  due_date: date             # 2025-12-31                        │    │
│  │  is_mandatory_4type: bool   # True                               │    │
│  │  video_asset_id: str        # video-asset-001 (재발행 시 복사)   │    │
│  │  script_text: str           # 스크립트 (재발행 시 복사)          │    │
│  │  subtitle_text: str         # 자막 (재발행 시 복사)              │    │
│  │                                                                  │    │
│  │  @property expires_at       # due_date 23:59:59 (Asia/Seoul)    │    │
│  │  @property status           # ACTIVE / EXPIRED (동적)           │    │
│  │  def is_expired(now)        # now > expires_at                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  Methods:                                                                │
│  ├── is_mandatory_4type(education_id) → bool                            │
│  ├── is_expired(education_id, now) → bool                               │
│  ├── register_education(...) → EducationMeta                            │
│  ├── get_education(education_id) → EducationMeta                        │
│  └── reissue(source_id, target_year, new_due_date) → EducationMeta     │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.3 EXPIRED 판정 로직

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         EXPIRED 판정 흐름                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. 요청 수신: POST /api/video/play/start                               │
│     │  training_id = "EDU-SEC-2025-001"                                 │
│     ▼                                                                    │
│  2. ensure_education_active(training_id)                                │
│     │                                                                    │
│     ▼                                                                    │
│  3. EducationCatalogService.is_expired(education_id, now=현재시각)      │
│     │                                                                    │
│     ├── 카탈로그에 없음 → False (하위 호환, 차단 안함)                  │
│     │                                                                    │
│     └── 카탈로그에 있음 → meta.is_expired(now)                          │
│         │                                                                │
│         ├── now > expires_at (due_date 23:59:59)                        │
│         │   └── True → HTTPException(404, EDU_EXPIRED)                  │
│         │                                                                │
│         └── now <= expires_at                                           │
│             └── False → 정상 진행                                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.4 재발행 프로세스

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         재발행 (Reissue) 흐름                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Request:                                                                │
│  {                                                                       │
│    "source_education_id": "EDU-SEC-2025-001",                           │
│    "target_year": 2026,                                                  │
│    "new_due_date": "2026-12-31"                                         │
│  }                                                                       │
│                                                                          │
│  1. Source 존재 확인                                                     │
│     └── 없으면 404 (SOURCE_NOT_FOUND)                                   │
│                                                                          │
│  2. 새 education_id 생성                                                 │
│     └── EDU-SEC-2025-001 → EDU-SEC-2026-001 (연도 치환)                 │
│                                                                          │
│  3. Target 중복 확인                                                     │
│     └── 이미 존재하면 409 (TARGET_EXISTS)                               │
│                                                                          │
│  4. Due date 범위 확인                                                   │
│     └── new_due_date.year != target_year이면 400 (DUE_DATE_OUT_OF_RANGE)│
│                                                                          │
│  5. 새 EducationMeta 생성 (복사되는 필드)                                │
│     ├── video_asset_id    (그대로 복사)                                 │
│     ├── script_text       (그대로 복사)                                 │
│     ├── subtitle_text     (그대로 복사)                                 │
│     ├── is_mandatory_4type (그대로 복사)                                │
│     ├── title             (그대로 복사)                                 │
│     └── video_ids         (그대로 복사)                                 │
│                                                                          │
│  6. 카탈로그에 등록                                                      │
│                                                                          │
│  Response (200):                                                         │
│  {                                                                       │
│    "success": true,                                                      │
│    "new_education_id": "EDU-SEC-2026-001",                              │
│    "source_education_id": "EDU-SEC-2025-001",                           │
│    "target_year": 2026,                                                  │
│    "due_date": "2026-12-31",                                            │
│    "expires_at": "2026-12-31T23:59:59+09:00",                           │
│    "copied_fields": {...}                                                │
│  }                                                                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. API 상세

### 3.1 EXPIRED 게이트 적용 엔드포인트

다음 모든 엔드포인트에서 EXPIRED 교육 접근 시 404 반환:

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/api/video/play/start` | POST | 영상 재생 시작 |
| `/api/video/progress` | POST | 진행률 업데이트 |
| `/api/video/complete` | POST | 완료 요청 |
| `/api/video/status` | GET | 상태 조회 |
| `/api/video/quiz/check` | GET | 퀴즈 시작 가능 여부 확인 |

**404 응답 예시:**
```json
{
  "detail": {
    "reason_code": "EDU_EXPIRED",
    "message": "Education EDU-SEC-2023-001 has expired and is no longer accessible."
  }
}
```

### 3.2 POST /api/admin/education/reissue

교육을 재발행(복제 발행)합니다.

**Request:**
```json
{
  "source_education_id": "EDU-SEC-2025-001",
  "target_year": 2026,
  "new_due_date": "2026-12-31"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "new_education_id": "EDU-SEC-2026-001",
  "source_education_id": "EDU-SEC-2025-001",
  "target_year": 2026,
  "due_date": "2026-12-31",
  "expires_at": "2026-12-31T23:59:59+09:00",
  "copied_fields": {
    "video_asset_id": "video-asset-001",
    "script_text": true,
    "subtitle_text": true,
    "is_mandatory_4type": true
  }
}
```

**에러 응답:**

| 상태 코드 | reason_code | 설명 |
|----------|-------------|------|
| 404 | SOURCE_NOT_FOUND | source_education_id가 없음 |
| 409 | TARGET_EXISTS | 생성될 education_id가 이미 존재 |
| 400 | DUE_DATE_OUT_OF_RANGE | new_due_date가 target_year 범위 밖 |

### 3.3 GET /api/admin/education/{education_id}

교육 메타데이터를 조회합니다 (개발/디버깅용).

**Response (200 OK):**
```json
{
  "education_id": "EDU-SEC-2025-001",
  "year": 2025,
  "due_date": "2025-12-31",
  "expires_at": "2025-12-31T23:59:59+09:00",
  "status": "ACTIVE",
  "is_mandatory_4type": true,
  "title": "보안교육 2025",
  "video_asset_id": "video-asset-001",
  "has_script": true,
  "has_subtitle": false
}
```

---

## 4. 주요 클래스 및 메서드

### 4.1 EducationMeta (dataclass)

```python
@dataclass
class EducationMeta:
    """교육 메타데이터."""
    education_id: str
    year: int
    due_date: date  # 마감일 (이 날 23:59:59까지 유효)
    is_mandatory_4type: bool = False
    title: Optional[str] = None
    video_asset_id: Optional[str] = None      # 재발행 시 복사
    script_text: Optional[str] = None         # 재발행 시 복사
    subtitle_text: Optional[str] = None       # 재발행 시 복사
    video_ids: List[str] = field(default_factory=list)
    created_at: Optional[datetime] = None

    @property
    def expires_at(self) -> datetime:
        """만료 시각: due_date 23:59:59 (Asia/Seoul)."""
        return datetime.combine(self.due_date, time(23, 59, 59), tzinfo=SEOUL_TZ)

    @property
    def status(self) -> str:
        """현재 상태 (동적 판정)."""
        now = datetime.now(SEOUL_TZ)
        return EducationStatus.EXPIRED if now > self.expires_at else EducationStatus.ACTIVE

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """만료 여부 확인."""
        if now is None:
            now = datetime.now(SEOUL_TZ)
        return now > self.expires_at
```

### 4.2 EducationCatalogService 확장

```python
class EducationCatalogService:
    """교육 메타데이터 서버 신뢰 소스."""

    # Phase 22 기존 메서드
    def is_mandatory_4type(self, education_id: str) -> bool: ...
    def register_mandatory_4type(self, education_id: str) -> None: ...

    # Phase 26 추가 메서드
    def is_expired(self, education_id: str, now: Optional[datetime] = None) -> bool:
        """교육이 만료되었는지 확인 (카탈로그에 없으면 False)."""

    def get_status(self, education_id: str) -> Optional[str]:
        """교육 상태 반환 (ACTIVE/EXPIRED)."""

    def register_education(self, education_id: str, year: int, ...) -> EducationMeta:
        """교육을 카탈로그에 등록."""

    def get_education(self, education_id: str) -> Optional[EducationMeta]:
        """교육 메타데이터 반환."""

    def reissue(self, source_education_id: str, target_year: int,
                new_due_date: date) -> EducationMeta:
        """교육을 재발행(복제 발행)."""

    def list_active_educations(self) -> List[EducationMeta]:
        """활성 상태인 교육 목록 반환."""
```

### 4.3 ensure_education_active (게이트 함수)

```python
def ensure_education_active(education_id: str) -> None:
    """교육이 활성 상태인지 확인 (EXPIRED 차단 게이트).

    EXPIRED된 교육은 목록/상세/시청/퀴즈 모두 서버에서 차단.
    직접 링크 접근도 차단하기 위해 404 반환.

    Raises:
        HTTPException: 교육이 만료된 경우 404
    """
    catalog = get_education_catalog_service()
    if catalog.is_expired(education_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "EDU_EXPIRED",
                "message": f"Education {education_id} has expired...",
            },
        )
```

---

## 5. 테스트 결과

### 5.1 Phase 26 테스트 (21개)

| 테스트 카테고리 | 테스트 수 | 상태 |
|---------------|----------|------|
| EXPIRED 판정 | 3 | ✅ PASS |
| EXPIRED 차단 API | 6 | ✅ PASS |
| 재발행 서비스 | 4 | ✅ PASS |
| Admin API | 6 | ✅ PASS |
| EducationMeta 모델 | 2 | ✅ PASS |
| **합계** | **21** | ✅ **ALL PASS** |

### 5.2 주요 테스트 케이스

```python
# 1. EXPIRED 판정 테스트
test_education_expires_after_due_2359
  - due_date 당일 23:59:00에는 허용
  - due_date 당일 23:59:59에도 허용
  - 다음날 00:00:01에는 차단

# 2. EXPIRED 차단 테스트
test_start_video_blocked_when_expired      # 404 확인
test_update_progress_blocked_when_expired  # 404 확인
test_complete_video_blocked_when_expired   # 404 확인
test_can_start_quiz_blocked_when_expired   # 404 확인
test_get_video_status_blocked_when_expired # 404 확인
test_active_education_not_blocked          # 200 확인

# 3. 재발행 테스트
test_reissue_creates_new_education_copying_assets_and_texts
  - 새 education_id 생성 규칙 확인
  - video_asset_id/script/subtitle 복사 확인
test_reissue_conflict_when_target_exists   # 409 확인
test_reissue_source_not_found              # ValueError 확인
test_reissue_due_date_out_of_range         # ValueError 확인
```

### 5.3 Phase 22 테스트 호환성

```
tests/test_phase22_video_progress.py: 20 passed
tests/test_phase26_education_expired.py: 21 passed
```

모든 Phase 22 테스트가 Phase 26 변경 후에도 정상 통과합니다.

---

## 6. 설계 결정 사항

### 6.1 동적 EXPIRED 판정

**결정**: DB 배치 없이 요청 시점에 `now > expires_at`으로 동적 판정

**이유**:
- 실시간 판정으로 정확한 마감 처리
- DB 상태 업데이트 불필요
- 서버 재시작 시에도 일관된 동작

### 6.2 하위 호환성 유지

**결정**: 카탈로그에 등록되지 않은 교육은 만료되지 않은 것으로 간주

**이유**:
- 기존 시스템과의 호환성 유지
- 점진적 마이그레이션 가능
- Phase 22 테스트 모두 통과

### 6.3 404 응답 사용

**결정**: EXPIRED 교육에 404 Not Found 반환 (403 대신)

**이유**:
- "직접 링크 접근 차단" 요구사항 충족
- 만료된 교육은 "존재하지 않는 것"으로 처리
- 클라이언트 캐시 무효화 용이

### 6.4 education_id 연도 치환 규칙

**결정**: `EDU-SEC-2025-001` → `EDU-SEC-2026-001` (연도만 치환)

**이유**:
- 단순하고 예측 가능한 규칙
- 기존 ID 체계 유지
- 연도가 없는 ID는 suffix 추가

### 6.5 Asia/Seoul 타임존 고정

**결정**: 모든 시간 계산에 `Asia/Seoul` 타임존 사용

**이유**:
- 한국 기업 교육 서비스에 적합
- 일관된 마감 시간 처리
- `zoneinfo.ZoneInfo("Asia/Seoul")` 사용

---

## 7. 체크리스트

- [x] EXPIRED 판정이 "due_date 23:59:59" 기준으로 동작
- [x] EXPIRED면 목록/상세/시청/퀴즈가 전부 서버에서 차단(404)
- [x] 재발행 API가 새 education_id를 만들고, 자산/텍스트를 복사
- [x] Phase 22 테스트 20개 모두 통과
- [x] Phase 26 테스트 21개 모두 통과
- [x] Admin API 구현 (reissue, get_education_meta)
- [x] 개발 문서 작성

---

## 8. 향후 개선 사항

### 8.1 단기
- [ ] 만료 임박 알림 (due_date - 7일)
- [ ] 재발행 이력 관리 (source → target 매핑)
- [ ] Admin API 인증 미들웨어 추가

### 8.2 중기
- [ ] 카탈로그 데이터 DB 저장 (현재 인메모리)
- [ ] 만료 교육 목록 조회 API
- [ ] 교육별 마감일 수정 API

### 8.3 장기
- [ ] 자동 재발행 스케줄러
- [ ] 교육 통계 대시보드 연동
- [ ] 아카이브 시청 기능 (선택적 활성화)

---

## 9. 사용 예시

### 9.1 백엔드에서 교육 등록

```java
// 백엔드가 교육 생성 시 AI 서버에 메타 등록 요청
// (실제 구현은 DB 연동으로 대체 가능)
```

### 9.2 재발행 요청

```java
// Spring Backend
Map<String, Object> reissueRequest = Map.of(
    "source_education_id", "EDU-SEC-2025-001",
    "target_year", 2026,
    "new_due_date", "2026-12-31"
);

ResponseEntity<Map> response = restTemplate.postForEntity(
    aiGatewayUrl + "/api/admin/education/reissue",
    new HttpEntity<>(reissueRequest, headers),
    Map.class
);

if (response.getStatusCode() == HttpStatus.OK) {
    String newId = (String) response.getBody().get("new_education_id");
    log.info("Education reissued: {} -> {}",
             "EDU-SEC-2025-001", newId);
}
```

### 9.3 EXPIRED 에러 처리 (프론트엔드)

```typescript
// React Frontend
try {
  await startVideo(trainingId);
} catch (error) {
  if (error.response?.status === 404) {
    const detail = error.response.data?.detail;
    if (detail?.reason_code === 'EDU_EXPIRED') {
      alert('이 교육은 마감되어 더 이상 시청할 수 없습니다.');
      navigate('/education/list');
    }
  }
}
```

---

## 10. 변경 파일 전체 목록

```
app/
├── api/v1/
│   ├── admin.py              # [신규] 재발행 API
│   └── video.py              # [수정] EXPIRED 게이트 추가
├── services/
│   └── education_catalog_service.py  # [수정] EducationMeta, reissue() 추가
└── main.py                   # [수정] admin 라우터 등록

tests/
└── test_phase26_education_expired.py  # [신규] 21개 테스트

docs/
└── DEVELOPMENT_REPORT_PHASE26.md      # [신규] 개발 보고서
```
