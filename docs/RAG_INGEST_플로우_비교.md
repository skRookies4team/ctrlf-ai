# RAG Ingest 플로우 비교

## 개요

RAGFlow에 문서를 임베딩하는 두 가지 플로우가 존재합니다.

| 구분              | 사규 업로드 플로우                                      | 소스셋 플로우                                  |
| ----------------- | ------------------------------------------------------- | ---------------------------------------------- |
| 용도              | 사내규정 문서 임베딩                                    | 영상 관련 문서 임베딩                          |
| 백엔드 클라이언트 | `RagAiClient.java`                                      | `SourceSetAiClient.java`                       |
| AI 엔드포인트     | `/v1/internal_ragflow/internal/ai/rag-documents/ingest` | `/internal/ai/source-sets/{sourceSetId}/start` |
| 콜백 처리         | `backend_client.update_rag_document_status`             | `source_set_orchestrator`                      |

### 소스셋 플로우 -> ragflow_client.py

### 사규 플로우 -> ragflow_ingest_client.py

백엔드 → AI: documentId=POL-xxx, title=새 사규/정책
AI → RAGFlow: docId=새 사규/정책 (title을 docId로 사용)

---

## 1. 사규 업로드 플로우

### 1.1 시퀀스 다이어그램

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ Frontend │     │ Backend  │     │ AI Server│     │ RAGFlow  │
└────┬─────┘     └────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │                │
     │ 1. 사규 업로드  │                │                │
     │───────────────>│                │                │
     │                │                │                │
     │                │ 2. POST /v1/internal_ragflow/internal/ai/rag-documents/ingest
     │                │───────────────>│                │
     │                │                │                │
     │                │  202 Accepted  │                │
     │                │<───────────────│                │
     │                │                │                │
     │                │                │ 3. POST /v1/internal_ragflow/internal/ragflow/ingest
     │                │                │───────────────>│
     │                │                │                │
     │                │                │  202 Accepted  │
     │                │                │<───────────────│
     │                │                │                │
     │                │                │ 4. 콜백 (완료/실패)
     │                │                │<───────────────│
     │                │                │                │
     │                │ 5. PATCH /internal/rag/documents/{pk}/status
     │                │<───────────────│                │
     │                │                │                │
```

### 1.2 요청/응답 규격

#### Backend → AI Server

**파일**: `ctrlf-back/infra-service/.../RagAiClient.java`

```java
// 엔드포인트
POST /v1/internal_ragflow/internal/ai/rag-documents/ingest

// 요청 Body
{
    "ragDocumentPk": "UUID",      // RAG 문서 PK
    "documentId": "POL-xxx",      // 사규 문서 ID (백엔드 DB 기준)
    "version": 1,                 // 문서 버전
    "sourceUrl": "s3://...",      // 원본 파일 URL
    "domain": "사내규정",          // 도메인
    "requestId": "UUID",          // 요청 ID
    "traceId": "trace-xxx",       // 추적 ID
    "department": "총무팀",        // (선택) 부서
    "title": "보안관리규정.pdf"    // (선택) 문서 제목 → RAGFlow docId로 사용
}
```

#### AI Server → RAGFlow

**파일**: `ctrlf-ai/app/clients/ragflow_ingest_client.py`

```python
# 엔드포인트
POST {RAGFLOW_BASE_URL}/v1/internal_ragflow/internal/ragflow/ingest

# 요청 Body
{
    "datasetId": "사내규정",       # RAGFlow dataset 이름
    "docId": "보안관리규정.pdf",   # title을 docId로 사용 ⚠️
    "version": 1,
    "fileUrl": "s3://...",
    "replace": true,
    "department": "총무팀",
    "meta": {
        "ragDocumentPk": "UUID",
        "domain": "사내규정",
        "traceId": "trace-xxx",
        "requestId": "UUID"
        # ⚠️ 원래 documentId가 포함되지 않음!
    }
}
```

#### RAGFlow → AI Server (콜백)

```python
# 엔드포인트
POST /v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest

# 요청 Body
{
    "ingestId": "UUID",
    "docId": "보안관리규정.pdf",   # title 값
    "version": 1,
    "status": "COMPLETED" | "FAILED",
    "processedAt": "2024-01-10T12:00:00Z",
    "failReason": null,
    "meta": {
        "ragDocumentPk": "UUID",
        "traceId": "trace-xxx",
        "requestId": "UUID",
        "domain": "사내규정",
        "source_set_id": null,
        "spring_document_id": null  # ⚠️ null!
    }
}
```

#### AI Server → Backend (상태 업데이트)

```python
# 엔드포인트
PATCH /internal/rag/documents/{ragDocumentPk}/status

# 요청 Body
{
    "status": "COMPLETED" | "FAILED",
    "documentId": "보안관리규정.pdf",  # ⚠️ title 값이 전달됨 (문제!)
    "version": 1,
    "processedAt": "2024-01-10T12:00:00Z",
    "failReason": null,
    "content": "..."
}
```

### 1.3 문제점

백엔드가 `documentId=POL-xxx`를 기대하는데, AI 서버가 `documentId=보안관리규정.pdf` (title)를 전달하여 **DocumentId mismatch** 에러 발생.

---

## 2. 소스셋 플로우

### 2.1 시퀀스 다이어그램

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ Frontend │     │ Backend  │     │ AI Server│     │ RAGFlow  │
└────┬─────┘     └────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │                │
     │ 1. 영상 업로드  │                │                │
     │───────────────>│                │                │
     │                │                │                │
     │                │ 2. POST /internal/ai/source-sets/{id}/start
     │                │───────────────>│                │
     │                │                │                │
     │                │  200 OK        │                │
     │                │<───────────────│                │
     │                │                │                │
     │                │                │ 3. 각 문서별 RAGFlow ingest
     │                │                │───────────────>│
     │                │                │                │
     │                │                │ 4. 콜백 (완료/실패)
     │                │                │<───────────────│
     │                │                │                │
     │                │                │ 5. 모든 문서 완료 시
     │                │                │    스크립트 생성 + 콜백
     │                │<───────────────│                │
     │                │                │                │
```

### 2.2 요청/응답 규격

#### Backend → AI Server

**파일**: `ctrlf-back/education-service/.../SourceSetAiClient.java`

```java
// 엔드포인트
POST /internal/ai/source-sets/{sourceSetId}/start

// 요청 Body (StartRequest)
{
    "videoId": "UUID",
    "scriptJobId": "UUID",
    "documents": [
        {
            "documentId": "doc-001",      // Spring 문서 ID
            "title": "영상스크립트.txt",
            "fileUrl": "s3://...",
            "domain": "영상"
        }
    ],
    "callbackUrl": "http://backend/callback"
}
```

#### AI Server 내부 처리

- `source_set_orchestrator`가 각 문서를 개별적으로 RAGFlow에 ingest
- `meta.source_set_id`와 `meta.spring_document_id`를 설정하여 추적

#### RAGFlow → AI Server (콜백)

```python
{
    "ingestId": "UUID",
    "docId": "영상스크립트.txt",
    "version": 1,
    "status": "COMPLETED",
    "meta": {
        "ragDocumentPk": "UUID",
        "source_set_id": "source-set-001",      # ✅ 설정됨
        "spring_document_id": "doc-001"          # ✅ 설정됨
    }
}
```

### 2.3 차이점

소스셋 플로우에서는 `source_set_orchestrator`가 별도로 관리하며:

- `meta.spring_document_id`를 명시적으로 설정
- 백엔드 RAG document 엔티티를 직접 업데이트하지 않음
- 모든 문서 완료 시 별도 콜백으로 처리

---

## 3. 문제 해결 방안

### 3.1 수정 필요 파일

1. **`ragflow_ingest_client.py`**: meta에 원래 documentId 추가

```python
"meta": {
    "ragDocumentPk": rag_document_pk,
    "domain": domain,
    "traceId": trace_id,
    "requestId": request_id,
    "spring_document_id": original_document_id,  # ✅ 추가
}
```

2. **`rag_documents.py`**: ingest 함수에 original_document_id 파라미터 추가

3. **`rag_documents.py`**: 콜백 처리 시 spring_document_id 우선 사용

```python
# 기존
document_id=request.docId

# 수정
document_id=request.meta.spring_document_id or request.docId
```

---

## 4. 참고 파일 위치

| 구분                             | 파일 경로                                                                                            |
| -------------------------------- | ---------------------------------------------------------------------------------------------------- |
| 사규 백엔드 클라이언트           | `ctrlf-back/infra-service/src/main/java/com/ctrlf/infra/rag/client/RagAiClient.java`                 |
| 소스셋 백엔드 클라이언트         | `ctrlf-back/education-service/src/main/java/com/ctrlf/education/video/client/SourceSetAiClient.java` |
| AI 서버 엔드포인트               | `ctrlf-ai/app/api/v1/rag_documents.py`                                                               |
| RAGFlow Ingest 클라이언트        | `ctrlf-ai/app/clients/ragflow_ingest_client.py`                                                      |
| Backend 상태 업데이트 클라이언트 | `ctrlf-ai/app/clients/backend_client.py`                                                             |
| 소스셋 오케스트레이터            | `ctrlf-ai/app/services/source_set_orchestrator.py`                                                   |

{'ragDocumentPk': 'b72180b9-3468-411f-88fd-113133f8d6b6', 'documentId': 'POL-20260110175237', 'version': 1, 'sourceUrl': 'https://ctrl-s3.s3.ap-northeast-2.amazonaws.com/docs/4b581b40-1231-4c5f-bf1b-808ca0c92b79?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20260110T085312Z&X-Amz-SignedHeaders=host&X-Amz-Credential=AKIA4PYYC6WQY734V4Z7%2F20260110%2Fap-northeast-2%2Fs3%2Faws4_request&X-Amz-Expires=43200&X-Amz-Signature=a1110d97e2d3ad12c256d3091f431ccc0cd318e46ab2220506df00ad0a0d9698', 'domain': '사내규정', 'requestId': 'eeba935e-14d1-4921-84fd-bdaca5e80060', 'traceId': 'trace-b72180b9', 'title': '새 사규/정책', 'department': None}","trace_id":null,"user_id":null,"dept_id":null,"conversation_id":null,"turn_id":null}
