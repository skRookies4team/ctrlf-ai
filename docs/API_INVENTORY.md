# API 인벤토리

생성일: 2025-12-19

## 분류 기준

| 라벨 | 정의 |
|------|------|
| **KEEP** | 현재 플로우에서 실제 호출됨 (테스트/CLI/FE/BE 연동 확인) |
| **DEPRECATE** | 기능 중복, 대체 API 존재 |
| **DELETE** | 호출처 없음, 레거시, 테스트만 존재 |
| **REMOVED** | Phase 42에서 제거됨 (410 Gone 반환) |

---

## 1. 헬스체크 (health.py)

| Method | Path | 라벨 | 담당 서비스 | 호출 주체 | 근거 |
|--------|------|------|-------------|-----------|------|
| GET | /health | **KEEP** | - | K8s/LB | 헬스체크 필수 |
| GET | /health/ready | **KEEP** | - | K8s/LB | Readiness 필수 |

---

## 2. 채팅 (chat.py, chat_stream.py)

| Method | Path | 라벨 | 담당 서비스 | 호출 주체 | 근거 |
|--------|------|------|-------------|-----------|------|
| POST | /ai/chat/messages | **KEEP** | ChatService | FE/BE | 핵심 채팅 API, chat_cli.py 사용 |
| POST | /ai/chat/stream | **KEEP** | ChatStreamService | BE→SSE | 스트리밍 채팅, BE 연동 |

---

## 3. RAG/인덱싱 (rag.py, ingest.py, search.py, internal_rag.py)

| Method | Path | 라벨 | 담당 서비스 | 호출 주체 | 근거 |
|--------|------|------|-------------|-----------|------|
| POST | /ai/rag/process | **DELETE** | RagService | - | Phase 25 이후 internal_rag로 대체 |
| POST | /ingest | **DELETE** | IngestService | - | RAGFlow 기반, internal_rag로 대체 |
| POST | /search | **DELETE** | SearchService | - | RAGFlow 기반, Milvus 직접 검색으로 대체 |
| POST | /internal/rag/index | **REMOVED** | - | - | Phase 42에서 제거됨 (410 Gone), RAGFlow 경유로 대체 |
| POST | /internal/rag/delete | **REMOVED** | - | - | Phase 42에서 제거됨 (410 Gone), RAGFlow 경유로 대체 |
| GET | /internal/jobs/{job_id} | **REMOVED** | - | - | Phase 42에서 제거됨 (410 Gone) |

**삭제 근거:**
- `/ai/rag/process`: RagflowClient 사용, MILVUS_ENABLED=true 환경에서 미사용
- `/ingest`: RAGFlow 파이프라인, internal_rag가 Milvus 직접 처리
- `/search`: ChatService가 MilvusClient.search_as_sources() 직접 호출

---

## 4. 영상 진행률 (video.py - Phase 22/26)

| Method | Path | 라벨 | 담당 서비스 | 호출 주체 | 근거 |
|--------|------|------|-------------|-----------|------|
| POST | /api/video/play/start | **KEEP** | VideoProgressService | FE | 영상 재생 시작 |
| POST | /api/video/progress | **KEEP** | VideoProgressService | FE | 진행률 업데이트 |
| POST | /api/video/complete | **KEEP** | VideoProgressService | FE | 완료 처리 |
| GET | /api/video/status | **KEEP** | VideoProgressService | FE | 상태 조회 |
| GET | /api/video/quiz/check | **KEEP** | VideoProgressService | FE | 퀴즈 잠금 확인 |

---

## 5. 관리자 API (admin.py - Phase 26)

| Method | Path | 라벨 | 담당 서비스 | 호출 주체 | 근거 |
|--------|------|------|-------------|-----------|------|
| POST | /api/admin/education/reissue | **KEEP** | EducationCatalogService | Admin | 교육 재발행 |
| GET | /api/admin/education/{id} | **KEEP** | EducationCatalogService | Admin | 메타 조회 |

---

## 6. 영상 생성 V1 (video_render.py - Phase 27/28/31/38)

| Method | Path | 라벨 | 담당 서비스 | 호출 주체 | 근거 |
|--------|------|------|-------------|-----------|------|
| POST | /api/scripts | **KEEP** | VideoRenderService | BE | 스크립트 생성 |
| POST | /api/scripts/{id}/approve | **KEEP** | VideoRenderService | Reviewer | 승인 |
| GET | /api/scripts/{id} | **KEEP** | VideoRenderService | FE/BE | 조회 |
| POST | /api/videos/{id}/scripts/generate | **KEEP** | ScriptGenerationService | BE | Phase 31 자동생성 |
| POST | /api/videos/{id}/render-jobs | **DEPRECATE** | VideoRenderService | - | V2로 대체 |
| GET | /api/render-jobs/{id} | **KEEP** | VideoRenderService | FE/BE | 상태 조회 |
| POST | /api/render-jobs/{id}/start | **KEEP** | RenderJobRunner | BE | Phase 38 잡 시작 |
| POST | /api/render-jobs/{id}/retry | **KEEP** | RenderJobRunner | BE | Phase 38 재시도 |
| POST | /api/render-jobs/{id}/cancel | **DEPRECATE** | VideoRenderService | - | V2로 대체 |
| GET | /api/videos/{id}/asset | **KEEP** | VideoRenderService | FE | 에셋 조회 |
| POST | /api/videos/{id}/publish | **KEEP** | VideoRenderService+KB | Reviewer | Phase 28 발행 |
| GET | /api/videos/{id}/kb-status | **KEEP** | KBIndexService | FE/BE | KB 상태 |

---

## 7. 영상 생성 V2 (video_render_phase33.py - Phase 33)

| Method | Path | 라벨 | 담당 서비스 | 호출 주체 | 근거 |
|--------|------|------|-------------|-----------|------|
| POST | /api/v2/videos/{id}/render-jobs | **KEEP** | RenderJobRunner | FE/BE | Idempotent 생성 |
| GET | /api/v2/videos/{id}/render-jobs | **KEEP** | RenderJobRunner | FE | 목록 조회 |
| GET | /api/v2/videos/{id}/render-jobs/{job_id} | **KEEP** | RenderJobRunner | FE | 상세 조회 |
| POST | /api/v2/videos/{id}/render-jobs/{job_id}/cancel | **KEEP** | RenderJobRunner | FE | 취소 |
| GET | /api/v2/videos/{id}/assets/published | **KEEP** | RenderJobRunner | FE | 발행 에셋 |

---

## 8. WebSocket (ws_render_progress.py - Phase 32)

| Method | Path | 라벨 | 담당 서비스 | 호출 주체 | 근거 |
|--------|------|------|-------------|-----------|------|
| WS | /ws/videos/{id}/render-progress | **KEEP** | ConnectionManager | FE | 실시간 진행률 |

---

## 9. 스크립트 편집 (script_editor.py - Phase 42)

| Method | Path | 라벨 | 담당 서비스 | 호출 주체 | 근거 |
|--------|------|------|-------------|-----------|------|
| GET | /api/scripts/{id}/editor | **KEEP** | VideoRenderService | FE | 편집 뷰 |
| PATCH | /api/scripts/{id}/editor | **KEEP** | VideoRenderService | FE | 씬 편집 |

---

## 10. 부가 기능 (gap_suggestions.py, quiz_generate.py, faq.py)

| Method | Path | 라벨 | 담당 서비스 | 호출 주체 | 근거 |
|--------|------|------|-------------|-----------|------|
| POST | /ai/gap/policy-edu/suggestions | **KEEP** | GapSuggestionService | Admin/BE | Gap 분석 |
| POST | /ai/quiz/generate | **KEEP** | QuizGenerateService | BE | 퀴즈 생성 |
| POST | /ai/faq/generate | **KEEP** | FaqDraftService | BE | FAQ 단건 |
| POST | /ai/faq/generate/batch | **KEEP** | FaqDraftService | BE | FAQ 배치 |

---

## 삭제 대상 요약

| Path | 파일 | 대체 API | 삭제 근거 |
|------|------|----------|-----------|
| POST /ai/rag/process | rag.py | SourceSet Orchestrator | RagflowClient 레거시, Milvus 전환 |
| POST /ingest | ingest.py | SourceSet Orchestrator | RAGFlow 인덱싱 레거시 |
| POST /search | search.py | ChatService 내부 | RAGFlow 검색 레거시 |

## REMOVED 대상 요약 (Phase 42)

| Path | 상태 | 대체 경로 |
|------|------|-----------|
| POST /internal/rag/index | 410 Gone | SourceSet Orchestrator → RAGFlow |
| POST /internal/rag/delete | 410 Gone | SourceSet Orchestrator → RAGFlow |
| GET /internal/jobs/{job_id} | 410 Gone | SourceSet Orchestrator 내부 관리 |

## DEPRECATE 대상 요약

| Path | 대체 API | 처리 방안 |
|------|----------|-----------|
| POST /api/videos/{id}/render-jobs (V1) | POST /api/v2/.../render-jobs | 응답에 deprecation 헤더 추가 |
| POST /api/render-jobs/{id}/cancel (V1) | POST /api/v2/.../cancel | 응답에 deprecation 헤더 추가 |

---

## 다음 단계

1. DELETE 대상 3개 제거: rag.py, ingest.py, search.py
2. 연관 서비스/모델/테스트 정리
3. DEPRECATE 대상에 헤더 추가
4. 스모크 테스트 실행
