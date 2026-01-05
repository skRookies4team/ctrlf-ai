# RAGFlow → AI 콜백 API 명세

## 개요

RAGFlow에서 문서 처리(전처리 + Milvus 적재) 완료/실패 시 AI 서버로 결과를 전달하는 콜백 API입니다.

---

## API 정보

| 항목 | 값 |
|------|-----|
| **URL** | `POST /v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest` |
| **Host** | AI 서버 (예: `http://ai-server:8000`) |
| **Content-Type** | `application/json` |
| **인증** | `X-Internal-Token` 헤더 |

---

## 인증

| 헤더 | 값 | 필수 |
|------|-----|------|
| `X-Internal-Token` | RAGFlow-AI 간 공유 토큰 | O |

```
X-Internal-Token: {RAGFLOW_CALLBACK_TOKEN}
```

> 토큰 값은 배포 시 환경변수로 설정됩니다.

---

## 요청 (Request)

### Body

```json
{
  "ingestId": "ingest-uuid-1234",
  "docId": "DOC-001",
  "version": 1,
  "status": "COMPLETED",
  "processedAt": "2025-01-05T10:30:00Z",
  "failReason": null,
  "meta": {
    "ragDocumentPk": "rag-doc-uuid",
    "traceId": "trace-uuid",
    "requestId": "request-uuid"
  },
  "stats": {
    "chunks": 15
  }
}
```

### 필드 설명

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `ingestId` | string | O | RAGFlow에서 생성한 ingest 작업 ID |
| `docId` | string | O | 문서 ID (AI에서 전달한 값 그대로) |
| `version` | int | O | 문서 버전 |
| `status` | string | O | 처리 결과: `COMPLETED` 또는 `FAILED` |
| `processedAt` | string | O | 처리 완료 시간 (ISO-8601 형식) |
| `failReason` | string | X | 실패 사유 (실패 시 필수) |
| `meta` | object | O | 메타데이터 |
| `meta.ragDocumentPk` | string | O | RAG 문서 PK (AI에서 전달한 값) |
| `meta.traceId` | string | O | 추적 ID |
| `meta.requestId` | string | O | 요청 ID |
| `stats` | object | X | 처리 통계 (성공 시) |
| `stats.chunks` | int | X | 생성된 청크 수 |

---

## 실패 사유 (failReason)

`status`가 `FAILED`인 경우, `failReason` 필드에 실패 원인을 명시해주세요.

### 예상되는 실패 유형

| 실패 유형 | failReason 예시 |
|----------|----------------|
| 문서 전처리 실패 | `PREPROCESSING_FAILED: 문서 파싱 중 오류 발생` |
| Milvus 적재 실패 | `MILVUS_UPLOAD_FAILED: 벡터 저장소 연결 실패` |
| 파일 다운로드 실패 | `FILE_DOWNLOAD_FAILED: S3 URL 접근 불가` |
| 지원하지 않는 형식 | `UNSUPPORTED_FORMAT: 지원하지 않는 파일 형식` |
| 파일 손상 | `CORRUPTED_FILE: 파일이 손상되었습니다` |
| 타임아웃 | `TIMEOUT: 처리 시간 초과` |

### 형식 권장

```
{ERROR_CODE}: {상세 메시지}
```

예시:
- `PREPROCESSING_FAILED: PDF 파싱 중 페이지 3에서 오류 발생`
- `MILVUS_UPLOAD_FAILED: 연결 타임아웃 (30초 초과)`

---

## 응답 (Response)

### 성공 (200 OK)

```json
{
  "received": true
}
```

### 인증 실패 (401 Unauthorized)

```json
{
  "error": "UNAUTHORIZED",
  "message": "X-Internal-Token 헤더가 필요합니다.",
  "traceId": "trace-uuid"
}
```

---

## 요청 예시

### 성공 케이스

```bash
curl -X POST "http://ai-server:8000/v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: your-ragflow-callback-token" \
  -d '{
    "ingestId": "ingest-uuid-1234",
    "docId": "DOC-001",
    "version": 1,
    "status": "COMPLETED",
    "processedAt": "2025-01-05T10:30:00Z",
    "failReason": null,
    "meta": {
      "ragDocumentPk": "rag-doc-uuid",
      "traceId": "trace-uuid",
      "requestId": "request-uuid"
    },
    "stats": {
      "chunks": 15
    }
  }'
```

### 실패 케이스 (문서 전처리 실패)

```bash
curl -X POST "http://ai-server:8000/v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: your-ragflow-callback-token" \
  -d '{
    "ingestId": "ingest-uuid-5678",
    "docId": "DOC-002",
    "version": 1,
    "status": "FAILED",
    "processedAt": "2025-01-05T10:35:00Z",
    "failReason": "PREPROCESSING_FAILED: PDF 파싱 중 오류 발생 - 페이지 5 손상",
    "meta": {
      "ragDocumentPk": "rag-doc-uuid-2",
      "traceId": "trace-uuid-2",
      "requestId": "request-uuid-2"
    },
    "stats": null
  }'
```

### 실패 케이스 (Milvus 적재 실패)

```bash
curl -X POST "http://ai-server:8000/v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: your-ragflow-callback-token" \
  -d '{
    "ingestId": "ingest-uuid-9999",
    "docId": "DOC-003",
    "version": 1,
    "status": "FAILED",
    "processedAt": "2025-01-05T10:40:00Z",
    "failReason": "MILVUS_UPLOAD_FAILED: 벡터 저장소 연결 타임아웃",
    "meta": {
      "ragDocumentPk": "rag-doc-uuid-3",
      "traceId": "trace-uuid-3",
      "requestId": "request-uuid-3"
    },
    "stats": null
  }'
```

---

## 호출 시점

RAGFlow에서 다음 시점에 이 API를 호출해주세요:

1. **문서 전처리 완료** → `status: "COMPLETED"`
2. **문서 전처리 실패** → `status: "FAILED"`, `failReason: "PREPROCESSING_FAILED: ..."`
3. **Milvus 적재 실패** → `status: "FAILED"`, `failReason: "MILVUS_UPLOAD_FAILED: ..."`

---

## 흐름도

```
AI 서버                         RAGFlow
   │                               │
   │ POST /ingest                  │
   │ (docId, fileUrl, meta)        │
   ├──────────────────────────────>│
   │                               │
   │       202 Accepted            │
   │<──────────────────────────────┤
   │                               │
   │                               │ (문서 처리 중...)
   │                               │
   │                               │ 완료/실패
   │                               │
   │ POST /callbacks/ragflow/ingest│
   │ (status, failReason)          │
   │<──────────────────────────────┤
   │                               │
   │       200 OK                  │
   ├──────────────────────────────>│
   │                               │
```

---

## 참고사항

- AI 서버는 콜백을 받으면 Spring 백엔드에 상태를 전달합니다.
- 콜백 호출이 실패해도 RAGFlow 측에서 재시도할 필요 없습니다 (AI 서버에서 polling으로 백업).
- `meta` 필드의 값들은 AI에서 ingest 요청 시 전달한 값을 그대로 반환해주세요.

---

## 문의

API 관련 문의사항이 있으시면 AI 팀에 연락해주세요.
