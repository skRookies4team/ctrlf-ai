# Phase 30: Internal RAG API 및 백엔드 연동

**작성일**: 2025-12-19
**Phase**: 30
**상태**: 완료
**테스트 결과**: 17 passed

---

## 1. 개요

Phase 30에서는 Spring 백엔드와 AI 서버 간의 Internal RAG API 연동을 확인하고 테스트를 보강했습니다.

### 1.1 목표

- **A) 백엔드 → AI 연동 확인**: POST /rag/documents/{id}/reprocess → AI /internal/rag/index 호출
- **B) Job 상태 추적**: queued → running → completed/failed 상태 전이
- **C) 이전 버전 즉시 삭제**: upsert 성공 후에만 delete_old_versions 실행

---

## 2. 구현 상세

### 2.1 AI 서버 엔드포인트 (이미 구현됨 - Phase 25)

#### 2.1.1 POST /internal/rag/index

문서 인덱싱을 요청합니다. 비동기로 처리되며 즉시 202 Accepted를 반환합니다.

**파일**: `app/api/v1/internal_rag.py:36-88`

```python
@router.post(
    "/rag/index",
    response_model=InternalRagIndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def index_document(request: InternalRagIndexRequest):
    # 비동기 파이프라인 실행
    # download → extract → chunk → embed → upsert → delete_old_versions
```

#### 2.1.2 POST /internal/rag/delete

Milvus에서 문서 청크를 삭제합니다.

**파일**: `app/api/v1/internal_rag.py:96-146`

```python
@router.post("/rag/delete", response_model=InternalRagDeleteResponse)
async def delete_document(request: InternalRagDeleteRequest):
    # versionNo가 있으면 해당 버전만, 없으면 전체 삭제
```

#### 2.1.3 GET /internal/jobs/{job_id}

작업 상태를 폴링합니다.

**파일**: `app/api/v1/internal_rag.py:154-195`

```python
@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    # queued, running, completed, failed 상태 반환
```

### 2.2 이전 버전 삭제 로직

**파일**: `app/services/indexing_service.py:196-198`

```python
# Step 6: 이전 버전 삭제 (새 버전 성공 후에만)
await self._job_service.update_job(job_id, progress="cleaning")
await self._milvus.delete_old_versions(document_id, version_no)
```

**삭제 조건**: `document_id == X AND version_no < current_version_no`

**보장 사항**:
- upsert 성공 후에만 delete_old_versions 실행
- upsert 실패 시 이전 버전 유지 (데이터 안전성)

### 2.3 Job 상태 전이

| 상태 | 설명 |
|------|------|
| `queued` | 작업 대기 중 (API 응답 시점) |
| `running` | 실행 중 (downloading → embedding → upserting → cleaning) |
| `completed` | 완료 (chunks_processed 포함) |
| `failed` | 실패 (error_message 포함) |

---

## 3. 환경 변수

**파일**: `app/core/config.py`

```bash
# Internal RAG API 설정
AI_INTERNAL_BASE_URL=http://ai-gateway:8000  # 백엔드에서 설정

# 재시도 설정
INDEX_RETRY_MAX_ATTEMPTS=3
INDEX_RETRY_BACKOFF_SECONDS=1,2,4

# Milvus 설정
MILVUS_ENABLED=true
MILVUS_HOST=localhost
MILVUS_PORT=19530
MILVUS_COLLECTION_NAME=ctrlf_chunks
```

---

## 4. API 사용 예시 (curl)

### 4.1 문서 인덱싱 요청

```bash
curl -X POST http://localhost:8000/internal/rag/index \
  -H "Content-Type: application/json" \
  -d '{
    "documentId": "DOC-001",
    "versionNo": 2,
    "title": "인사규정 v2.0",
    "domain": "POLICY",
    "fileUrl": "https://storage.example.com/docs/hr-policy.pdf",
    "requestedBy": "admin-001",
    "jobId": "job-uuid-1234"
  }'
```

**응답 (202 Accepted)**:
```json
{
  "jobId": "job-uuid-1234",
  "status": "queued",
  "message": "Indexing job job-uuid-1234 has been queued"
}
```

### 4.2 작업 상태 조회

```bash
curl -X GET http://localhost:8000/internal/jobs/job-uuid-1234
```

**응답 (실행 중)**:
```json
{
  "jobId": "job-uuid-1234",
  "status": "running",
  "documentId": "DOC-001",
  "versionNo": 2,
  "progress": "embedding",
  "chunksProcessed": 10,
  "createdAt": "2025-12-19T10:00:00Z",
  "updatedAt": "2025-12-19T10:00:05Z"
}
```

**응답 (완료)**:
```json
{
  "jobId": "job-uuid-1234",
  "status": "completed",
  "documentId": "DOC-001",
  "versionNo": 2,
  "progress": "completed",
  "chunksProcessed": 25,
  "createdAt": "2025-12-19T10:00:00Z",
  "updatedAt": "2025-12-19T10:00:15Z"
}
```

**응답 (실패)**:
```json
{
  "jobId": "job-uuid-1234",
  "status": "failed",
  "documentId": "DOC-001",
  "versionNo": 2,
  "progress": "embedding",
  "errorMessage": "Embedding API connection timeout",
  "createdAt": "2025-12-19T10:00:00Z",
  "updatedAt": "2025-12-19T10:00:10Z"
}
```

### 4.3 문서 삭제

```bash
# 특정 버전 삭제
curl -X POST http://localhost:8000/internal/rag/delete \
  -H "Content-Type: application/json" \
  -d '{
    "documentId": "DOC-001",
    "versionNo": 1,
    "jobId": "job-del-001"
  }'

# 모든 버전 삭제
curl -X POST http://localhost:8000/internal/rag/delete \
  -H "Content-Type: application/json" \
  -d '{
    "documentId": "DOC-001",
    "jobId": "job-del-002"
  }'
```

**응답**:
```json
{
  "jobId": "job-del-001",
  "status": "completed",
  "deletedCount": 15,
  "message": "Deleted 15 chunks for document DOC-001"
}
```

---

## 5. 테스트 케이스

**파일**: `tests/test_phase30_internal_rag.py`

| 테스트 클래스 | 테스트 수 | 설명 |
|-------------|----------|------|
| `TestDeleteOldVersions` | 4 | upsert 성공/실패 시 delete_old_versions 동작 |
| `TestJobStatusTransition` | 2 | job 상태 전이 (queued→running→completed/failed) |
| `TestJobService` | 5 | JobService 단위 테스트 |
| `TestInternalRagAPI` | 4 | API 엔드포인트 테스트 |
| `TestVersionDeletionOrder` | 2 | 버전 삭제 순서 검증 |

```bash
# 테스트 실행
python -m pytest tests/test_phase30_internal_rag.py -v

# 결과
17 passed in 38.94s
```

### 5.1 핵심 테스트 케이스

#### upsert 성공 시 delete_old_versions 호출

```python
async def test_delete_old_versions_called_on_upsert_success():
    # Given: 정상적인 upsert
    # When: 인덱싱 요청
    # Then: delete_old_versions가 호출됨
    mock_milvus_client.delete_old_versions.assert_called_once_with("DOC-001", 2)
```

#### upsert 실패 시 delete_old_versions 미호출

```python
async def test_delete_old_versions_not_called_on_upsert_failure():
    # Given: upsert 실패 설정
    mock_milvus_client.upsert_chunks = AsyncMock(side_effect=Exception("Failed"))
    # When: 인덱싱 요청
    # Then: delete_old_versions가 호출되지 않음
    mock_milvus_client.delete_old_versions.assert_not_called()
```

---

## 6. 백엔드 연동 가이드

### 6.1 Spring 백엔드 구현 체크리스트

1. **POST /rag/documents/{id}/reprocess**
   - jobId(UUID) 생성
   - 202 + jobId 반환
   - AI `/internal/rag/index` 호출 (비동기)

2. **Job 상태 폴링**
   - AI `/internal/jobs/{jobId}` 폴링
   - 완료/실패 시 백엔드 DB 업데이트

3. **DELETE /rag/documents/{id}**
   - AI `/internal/rag/delete` 호출
   - 전체 버전 삭제

### 6.2 예시 흐름

```
[백엔드]                        [AI 서버]
    |                              |
    | POST /internal/rag/index     |
    | (jobId, documentId, ...)     |
    |----------------------------->|
    |                              |
    |     202 Accepted (queued)    |
    |<-----------------------------|
    |                              |
    | (비동기 파이프라인 실행)       |
    |                              |
    | GET /internal/jobs/{jobId}   |
    |----------------------------->|
    |                              |
    |     running (embedding)      |
    |<-----------------------------|
    |                              |
    | GET /internal/jobs/{jobId}   |
    |----------------------------->|
    |                              |
    |     completed                |
    |<-----------------------------|
```

---

## 7. 변경된 파일

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `tests/test_phase30_internal_rag.py` | 신규 | Phase 30 테스트 17개 |
| `docs/DEVELOPMENT_REPORT_PHASE30.md` | 신규 | Phase 30 개발 리포트 |

**기존 구현 (Phase 25)**:
- `app/api/v1/internal_rag.py` - API 엔드포인트
- `app/services/indexing_service.py` - 인덱싱 파이프라인
- `app/services/job_service.py` - Job 상태 관리
- `app/models/internal_rag.py` - 요청/응답 모델

---

## 8. 향후 계획

1. **Redis 기반 Job Store**: 현재 인메모리 → Redis로 확장 (다중 인스턴스 지원)
2. **Webhook 콜백**: 작업 완료 시 백엔드로 콜백 (폴링 대신)
3. **Job 만료 정책**: 오래된 Job 자동 정리 (현재 1시간 보관)

---

**작성자**: Claude Code
**검토자**: -
