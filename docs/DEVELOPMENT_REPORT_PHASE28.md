# Phase 28: Published Video → KB Indexing (승인본만 RAG 적재)

**작성일**: 2025-12-18
**작성자**: AI Assistant (Claude)
**버전**: Phase 28

---

## 1. 개요

### 1.1 목표
승인된 교육 영상 스크립트를 KB(Knowledge Base)에 적재하여 RAG 검색 대상으로 만듭니다.
- RAG 적재 타이밍을 엄격하게 고정 (PUBLISHED 상태만)
- 승인된 스크립트 + 렌더 성공 후에만 적재
- 최신 버전 1개만 ACTIVE, 이전 버전은 ARCHIVED/삭제

### 1.2 핵심 정책 (절대 불변)
| 항목 | 정책 |
|------|------|
| 적재 시점 | PUBLISHED(영상 생성 + 업로드 + 검토자 승인) 이후에만 |
| 적재 대상 | APPROVED 스크립트만 |
| 초안 처리 | DRAFT/REVIEW 상태는 절대 KB에 넣지 않음 |
| 버전 관리 | 최신 버전 1개만 ACTIVE, 이전 버전은 ARCHIVED |
| EXPIRED | 검색/적재 모두 제외 |

### 1.3 구현 목표 (Done Definition)
1. 검토자가 PUBLISH 수행
2. 서버가 (APPROVED 스크립트 + 렌더 SUCCEEDED + not EXPIRED) 검증
3. KB Index Job 실행하여 벡터DB에 upsert
4. 챗봇 RAG 검색에서 "교육 스크립트 근거" 검색됨
5. 재발행 시 이전 버전은 검색에서 사라짐

---

## 2. 구현 내용

### 2.1 파일 변경 요약

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/models/video_render.py` | 수정 | KBIndexStatus, KBDocumentStatus enum 추가 |
| `app/models/video_render.py` | 수정 | VideoScript에 KB 인덱스 필드 추가 |
| `app/models/video_render.py` | 수정 | KBChunk 데이터클래스 추가 |
| `app/models/video_render.py` | 수정 | PublishResponse, KBIndexStatusResponse 추가 |
| `app/services/kb_index_service.py` | **신규** | KB 인덱싱 서비스 |
| `app/api/v1/video_render.py` | 수정 | publish, kb-status 엔드포인트 추가 |
| `tests/test_phase28_kb_indexing.py` | **신규** | 테스트 23개 |

### 2.2 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Publish Flow                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   1. POST /api/videos/{video_id}/publish                                │
│      │                                                                   │
│      ▼                                                                   │
│   2. 검증 (verify_reviewer_role)                                        │
│      │                                                                   │
│      ▼                                                                   │
│   3. 검증 (ensure_education_not_expired) ──→ 404 EDU_EXPIRED            │
│      │                                                                   │
│      ▼                                                                   │
│   4. 검증 (render job SUCCEEDED?) ──────────→ 409 RENDER_NOT_SUCCEEDED  │
│      │                                                                   │
│      ▼                                                                   │
│   5. 검증 (script APPROVED?) ───────────────→ 409 SCRIPT_NOT_APPROVED   │
│      │                                                                   │
│      ▼                                                                   │
│   6. 상태 변경                                                           │
│      ├── script.status = PUBLISHED                                      │
│      └── script.kb_index_status = PENDING                               │
│      │                                                                   │
│      ▼                                                                   │
│   7. asyncio.create_task(_run_kb_indexing)                              │
│      │                                                                   │
│      ▼                                                                   │
│   8. Response: {kb_index_status: "PENDING"}                             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼ (비동기)
┌─────────────────────────────────────────────────────────────────────────┐
│                      KBIndexService Pipeline                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   1. kb_index_status = RUNNING                                          │
│      │                                                                   │
│      ▼                                                                   │
│   2. build_chunks_from_script(script.raw_json)                          │
│      │  - chapters/scenes 구조 파싱                                     │
│      │  - 씬 단위로 1 chunk 생성                                        │
│      │  - chunk_id = script_id:chapter:scene                            │
│      │                                                                   │
│      ▼                                                                   │
│   3. archive_previous_version(video_id, script_id)                      │
│      │  - 이전 ACTIVE 스크립트 ARCHIVED 처리                            │
│      │  - 벡터DB에서 삭제 (검색 제외)                                   │
│      │                                                                   │
│      ▼                                                                   │
│   4. upsert_chunks(doc_id, chunks, metadata)                            │
│      │  - 임베딩 생성                                                   │
│      │  - Milvus에 upsert                                               │
│      │                                                                   │
│      ▼                                                                   │
│   5. kb_index_status = SUCCEEDED                                        │
│      └── kb_indexed_at = now()                                          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.3 KB 인덱스 상태 머신

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      KBIndexStatus State Machine                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                     ┌─────────────┐                                     │
│                     │ NOT_INDEXED │ ← 초기 상태                         │
│                     └──────┬──────┘                                     │
│                            │ publish()                                   │
│                            ▼                                             │
│                     ┌─────────────┐                                     │
│                     │   PENDING   │                                     │
│                     └──────┬──────┘                                     │
│                            │ _run_kb_indexing()                         │
│                            ▼                                             │
│                     ┌─────────────┐                                     │
│                     │   RUNNING   │                                     │
│                     └──────┬──────┘                                     │
│                            │                                             │
│              ┌─────────────┴─────────────┐                              │
│              ▼                           ▼                               │
│       ┌─────────────┐             ┌─────────────┐                       │
│       │  SUCCEEDED  │             │   FAILED    │                       │
│       └─────────────┘             └─────────────┘                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.4 청킹 규칙 (Script → KB Chunks)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Script JSON Structure                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  {                                                                       │
│    "chapters": [                                                         │
│      {                                                                   │
│        "chapter_id": 1,                                                  │
│        "title": "보안교육 개요",                                         │
│        "scenes": [                                                       │
│          {                                                               │
│            "scene_id": 1,                                                │
│            "purpose": "인사",                                            │
│            "narration": "안녕하세요.",                                   │
│            "caption": "환영합니다",                                      │
│            "source_refs": {"doc_id": "...", "chunk_id": "..."}          │
│          }                                                               │
│        ]                                                                 │
│      }                                                                   │
│    ]                                                                     │
│  }                                                                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      KBChunk (Scene 단위)                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  KBChunk {                                                               │
│    chunk_id: "script-001:1:1",     # script_id:chapter:scene            │
│    video_id: "video-001",                                                │
│    script_id: "script-001",                                              │
│    chapter_order: 1,                                                     │
│    scene_order: 1,                                                       │
│    chapter_title: "보안교육 개요",                                       │
│    scene_purpose: "인사",                                                │
│    content: "안녕하세요.\n환영합니다",  # narration + caption           │
│    source_refs: {"doc_id": "...", "chunk_id": "..."},                   │
│    metadata: {course_type, year, training_id, domain}                   │
│  }                                                                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. API 상세

### 3.1 POST /api/videos/{video_id}/publish

영상을 발행하고 KB에 적재합니다.

**권한**: REVIEWER만 가능

**검증 순서**:
1. REVIEWER 역할 확인
2. EXPIRED 교육 확인 → 404 EDU_EXPIRED
3. SUCCEEDED 렌더 잡 확인 → 409 RENDER_NOT_SUCCEEDED
4. APPROVED 스크립트 확인 → 409 SCRIPT_NOT_APPROVED

**Response (200 OK):**
```json
{
  "video_id": "video-001",
  "script_id": "script-001",
  "status": "PUBLISHED",
  "kb_index_status": "PENDING",
  "message": "영상 발행이 시작되었습니다. KB 인덱싱이 진행 중입니다."
}
```

**에러 응답:**

| 상태 코드 | reason_code | 설명 |
|----------|-------------|------|
| 403 | PERMISSION_DENIED | REVIEWER가 아님 |
| 404 | EDU_EXPIRED | 교육 만료 |
| 409 | RENDER_NOT_SUCCEEDED | SUCCEEDED 잡 없음 |
| 409 | SCRIPT_NOT_APPROVED | 스크립트 미승인 |

### 3.2 GET /api/videos/{video_id}/kb-status

KB 인덱스 상태를 조회합니다.

**Response (200 OK):**
```json
{
  "video_id": "video-001",
  "script_id": "script-001",
  "kb_index_status": "SUCCEEDED",
  "kb_indexed_at": "2025-12-18T10:30:00",
  "kb_document_status": "ACTIVE",
  "kb_last_error": null
}
```

---

## 4. 주요 클래스 및 메서드

### 4.1 Enum 정의

```python
class KBIndexStatus(str, Enum):
    """KB 인덱스 상태."""
    NOT_INDEXED = "NOT_INDEXED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class KBDocumentStatus(str, Enum):
    """KB 문서 상태."""
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
```

### 4.2 VideoScript 확장 (Phase 28)

```python
@dataclass
class VideoScript:
    # Phase 27 기존 필드
    script_id: str
    video_id: str
    status: ScriptStatus  # PUBLISHED 상태 추가
    raw_json: Dict[str, Any]
    created_by: str
    created_at: datetime

    # Phase 28 KB 인덱스 필드
    kb_index_status: KBIndexStatus = KBIndexStatus.NOT_INDEXED
    kb_indexed_at: Optional[datetime] = None
    kb_last_error: Optional[str] = None
    kb_document_id: Optional[str] = None
    kb_document_status: KBDocumentStatus = KBDocumentStatus.ACTIVE

    def is_approved(self) -> bool:
        """APPROVED 또는 PUBLISHED면 True."""
        return self.status in (ScriptStatus.APPROVED, ScriptStatus.PUBLISHED)

    def is_published(self) -> bool:
        """PUBLISHED 상태인지 확인."""
        return self.status == ScriptStatus.PUBLISHED

    def is_kb_indexed(self) -> bool:
        """KB 인덱싱 완료 여부."""
        return self.kb_index_status == KBIndexStatus.SUCCEEDED
```

### 4.3 KBIndexService

```python
class KBIndexService:
    """KB 인덱스 서비스."""

    async def index_published_video(
        self,
        video_id: str,
        script: VideoScript,
        course_type: str = "TRAINING",
        year: Optional[int] = None,
    ) -> KBIndexStatus:
        """발행된 영상의 스크립트를 KB에 적재."""

    def build_chunks_from_script(
        self,
        script: VideoScript,
        course_type: str = "TRAINING",
    ) -> List[KBChunk]:
        """스크립트 JSON → 청크 리스트 변환."""

    async def upsert_chunks(
        self,
        doc_id: str,
        chunks: List[KBChunk],
        metadata: Dict[str, Any],
    ) -> int:
        """청크들을 벡터 DB에 upsert."""

    async def archive_previous_version(
        self,
        video_id: str,
        current_script_id: str,
    ) -> int:
        """이전 버전 아카이브/삭제."""
```

### 4.4 KBChunk

```python
@dataclass
class KBChunk:
    """KB 청크 모델."""
    chunk_id: str           # script_id:chapter:scene
    video_id: str
    script_id: str
    chapter_order: int
    scene_order: int
    chapter_title: str
    scene_purpose: str
    content: str            # narration + caption
    source_refs: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
```

---

## 5. 테스트 결과

### 5.1 Phase 28 테스트 (23개)

| 테스트 카테고리 | 테스트 수 | 상태 |
|---------------|----------|------|
| 발행 성공 | 2 | ✅ PASS |
| 발행 검증 | 3 | ✅ PASS |
| 만료 교육 | 2 | ✅ PASS |
| KB 청킹 | 4 | ✅ PASS |
| KB 인덱싱 | 3 | ✅ PASS |
| 이전 버전 아카이브 | 1 | ✅ PASS |
| 모델 테스트 | 8 | ✅ PASS |
| **합계** | **23** | ✅ **ALL PASS** |

### 5.2 주요 테스트 케이스

```python
# 1. 발행 성공 테스트
test_publish_with_approved_and_succeeded
  - APPROVED 스크립트 + SUCCEEDED 잡 → 발행 성공

test_publish_changes_status_to_published
  - 발행 시 status=PUBLISHED, kb_index_status=PENDING

# 2. 발행 검증 테스트
test_publish_fails_when_script_not_approved  # DRAFT → 실패
test_publish_fails_when_render_not_succeeded # PENDING → 실패
test_publish_fails_when_job_pending          # 잡 없음 → 실패

# 3. 만료 교육 테스트
test_publish_fails_when_education_expired    # EXPIRED → 404
test_publish_succeeds_when_education_active  # ACTIVE → 성공

# 4. KB 청킹 테스트
test_build_chunks_from_script_with_chapters  # chapters/scenes 파싱
test_build_chunks_from_simple_scenes         # scenes만 있는 경우
test_chunk_preserves_source_refs             # source_refs 보존
test_empty_content_scene_skipped             # 빈 씬 제외

# 5. KB 인덱싱 테스트
test_index_published_video_success           # 성공 → SUCCEEDED
test_index_fails_for_non_approved_script     # 미승인 → ValueError
test_index_returns_failed_for_empty_chunks   # 빈 청크 → FAILED
```

### 5.3 회귀 테스트

```
tests/test_phase27_video_render.py: 19 passed
tests/test_phase26_education_expired.py: 21 passed
tests/test_phase22_video_progress.py: 20 passed
tests/test_phase28_kb_indexing.py: 23 passed
────────────────────────────────────────────────
Total: 83 tests passed
```

---

## 6. 설계 결정 사항

### 6.1 적재 시점: PUBLISH 엔드포인트

**결정**: render finalize 자동 적재 대신 별도 publish 엔드포인트 사용

**이유**:
- 검토자 승인 후에만 KB에 들어가야 함
- 초안이 KB에 들어갔다가 취소되면 삭제해야 하는 문제 방지
- 명시적 발행 동작으로 의도 명확화

### 6.2 청킹 단위: 씬 단위

**결정**: MVP에서는 씬(scene) 단위로 1 chunk 생성

**이유**:
- 단순하고 예측 가능
- 복잡한 토큰 기반 청킹은 Phase 29+로 미룸
- 씬은 논리적 단위로 검색에 적합

### 6.3 버전 관리: 삭제 방식

**결정**: 재발행 시 이전 버전을 벡터DB에서 삭제

**이유**:
- 검색 제외가 확실
- metadata active=false 방식보다 단순
- MVP에서 관리 용이

### 6.4 비동기 인덱싱

**결정**: asyncio.create_task()로 백그라운드 실행

**이유**:
- 발행 API 응답 즉시 반환
- 클라이언트가 kb-status로 폴링
- 향후 Celery로 확장 가능

### 6.5 Mock 모드 지원

**결정**: Milvus 클라이언트 없으면 Mock 모드 자동 전환

**이유**:
- 테스트 환경에서 Milvus 불필요
- CI/CD 파이프라인 호환성
- 개발 편의성

---

## 7. 체크리스트

- [x] PUBLISH API 구현 (POST /api/videos/{video_id}/publish)
- [x] REVIEWER 권한 검증
- [x] EXPIRED 교육 차단 (404)
- [x] render SUCCEEDED 검증 (409)
- [x] script APPROVED 검증 (409)
- [x] KB Index Job 비동기 실행
- [x] 스크립트 → 청크 변환 (씬 단위)
- [x] 청크 메타데이터 (video_id, script_id, chapter, scene, source_refs)
- [x] 이전 버전 아카이브 로직
- [x] KB 상태 조회 API (GET /api/videos/{video_id}/kb-status)
- [x] Phase 28 테스트 23개 모두 통과
- [x] Phase 22/26/27 회귀 테스트 통과
- [x] 개발 문서 작성

---

## 8. 향후 개선 사항

### 8.1 단기
- [ ] 실제 Milvus 연동 테스트
- [ ] 토큰 기반 청킹 (긴 씬 분할)
- [ ] 임베딩 캐싱

### 8.2 중기
- [ ] Celery/Redis Queue 기반 인덱싱
- [ ] 인덱싱 진행률 웹소켓 알림
- [ ] 배치 재인덱싱 기능

### 8.3 장기
- [ ] RAG 검색에서 "교육 스크립트 근거" 표시
- [ ] 다국어 청킹 지원
- [ ] 청크 품질 검증 (중복/빈 내용 감지)

---

## 9. 사용 예시

### 9.1 영상 발행 플로우

```bash
# 1. 스크립트 생성
curl -X POST http://localhost:8000/api/scripts \
  -H "Content-Type: application/json" \
  -d '{
    "video_id": "video-001",
    "raw_json": {
      "chapters": [
        {
          "chapter_id": 1,
          "title": "보안교육",
          "scenes": [
            {"scene_id": 1, "narration": "안녕하세요."}
          ]
        }
      ]
    }
  }'

# 2. 스크립트 승인
curl -X POST http://localhost:8000/api/scripts/script-xxx/approve

# 3. 렌더 잡 생성 및 완료 대기
curl -X POST http://localhost:8000/api/videos/video-001/render-jobs \
  -d '{"script_id": "script-xxx"}'

# 4. 영상 발행 (KB 적재)
curl -X POST http://localhost:8000/api/videos/video-001/publish

# Response:
# {
#   "video_id": "video-001",
#   "script_id": "script-xxx",
#   "status": "PUBLISHED",
#   "kb_index_status": "PENDING",
#   "message": "영상 발행이 시작되었습니다. KB 인덱싱이 진행 중입니다."
# }

# 5. KB 상태 확인
curl http://localhost:8000/api/videos/video-001/kb-status

# Response:
# {
#   "video_id": "video-001",
#   "script_id": "script-xxx",
#   "kb_index_status": "SUCCEEDED",
#   "kb_indexed_at": "2025-12-18T10:35:00",
#   "kb_document_status": "ACTIVE"
# }
```

### 9.2 에러 처리 (프론트엔드)

```typescript
try {
  await publishVideo(videoId);
} catch (error) {
  const detail = error.response?.data?.detail;

  switch (detail?.reason_code) {
    case 'RENDER_NOT_SUCCEEDED':
      alert('영상 렌더링이 완료되지 않았습니다.');
      break;
    case 'SCRIPT_NOT_APPROVED':
      alert('스크립트가 승인되지 않았습니다.');
      break;
    case 'EDU_EXPIRED':
      alert('교육이 만료되어 발행할 수 없습니다.');
      break;
  }
}
```

---

## 10. 변경 파일 전체 목록

```
app/
├── api/v1/
│   └── video_render.py           # [수정] publish, kb-status 엔드포인트 추가
├── models/
│   └── video_render.py           # [수정] KBIndexStatus, KBChunk, PublishResponse 추가
└── services/
    └── kb_index_service.py       # [신규] KB 인덱싱 서비스

tests/
└── test_phase28_kb_indexing.py   # [신규] 23개 테스트

docs/
└── DEVELOPMENT_REPORT_PHASE28.md # [신규] 개발 보고서
```
