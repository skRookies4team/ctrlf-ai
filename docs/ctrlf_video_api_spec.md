# CTRL+F AI — 문서→RAGFlow→Milvus→스크립트→렌더→게시 **전체 API 명세서**

> 기준 플로우  
> **문서 업로드 → (백) S3 저장 + RAGFlow 적재 요청 → (RAGFlow) 전처리/임베딩 후 Milvus 저장 → (RAGFlow→백) 적재 완료 콜백(milvusRef 포함) → (백→AI) 스크립트 생성 Job → (AI→백) 스크립트 생성 완료 콜백 → (관리자) 1차 승인 → (백→AI) 렌더 Job → (AI→백) 렌더 완료 콜백 → (검토자) 2차 최종 승인/게시**

---

## 전체 상태 모델(공통)

### 문서(Document) 상태
- `UPLOADED` → `INGESTING` → `INGESTED`(성공) / `INGEST_FAILED`(실패)

### 스크립트(Script) 상태
- `DRAFT`(AI 생성 직후) → `SCRIPT_SUBMITTED`(관리자 검토 요청)  
- `SCRIPT_APPROVED`(1차 승인) / `SCRIPT_REJECTED`(반려)

### 렌더(Render Job) 상태
- `RENDERING` → `COMPLETED` / `FAILED`

### 영상(Video) 상태
- `DRAFT` → `RENDERED`(job 완료) → `PUBLISHED`(2차 최종 승인) / `FINAL_REJECTED`

---

# 0) (외부) 문서 업로드 등록

카테고리 : 문서  
method : POST  
URL : `/documents`  
사용자 : 일반 직원 / 관리자 / 제작자  
요청자 : 프론트  
응답자 : 백엔드  

## 📘 문서 업로드 등록

### 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /documents` |
| **설명** | 업로드된 파일(S3 경로)을 문서로 등록하고, RAGFlow 적재를 트리거하기 위한 문서ID 발급 |
| **호출 주체** | Frontend → Backend |
| **권한** | 로그인 사용자 |
| **인증** | `Authorization: Bearer <token>` |

---

### 2. 상세 설명

- 파일 업로드는 서비스 정책에 따라 **프론트 presigned 업로드** 또는 **백 업로드 대행** 중 하나로 처리 가능하나, 이 API는 **“최종 S3 fileUrl이 확보된 상태”**를 전제로 문서 레코드를 생성한다.
- 백엔드는 문서 생성 직후 내부적으로 **RAGFlow 적재 요청(내부 API 호출)** 을 수행한다.

---

### 3. Request

#### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `Content-Type` | `application/json` | ✅ |
| `Authorization` | `Bearer <token>` | ✅ |

#### Body

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `title` | 문서 제목 | `string` | false |
| `domain` | 문서 도메인 | `string` | false |
| `fileUrl` | 업로드된 파일 S3 경로 | `string` | false |
| `uploadedBy` | 업로더 ID(감사로그) | `string` | true *(토큰에서 추출 가능)* |

#### Example

```json
{
  "title": "직장내괴롭힘 교육자료(2025)",
  "domain": "FOUR_MANDATORY",
  "fileUrl": "s3://bucket/docs/DOC-001.pdf",
  "uploadedBy": "U-EMP-001"
}
```

---

### 4. Response (201 Created)

```json
{
  "documentId": "DOC-001",
  "status": "UPLOADED",
  "fileUrl": "s3://bucket/docs/DOC-001.pdf"
}
```

---

### 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `201` | 생성 성공 |
| `400/422` | 파라미터 오류 |
| `401/403` | 인증/권한 오류 |
| `500` | 생성 실패 |

---

# 1) (내부) RAGFlow 적재 요청

카테고리 : RAG(적재)  
method : POST  
URL : `/internal/ragflow/ingests`  
사용자 : 시스템(내부)  
요청자 : 백엔드  
응답자 : RAGFlow  

## 📘 RAGFlow 적재 요청

### 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /internal/ragflow/ingests` |
| **설명** | 문서를 RAGFlow로 적재 요청(전처리/청킹/임베딩/밀버스 저장) |
| **호출 주체** | Backend → RAGFlow |
| **권한** | 내부 호출 전용 |
| **인증** | `X-Internal-Token` 필수 |

---

### 2. 상세 설명

- 백엔드는 문서 등록 후 상태를 `INGESTING`으로 바꾸고, RAGFlow에 적재를 요청한다.
- RAGFlow는 작업을 비동기로 처리하고, 완료 시 **콜백**으로 결과를 백엔드에 전달한다.

---

### 3. Request

#### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `Content-Type` | `application/json` | ✅ |
| `X-Internal-Token` | `<token>` | ✅ |

#### Body

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `ingestId` | 적재 Job ID(백 발급) | `string(uuid)` | false |
| `documentId` | 문서 ID | `string` | false |
| `datasetId` | RAGFlow dataset 식별자 | `string` | false |
| `fileUrl` | S3 경로 | `string` | false |
| `indexVersion` | 인덱스 버전(정수) | `number` | false |
| `requestId` | 멱등 키(권장) | `string(uuid)` | true |

#### Example

```json
{
  "ingestId": "uuid",
  "documentId": "DOC-001",
  "datasetId": "DS-POLICY-01",
  "fileUrl": "s3://bucket/docs/DOC-001.pdf",
  "indexVersion": 1,
  "requestId": "uuid"
}
```

---

### 4. Response (202 Accepted)

```json
{
  "received": true,
  "ingestId": "uuid",
  "status": "INGESTING"
}
```

---

### 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `202` | 요청 접수(비동기) |
| `401/403` | 내부 토큰 오류 |
| `409` | 중복 ingestId 등 충돌 |
| `500` | 요청 실패 |

---

# 2) (내부) RAGFlow 적재 완료 콜백

카테고리 : RAG(적재)  
method : POST  
URL : `/internal/callbacks/ragflow/ingests/{ingestId}/complete`  
사용자 : 시스템(내부)  
요청자 : RAGFlow  
응답자 : 백엔드  

## 📘 RAGFlow 적재 완료 콜백

### 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /internal/callbacks/ragflow/ingests/{ingestId}/complete` |
| **설명** | 적재 완료/실패 결과 + “Milvus 조회에 필요한 참조키(milvusRef)” 전달 |
| **호출 주체** | RAGFlow → Backend |
| **권한** | 내부 호출 전용 |
| **인증** | `X-Internal-Token` 필수 |

---

### 2. 상세 설명

- **핵심:** AI가 Milvus에서 정확히 조회하려면 “적재ID만”으로는 부족할 수 있으므로, RAGFlow는 콜백에서 **milvusRef**를 제공한다.
- 백엔드는 결과를 저장하고, 성공 시 문서 상태를 `INGESTED`로 전이한다.

---

### 3. Request

#### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `Content-Type` | `application/json` | ✅ |
| `X-Internal-Token` | `<token>` | ✅ |

#### Path Params

| Param | 설명 | 타입 |
| --- | --- |
| `ingestId` | 적재 Job ID | `string(uuid)` |

#### Body

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `documentId` | 문서 ID | `string` | false |
| `datasetId` | dataset 식별자 | `string` | false |
| `indexVersion` | 인덱스 버전 | `number` | false |
| `status` | 결과 | `string` (`INGESTED` \| `INGEST_FAILED`) | false |
| `milvusRef` | Milvus 조회 참조키 세트 | `object` | true *(성공 시 필수)* |
| `stats` | 청크/토큰 등 통계 | `object` | true |
| `errorCode` | 실패 코드 | `string` | true |
| `errorMessage` | 실패 메시지 | `string` | true |
| `requestId` | 멱등 키(권장) | `string(uuid)` | true |

##### milvusRef (권장 필드)

| key | 설명 | 타입 |
| --- | --- | --- |
| `collection` | Milvus 컬렉션 | `string` |
| `partition` | 파티션(사용 시) | `string` |
| `filterExpr` | 조회 필터식(예: doc_id/version) | `string` |

#### Example (성공)

```json
{
  "documentId": "DOC-001",
  "datasetId": "DS-POLICY-01",
  "indexVersion": 1,
  "status": "INGESTED",
  "milvusRef": {
    "collection": "policy_chunks",
    "partition": "DS-POLICY-01",
    "filterExpr": "document_id == 'DOC-001' && index_version == 1"
  },
  "stats": {
    "chunkCount": 320,
    "tokenEstimate": 48000
  },
  "requestId": "uuid"
}
```

#### Example (실패)

```json
{
  "documentId": "DOC-001",
  "datasetId": "DS-POLICY-01",
  "indexVersion": 1,
  "status": "INGEST_FAILED",
  "errorCode": "OCR_FAILED",
  "errorMessage": "pdf text extraction failed",
  "requestId": "uuid"
}
```

---

### 4. Response (200 OK)

```json
{
  "saved": true
}
```

---

### 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `200` | 저장 성공(멱등 포함) |
| `401/403` | 내부 토큰 오류 |
| `404` | ingestId 없음 |
| `500` | 저장 실패 |

---

# 3) (내부) 스크립트 생성 Job 요청(적재 결과 기반)

카테고리 : 영상(스크립트)  
method : POST  
URL : `/internal/ai/script-jobs`  
사용자 : 시스템(내부)  
요청자 : 백엔드  
응답자 : AI  

## 📘 스크립트 생성 Job 요청

### 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /internal/ai/script-jobs` |
| **설명** | RAGFlow 적재 완료된 문서를 기반으로 “영상 스크립트 + 메타데이터” 생성 Job 시작 |
| **호출 주체** | Backend → AI |
| **권한** | 내부 호출 전용 |
| **인증** | `X-Internal-Token` 필수 |

---

### 2. 상세 설명

- 백엔드는 문서 상태가 `INGESTED`일 때만 요청한다.
- AI는 전달받은 **milvusRef**를 사용해 Milvus에서 청크를 조회하고, 결과를 바탕으로 스크립트/메타를 구성한다.
- 결과 저장은 **콜백(완료 통지)** 로 백엔드에 전달한다.

---

### 3. Request

#### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `Content-Type` | `application/json` | ✅ |
| `X-Internal-Token` | `<token>` | ✅ |

#### Body

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `scriptJobId` | 스크립트 생성 Job ID(백 발급) | `string(uuid)` | false |
| `videoId` | 영상 ID | `string` | false |
| `documentId` | 문서 ID | `string` | false |
| `datasetId` | dataset 식별자 | `string` | false |
| `ingestId` | 적재 Job ID | `string(uuid)` | false |
| `indexVersion` | 인덱스 버전 | `number` | false |
| `milvusRef` | Milvus 조회 참조키 | `object` | false |
| `scriptPolicyId` | 스크립트 정책 프리셋(선택) | `string` | true |
| `requestId` | 멱등 키(권장) | `string(uuid)` | true |

#### Example

```json
{
  "scriptJobId": "uuid",
  "videoId": "V-FOUR-001",
  "documentId": "DOC-001",
  "datasetId": "DS-POLICY-01",
  "ingestId": "uuid",
  "indexVersion": 1,
  "milvusRef": {
    "collection": "policy_chunks",
    "partition": "DS-POLICY-01",
    "filterExpr": "document_id == 'DOC-001' && index_version == 1"
  },
  "scriptPolicyId": "SP-DEFAULT-01",
  "requestId": "uuid"
}
```

---

### 4. Response (202 Accepted)

```json
{
  "received": true,
  "scriptJobId": "uuid",
  "status": "GENERATING"
}
```

---

### 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `202` | 요청 접수(비동기) |
| `401/403` | 내부 토큰 오류 |
| `409` | 중복 scriptJobId 등 충돌 |
| `500` | 요청 실패 |

---

# 4) (내부) 스크립트 생성 완료 콜백

카테고리 : 영상(스크립트)  
method : POST  
URL : `/internal/callbacks/script-jobs/{scriptJobId}/complete`  
사용자 : 시스템(내부)  
요청자 : AI  
응답자 : 백엔드  

## 📘 스크립트 생성 완료 콜백

### 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /internal/callbacks/script-jobs/{scriptJobId}/complete` |
| **설명** | 생성 결과(성공/실패) + 생성된 scriptId/version 전달 |
| **호출 주체** | AI → Backend |
| **권한** | 내부 호출 전용 |
| **인증** | `X-Internal-Token` 필수 |

---

### 2. 상세 설명

- 성공 시 백엔드는 스크립트 레코드를 생성하고 상태를 `DRAFT`로 저장한다.
- 이후 관리자/검토자가 스크립트를 확인하고 `SCRIPT_APPROVED`로 전이한다(별도 외부 API).

---

### 3. Request

#### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `Content-Type` | `application/json` | ✅ |
| `X-Internal-Token` | `<token>` | ✅ |

#### Path Params

| Param | 설명 | 타입 |
| --- | --- |
| `scriptJobId` | 스크립트 Job ID | `string(uuid)` |

#### Body

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `videoId` | 영상 ID | `string` | false |
| `status` | 결과 | `string` (`COMPLETED` \| `FAILED`) | false |
| `scriptId` | 생성된 스크립트 ID | `string(uuid)` | true |
| `scriptVersion` | 생성 버전(정수) | `number` | true |
| `totalDurationSec` | 총 길이(초) | `number` | true |
| `outline` | 챕터 요약(선택) | `array` | true |
| `errorCode` | 실패 코드 | `string` | true |
| `errorMessage` | 실패 메시지 | `string` | true |
| `traceId` | 추적용 트레이스 ID(권장) | `string` | true |
| `requestId` | 멱등 키(권장) | `string(uuid)` | true |

#### Example (성공)

```json
{
  "videoId": "V-FOUR-001",
  "status": "COMPLETED",
  "scriptId": "uuid",
  "scriptVersion": 1,
  "totalDurationSec": 720,
  "outline": [
    {"chapterOrder": 1, "title": "정의와 유형", "durationSec": 180},
    {"chapterOrder": 2, "title": "사례와 예방", "durationSec": 240}
  ],
  "traceId": "trace-123",
  "requestId": "uuid"
}
```

#### Example (실패)

```json
{
  "videoId": "V-FOUR-001",
  "status": "FAILED",
  "errorCode": "MILVUS_QUERY_FAILED",
  "errorMessage": "collection not found",
  "traceId": "trace-123",
  "requestId": "uuid"
}
```

---

### 4. Response (200 OK)

```json
{
  "saved": true
}
```

---

### 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `200` | 저장 성공(멱등 포함) |
| `401/403` | 내부 토큰 오류 |
| `404` | scriptJobId 없음 |
| `500` | 저장 실패 |

---

# 5) (외부) 스크립트 1차 승인/반려

카테고리 : 영상(스크립트)  
method : POST  
URL : `/video/scripts/{scriptId}/approve`  
사용자 : 시스템 관리자(ADMIN) *(프로젝트 RBAC 명칭에 맞춰 매핑)*  
요청자 : 프론트  
응답자 : 백엔드  

## 📘 스크립트 1차 승인/반려

### 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /video/scripts/{scriptId}/approve` |
| **설명** | 스크립트를 `SCRIPT_APPROVED` 또는 `SCRIPT_REJECTED`로 전이(렌더 가능 여부 결정) |
| **호출 주체** | Frontend → Backend |
| **권한** | `ROLE_ADMIN` |
| **인증** | `Authorization: Bearer <token>` |

---

### 2. 상세 설명

- 승인 시 백엔드는 스크립트 상태를 `SCRIPT_APPROVED`로 변경한다.
- 반려 시 `SCRIPT_REJECTED`로 변경하고 사유를 저장한다.

---

### 3. Request

#### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `Content-Type` | `application/json` | ✅ |
| `Authorization` | `Bearer <token>` | ✅ |

#### Path Params

| Param | 설명 | 타입 |
| --- | --- |
| `scriptId` | 스크립트 ID | `string(uuid)` |

#### Body

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `decision` | 결정 | `string` (`APPROVE` \| `REJECT`) | false |
| `comment` | 코멘트 | `string` | true |
| `reasonCode` | 반려 사유 코드 | `string` | true |

#### Example (승인)

```json
{
  "decision": "APPROVE",
  "comment": "구성/톤/길이 모두 적절합니다."
}
```

---

### 4. Response (200 OK)

```json
{
  "scriptId": "uuid",
  "status": "SCRIPT_APPROVED",
  "approvedAt": "2025-12-20T12:00:00+09:00"
}
```

---

### 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `200` | 처리 성공 |
| `403` | 권한 없음 |
| `404` | scriptId 없음 |
| `409` | 상태 충돌(예: 이미 승인/반려 완료) |
| `500` | 처리 실패 |

---

# 6) (내부) 영상(렌더) 생성 요청

카테고리 : 영상  
method : POST  
URL : `/internal/ai/render-jobs`  
사용자 : 시스템(내부)  
요청자 : 백엔드  
응답자 : AI  

## 📘 영상(렌더) 생성 요청

### 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /internal/ai/render-jobs` |
| **설명** | 1차 승인된 스크립트로 영상 렌더 Job 시작 |
| **호출 주체** | Backend → AI |
| **권한** | 내부 호출 전용 |
| **인증** | `X-Internal-Token` 필수 |

---

### 2. 상세 설명

- 백엔드는 **`SCRIPT_APPROVED` 된 scriptId + scriptVersion** 에 대해서만 렌더 요청을 보낸다.
- AI는 렌더 시작 직전에 **승인본 스냅샷(render-spec)** 을 백엔드에서 조회해 그대로 렌더링한다.

#### 처리 흐름

```
Backend → AI (render-jobs 생성)
  └─ AI → Backend (GET /internal/scripts/{scriptId}/render-spec)
      └─ 렌더링 수행
          └─ AI → Backend (콜백: /internal/callbacks/render-jobs/{jobId}/complete)
```

---

### 3. Request

#### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `Content-Type` | `application/json` | ✅ |
| `X-Internal-Token` | `<token>` | ✅ |

#### Body  *(scriptVersion 필수)*

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `jobId` | 렌더 Job ID(백 발급) | `string(uuid)` | false |
| `videoId` | 영상 ID | `string` | false |
| `scriptId` | 승인된 스크립트 ID | `string(uuid)` | false |
| `scriptVersion` | 승인 버전(스냅샷 고정) | `number` | false |
| `renderPolicyId` | 렌더 정책 프리셋 | `string` | true |
| `requestId` | 멱등 키(권장) | `string(uuid)` | true |

#### Example

```json
{
  "jobId": "uuid",
  "videoId": "V-FOUR-001",
  "scriptId": "uuid",
  "scriptVersion": 1,
  "renderPolicyId": "RP-DEFAULT-01",
  "requestId": "uuid"
}
```

---

### 4. Response (202 Accepted)

```json
{
  "received": true,
  "jobId": "uuid",
  "status": "RENDERING"
}
```

---

### 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `202` | 요청 접수(비동기) |
| `401/403` | 내부 토큰 오류 |
| `409` | 중복 jobId 등 상태 충돌 |
| `500` | 요청 실패 |

---

# 7) (내부) 렌더 스펙 조회(승인본 스냅샷)

카테고리 : 영상  
method : GET  
URL : `/internal/scripts/{scriptId}/render-spec`  
사용자 : 시스템(내부)  
요청자 : AI  
응답자 : 백엔드  

## 📘 렌더 스펙 조회(승인본 스냅샷)

### 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `GET /internal/scripts/{scriptId}/render-spec` |
| **설명** | AI가 렌더링에 사용할 “승인본 스냅샷(render spec)” 조회 |
| **호출 주체** | AI → Backend |
| **권한** | 내부 호출 전용 |
| **인증** | `X-Internal-Token` 필수 |

---

### 3. Request

#### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `X-Internal-Token` | `<token>` | ✅ |

#### Path Params

| Param | 설명 | 타입 |
| --- | --- |
| `scriptId` | 스크립트 ID | `string(uuid)` |

---

### 4. Response (200 OK)

```json
{
  "scriptId": "uuid",
  "videoId": "V-FOUR-001",
  "version": 1,
  "renderPolicyId": "RP-DEFAULT-01",
  "totalDurationSec": 720,

  "source": {
    "documentId": "DOC-001",
    "datasetId": "DS-POLICY-01",
    "ingestId": "uuid",
    "indexVersion": 1
  },

  "scenes": [
    {
      "sceneId": "uuid",
      "sceneOrder": 1,
      "narration": "…",
      "caption": "…",
      "durationSec": 15
    }
  ]
}
```

---

### 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `200` | 조회 성공 |
| `401/403` | 내부 토큰 오류 |
| `404` | script 없음 |
| `500` | 조회 실패 |

---

# 8) (내부) 영상 생성 완료 콜백

카테고리 : 영상  
method : POST  
URL : `/internal/callbacks/render-jobs/{jobId}/complete`  
사용자 : 시스템(내부)  
요청자 : AI  
응답자 : 백엔드  

## 📘 영상 생성 완료 콜백

### 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /internal/callbacks/render-jobs/{jobId}/complete` |
| **설명** | 렌더링 결과(성공/실패) + 산출물 URL 전달 |
| **호출 주체** | AI → Backend |
| **권한** | 내부 호출 전용 |
| **인증** | `X-Internal-Token` 필수 |

---

### 3. Request

#### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `Content-Type` | `application/json` | ✅ |
| `X-Internal-Token` | `<token>` | ✅ |

#### Path Params

| Param | 설명 | 타입 |
| --- | --- |
| `jobId` | 렌더 Job ID | `string(uuid)` |

#### Body *(썸네일/자막/로그/traceId 포함)*

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `videoId` | 영상 ID | `string` | false |
| `status` | 결과 | `string` (`COMPLETED` \| `FAILED`) | false |
| `videoUrl` | 결과 영상 경로 | `string` | true |
| `thumbnailUrl` | 썸네일 경로(권장) | `string` | true |
| `subtitleUrl` | 자막 파일 경로(srt/vtt) | `string` | true |
| `durationSec` | 영상 길이(초) | `number` | true |
| `logUrl` | 렌더 로그/아티팩트 경로(권장) | `string` | true |
| `traceId` | 추적용 트레이스 ID(권장) | `string` | true |
| `errorCode` | 실패 코드 | `string` | true |
| `errorMessage` | 실패 메시지 | `string` | true |
| `requestId` | 멱등 키(권장) | `string(uuid)` | true |

#### Example (성공)

```json
{
  "videoId": "V-FOUR-001",
  "status": "COMPLETED",
  "videoUrl": "s3://bucket/videos/V-FOUR-001/render.mp4",
  "thumbnailUrl": "s3://bucket/videos/V-FOUR-001/thumb.png",
  "subtitleUrl": "s3://bucket/videos/V-FOUR-001/subtitles.vtt",
  "durationSec": 720,
  "logUrl": "s3://bucket/logs/render/V-FOUR-001/job-uuid.log",
  "traceId": "trace-456",
  "requestId": "uuid"
}
```

#### Example (실패)

```json
{
  "videoId": "V-FOUR-001",
  "status": "FAILED",
  "errorCode": "RENDER_FAILED",
  "errorMessage": "ffmpeg exit code 1",
  "traceId": "trace-456",
  "requestId": "uuid"
}
```

---

### 4. Response (200 OK)

```json
{
  "saved": true
}
```

---

### 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `200` | 저장 성공(멱등 포함) |
| `401/403` | 내부 토큰 오류 |
| `404` | job 없음 |
| `500` | 저장 실패 |

---

## (필수) 구현 규칙 5개 — “재시도/중복/추적”까지 안전

1. **RAGFlow 콜백은 milvusRef 포함**(AI가 Milvus에서 안정적으로 조회하도록)  
2. **script-jobs / render-jobs는 둘 다 비동기 + 콜백**(패턴 통일)  
3. **render-spec는 승인된 version 스냅샷 반환**(렌더 중 변경 사고 방지)  
4. **모든 콜백은 멱등(upsert) 처리**(중복 콜백도 200 OK)  
5. **requestId/traceId를 전 구간에서 전달/저장**(장애 원인 추적)

---

# 9) (외부) 영상 최종 승인(게시) 요청

카테고리 : 영상  
method : POST  
URL : `/videos/jobs/{jobId}/approve`  
사용자 : 콘텐츠 검토자 (CONTENTS_REVIEWER)  
요청자 : 프론트  
응답자 : 백엔드  

## 📘 영상 최종 승인(게시) 요청

### 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /videos/jobs/{jobId}/approve` |
| **설명** | 2차 검토(스크립트+영상) 결과로 최종 승인 시 `PUBLISHED` 처리(교육 목록 노출) |
| **호출 주체** | Frontend → Backend |
| **권한** | `ROLE_CONTENTS_REVIEWER` |
| **인증** | `Authorization: Bearer <token>` |

---

### 2. 상세 설명

- 검토자는 렌더 완료된 영상 결과물을 보고
  - 승인 → `PUBLISHED`
  - 반려 → `FINAL_REJECTED`
- 승인 조건: `renderJob.status == COMPLETED`가 아니면 `409`

---

### 3. Request

#### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `Content-Type` | `application/json` | ✅ |
| `Authorization` | `Bearer <token>` | ✅ |

#### Path Params

| Param | 설명 | 타입 |
| --- | --- |
| `jobId` | 최종 검토 대상 렌더 Job ID | `string(uuid)` |

#### Body

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `decision` | 최종 결정 | `string` (`APPROVE` \| `REJECT`) | false |
| `comment` | 검토 코멘트 | `string` | true |
| `reasonCode` | 반려 사유 코드 | `string` | true |
| `requestedBy` | 요청자 ID(감사로그) | `string` | true *(토큰에서 추출 가능)* |

#### Example

```json
{
  "decision": "APPROVE",
  "comment": "자막/음성/슬라이드 모두 OK, 게시 진행합니다.",
  "requestedBy": "U-REVIEWER-001"
}
```

---

### 4. Response (200 OK)

```json
{
  "jobId": "uuid",
  "videoId": "V-FOUR-001",
  "videoStatus": "PUBLISHED",
  "publishedAt": "2025-12-20T12:34:56+09:00"
}
```

반려 응답:

```json
{
  "jobId": "uuid",
  "videoId": "V-FOUR-001",
  "videoStatus": "FINAL_REJECTED"
}
```

---

### 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `200` | 승인/반려 처리 성공(멱등 권장) |
| `403` | 권한 없음 |
| `404` | jobId 없음 |
| `409` | 상태 충돌(예: job이 COMPLETED 아님 / 이미 게시 완료) |
| `422` | decision 누락/형식 오류 |
| `500` | 처리 실패 |

---

# (선택) 프론트 조회용 API 2개 (운영/화면에 꼭 필요)

## A) 영상 상세 조회
- `GET /videos/{videoId}`
- 포함 권장: `videoStatus`, `documentId`, `scriptId`, `latestJobId`, `videoUrl`, `thumbnailUrl`

## B) 렌더 Job 상세 조회
- `GET /videos/jobs/{jobId}`
- 포함 권장: `status`, `scriptId`, `scriptVersion`, `videoUrl`, `errorMessage`, `createdAt`, `updatedAt`
