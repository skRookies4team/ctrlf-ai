# 영상 제작 API 변경사항 안내

> **작성일**: 2025-12-23
> **대상**: 영상 제작 담당 팀원
> **중요도**: 높음 - 반드시 확인 필요

---

## 요약

"FE↔AI 직접 통신 금지" 정책 적용으로 일부 API가 삭제되었습니다.
**변경 불가로 명시된 API 중 일부가 삭제되었으니**, 아래 내용을 확인해주세요.

---

## 1. 삭제된 파일

### `app/api/v1/scripts.py` - 완전 삭제

| 삭제된 API | 설명 |
|------------|------|
| `POST /api/scripts` | 스크립트 생성 |
| `GET /api/scripts/{script_id}` | 스크립트 조회 |
| `POST /api/videos/{video_id}/scripts/generate` | 스크립트 자동 생성 (LLM) |
| `GET /api/scripts/{script_id}/editor` | 편집용 뷰 조회 |
| `PATCH /api/scripts/{script_id}/editor` | 씬 부분 수정 |

**이유**: FE가 직접 AI를 호출하지 않고 백엔드 경유 필수 정책

---

## 2. render_jobs.py 변경사항

### 삭제된 API (변경 불가로 명시되었던 것들)

| 삭제된 API | 문서에서의 상태 | 비고 |
|------------|----------------|------|
| `POST /api/v2/videos/{video_id}/render-jobs` | 변경 불가 X | FE용 삭제 |
| `GET /api/v2/videos/{video_id}/render-jobs` | 변경 불가 X | FE용 삭제 |
| `GET /api/v2/videos/{video_id}/render-jobs/{job_id}` | 변경 불가 X | FE용 삭제 |
| `POST /api/v2/videos/{video_id}/render-jobs/{job_id}/cancel` | 변경 불가 X | FE용 삭제 |
| `GET /api/v2/videos/{video_id}/assets/published` | 변경 불가 X | FE용 삭제 |

### 유지된 API (Backend → AI)

| API | 상태 |
|-----|------|
| `POST /ai/video/job/{job_id}/start` | **유지됨** ✅ |
| `POST /ai/video/job/{job_id}/retry` | **유지됨** ✅ |

### 새로 추가된 API

| API | 설명 |
|-----|------|
| `POST /internal/ai/render-jobs` | 백엔드가 호출하는 내부 API (X-Internal-Token 필요) |

---

## 3. 현재 render_jobs.py 구조

```
render_jobs.py
├── internal_router (/internal/ai)
│   └── POST /render-jobs          # 새로 추가 (Backend → AI)
│
└── ai_router (/ai/video/job)      # 기존 유지
    ├── POST /{job_id}/start       # 유지 ✅
    └── POST /{job_id}/retry       # 유지 ✅
```

---

## 4. 영향받는 테스트 파일

다음 테스트들은 `@pytest.mark.skip` 처리됨:

| 파일 | skip된 클래스/함수 |
|------|-------------------|
| `test_phase31_script_generation.py` | TestScriptGenerateAPI, TestManualScriptAPI, TestE2EScriptGenerationFlow |
| `test_phase32_video_rendering.py` | TestRegression |
| `test_phase33_render_job_ops.py` | TestRenderJobAPI, test_regression_existing_api |

---

## 5. source_set.py 모델 변경

`GeneratedScene`과 `GeneratedChapter`에서 ID 필드 제거됨:

```python
# 삭제된 필드
- scene_id: str       # GeneratedScene에서 삭제
- chapter_id: str     # GeneratedChapter에서 삭제

# 유지된 필드 (0-based index)
scene_index: int      # 0, 1, 2, ...
chapter_index: int    # 0, 1, 2, ...
```

**이유**: 백엔드 JPA가 `@GeneratedValue`로 ID 자동 생성

---

## 6. 팀원 조치사항

### 케이스 A: `/api/v2/*` API가 필요한 경우

**백엔드와 협의 필요**
FE가 직접 호출하던 API들이 삭제되었으므로:
1. 백엔드에 해당 기능 구현 요청
2. 또는 internal API로 백엔드 경유하도록 플로우 변경

### 케이스 B: scripts.py 기능이 필요한 경우

**SourceSet 오케스트레이션 사용**
스크립트 생성은 이제 `POST /internal/ai/source-sets/{sourceSetId}/start`를 통해 처리됩니다.

### 케이스 C: 기존 코드가 삭제된 API를 호출하는 경우

git에서 이전 버전 확인:
```bash
# 삭제 전 render_jobs.py 확인
git show f09e11e:app/api/v1/render_jobs.py

# 삭제 전 scripts.py 확인
git show f09e11e:app/api/v1/scripts.py
```

---

## 7. 유지되는 핵심 서비스 (변경 없음)

다음 파일들은 **변경되지 않았습니다**:

| 파일 | 역할 |
|------|------|
| `video_script_generation_service.py` | LLM 스크립트 생성 로직 |
| `render_job_runner.py` | 렌더 잡 오케스트레이터 |
| `video_renderer_real.py` | 7-Step 렌더링 파이프라인 |
| `video_composer.py` | FFmpeg 영상 합성 |
| `tts_provider.py` | TTS 제공자 |
| `storage_adapter.py` | 스토리지 어댑터 |
| `backend_client.py` | 백엔드 통신 클라이언트 |

---

## 8. 변경 커밋 히스토리

```
9626929 docs: 채팅 파이프라인 아키텍처 문서 업데이트
8e3fd7c refactor: 서비스 코드 개선
ba86fc1 test: rule_router Phase 43 RAG 우선 정책 테스트 수정
d811f84 test: 제거된 FE API 관련 테스트 skip 처리
8fd98dd feat: render-jobs internal API에 X-Internal-Token 인증 추가
ac765fd refactor: FE용 scripts API 제거
6c393e1 feat: SourceSet 콜백 스펙 정렬 - scene_id/chapter_id 제거
```

이전 상태로 복구하려면:
```bash
git revert <commit-hash>
```

---

## 질문/문의

변경사항 관련 문의사항이 있으면 언제든 연락주세요.
