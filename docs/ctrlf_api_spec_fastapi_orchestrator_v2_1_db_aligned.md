# CTRL+F AI — **FastAPI 오케스트레이터 v2.1 (DB 반영 버전)**  
(Milvus + DB 역할분리 + 멀티문서 소스셋) 전체 API 명세 (Notion용)

> **핵심 확정(유지)**  
> 1) **임베딩 벡터는 Milvus에 저장**  
> 2) **DB는 chunk_text + (권장) chunk_meta만 저장**  
> 3) **멀티 문서(소스셋) → 스크립트 1개 → 영상 1개**  
> 4) **FastAPI = RAGFlow 단일 접점(프록시/오케스트레이터)**  
> 5) Spring은 `datasetId/ingestId/indexVersion`을 **미리 알 수 없음** → 요청에서 제거하고, FastAPI가 내부 생성/결정

---

# 0) 용어/DB 기준(정렬)

- **SourceSet**: 여러 문서를 묶는 단위. DB에 `education.source_set / education.source_set_document`가 존재함.  
- **RagDocument**: 문서 1건. DB에 `infra.rag_document`가 존재하며 원본 URL은 `source_url`.  
- **Chunk**: 문서 청크. DB에 `infra.rag_document_chunk(chunk_index, chunk_text, embedding vector(1536))`가 존재하나, v2.1에서는 **embedding 컬럼 미사용(=NULL 유지)**.  
- **FailChunk**: 임베딩 실패 로그. DB에 `infra.rag_fail_chunk` 존재.

---

# A) SourceSet 오케스트레이션 (Spring ↔ FastAPI)

## 1) (내부) 소스셋 작업 시작 — **적재 오케스트레이션 + 스크립트 자동 생성 트리거**

카테고리 : RAG+스크립트(오케스트레이션)  
method : POST  
URL : `/internal/ai/source-sets/{sourceSetId}/start`  

사용자 : 시스템(내부)  
요청자 : Spring(백엔드)  
응답자 : FastAPI(AI)  

# 📘 소스셋 작업 시작

## 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /internal/ai/source-sets/{sourceSetId}/start` |
| **설명** | sourceSet의 문서들을 RAGFlow로 적재(임베딩은 Milvus), DB에 chunk_text/메타 저장 후 **스크립트를 자동 생성**하여 Spring에 콜백 |
| **호출 주체** | Spring → FastAPI |
| **권한** | 내부 호출 전용 |
| **인증** | `X-Internal-Token` 필수 |

---

## 2. 처리 흐름(확정)

```
Spring → FastAPI: /start
  └─ FastAPI → Spring: GET /internal/source-sets/{sourceSetId}/documents  (문서 목록 조회)
      └─ FastAPI → RAGFlow: 문서별 ingest 요청(프록시)
          └─ RAGFlow → FastAPI: ingest 완료(성공/실패 이벤트)
              ├─ 성공: FastAPI → Milvus 업서트(벡터 저장)
              ├─ 성공: FastAPI → Spring: chunk_text(+meta) bulk upsert
              ├─ 실패: FastAPI → Spring: fail_chunk bulk upsert
              └─ 문서별 결과 집계
                  ├─ 전체 성공 → 스크립트 생성 → /callbacks/source-sets/{id}/complete (COMPLETED)
                  └─ 하나라도 실패 → /callbacks/source-sets/{id}/complete (FAILED)
```

> DB `education.source_set.status`는 `CREATED → LOCKED → (SCRIPT_READY | FAILED)` 전이를 사용한다.  
> `/start` 호출 시 Spring이 `LOCKED`로 바꾸거나(권장), FastAPI 콜백 시점에 Spring이 상태를 갱신한다.

---

## 3. Request

### Headers

| Header | 값 | 필수 |
| --- | --- | --- |
| `Content-Type` | `application/json` | ✅ |
| `X-Internal-Token` | `<token>` | ✅ |

### Path Params

| Param | 설명 | 타입 |
| --- | --- | --- |
| `sourceSetId` | 소스셋 ID | `string(uuid)` |

### Body (v2.1)

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `educationId` | 연결 교육 ID(선택) | `string(uuid)` | true |
| `videoId` | 영상 ID(백 발급) | `string(uuid)` | false |
| `requestId` | 멱등 키(권장) | `string(uuid)` | true |
| `traceId` | 추적용(권장) | `string` | true |
| `scriptPolicyId` | 스크립트 생성 정책 프리셋(선택) | `string` | true |
| `llmModelHint` | 사용 모델 힌트(선택) | `string` | true |

> **제거(확정)**: `documents[]`, `datasetId`, `ingestId`, `indexVersion` (Spring이 미리 알 수 없음)

### Example

```json
{
  "educationId": "EDU-001",
  "videoId": "VID-001",
  "requestId": "1b2b3b4b-....",
  "traceId": "trace-20251222-0001",
  "scriptPolicyId": "SP-DEFAULT-01",
  "llmModelHint": "qwen2.5-14b-instruct"
}
```

---

## 4. Response (202 Accepted)

```json
{
  "received": true,
  "sourceSetId": "uuid",
  "status": "LOCKED"
}
```

- `status`는 DB의 `education.source_set.status` 값과 동일하게 반환한다.

---

## 5. Status Code

| Status Code | 의미 |
| --- | --- |
| `202` | 접수(비동기) |
| `401/403` | 내부 토큰 오류 |
| `409` | 상태 충돌(이미 LOCKED/SCRIPT_READY 등) |
| `500` | 처리 실패 |

---

## (필수) 구현 규칙 6개

1. **/start는 멱등**: 같은 `sourceSetId`에 대해 이미 `LOCKED` 이상이면 `202/409`로 동일 처리(정책 선택)  
2. 문서 목록은 **FastAPI가 Spring에서 조회**한다(요청 body에서 제거)  
3. 임베딩 벡터는 **Milvus 단독**, DB는 chunk_text(+meta)만 저장  
4. 검색/추적 키는 **(document_id, chunk_index)** 고정  
5. 문서 재적재는 DB soft delete + Milvus delete 후 재생성  
6. 콜백은 **멱등(upsert)**: 중복 호출도 200 OK

---

## 2) (내부) 소스셋 문서 목록 조회 (FastAPI가 호출)

카테고리 : SourceSet  
method : GET  
URL : `/internal/source-sets/{sourceSetId}/documents`  

사용자 : 시스템(내부)  
요청자 : FastAPI(AI)  
응답자 : Spring(백엔드)  

# 📘 소스셋 문서 목록 조회

## 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `GET /internal/source-sets/{sourceSetId}/documents` |
| **설명** | FastAPI가 sourceSet에 포함된 RagDocument 목록을 조회 |
| **호출 주체** | FastAPI → Spring |
| **인증** | `X-Internal-Token` 필수 |

---

## 3. Response (200 OK)

```json
{
  "sourceSetId": "uuid",
  "documents": [
    {
      "documentId": "uuid",
      "title": "교육자료 1",
      "domain": "FOUR_MANDATORY",
      "sourceUrl": "s3://bucket/docs/DOC-001.pdf",
      "status": "QUEUED"
    }
  ]
}
```

> DB 컬럼명이 `source_url`이므로 응답 키는 `sourceUrl`로 맞추는 것을 권장.

---

## 3) (내부) 소스셋 완료 콜백 — **(성공) 스크립트 생성 결과 / (실패) 실패 사유**

카테고리 : RAG+스크립트(콜백)  
method : POST  
URL : `/internal/callbacks/source-sets/{sourceSetId}/complete`  

사용자 : 시스템(내부)  
요청자 : FastAPI(AI)  
응답자 : Spring(백엔드)  

# 📘 소스셋 완료 콜백

## 1. 기본 정보

| 항목 | 내용 |
| --- | --- |
| **URL** | `POST /internal/callbacks/source-sets/{sourceSetId}/complete` |
| **설명** | sourceSet 오케스트레이션 완료 결과를 Spring에 전달(성공/실패) |
| **호출 주체** | FastAPI → Spring |
| **인증** | `X-Internal-Token` 필수 |

---

## 3. Body

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `videoId` | 영상 ID | `string(uuid)` | false |
| `status` | 결과 | `string` (`COMPLETED` \| `FAILED`) | false |
| `sourceSetStatus` | DB source_set 상태 | `string` (`SCRIPT_READY` \| `FAILED`) | false |
| `documents` | 문서별 결과 | `array` | false |
| `script` | 생성된 스크립트(성공 시) | `object` | true |
| `errorCode` | 실패 코드 | `string` | true |
| `errorMessage` | 실패 메시지 | `string` | true |
| `requestId` | 멱등 키 | `string(uuid)` | true |
| `traceId` | 추적용 | `string` | true |

### documents[] (문서별 결과)

| key | 설명 | 타입 |
| --- | --- | --- |
| `documentId` | RagDocument ID | `string(uuid)` |
| `status` | `COMPLETED \| FAILED` | `string` |
| `failReason` | 실패 사유(있으면) | `string` |

### script (성공 시)

- Spring DB에 바로 저장 가능한 “정본 JSON” 구조(education_script / chapter / scene 저장용)  
- **멀티문서 출처**를 위해 scene에는 `sourceRefs`를 포함한다.

```json
{
  "scriptId": "uuid",
  "educationId": "EDU-001",
  "sourceSetId": "uuid",
  "title": "직장내 괴롭힘 예방 교육",
  "totalDurationSec": 720,
  "version": 1,
  "llmModel": "qwen2.5-14b-instruct",
  "chapters": [
    {
      "chapterId": "uuid",
      "chapterIndex": 1,
      "title": "정의와 사례",
      "durationSec": 180,
      "scenes": [
        {
          "sceneId": "uuid",
          "sceneIndex": 1,
          "purpose": "도입",
          "narration": "...",
          "caption": "...",
          "visual": "...",
          "durationSec": 15,
          "confidenceScore": 0.82,
          "sourceRefs": [
            {"documentId": "uuid", "chunkIndex": 3},
            {"documentId": "uuid", "chunkIndex": 10}
          ]
        }
      ]
    }
  ]
}
```

---

## 4. Response (200 OK)

```json
{ "saved": true }
```

---

# B) 청크/실패 로그 저장 (FastAPI → Spring)

## 4) (내부) 문서 청크 Bulk Upsert

카테고리 : RAG(DB 저장)  
method : POST  
URL : `/internal/rag/documents/{documentId}/chunks:bulk`  

사용자 : 시스템(내부)  
요청자 : FastAPI(AI)  
응답자 : Spring(백엔드)

# 📘 문서 청크 Bulk Upsert

## Request Body

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `chunks` | 청크 리스트 | `array` | false |
| `requestId` | 멱등 키 | `string(uuid)` | true |

### chunks[] item

| key | 설명 | 타입 | Nullable |
| --- | --- | --- | --- |
| `chunkIndex` | 청크 번호 | `number` | false |
| `chunkText` | 청크 텍스트 | `string` | false |
| `chunkMeta` | (권장) 메타데이터 | `object` | true |

> **주의(DB 반영 필요)**: service.md의 `infra.rag_document_chunk`에는 `chunk_meta` 컬럼이 아직 없으므로,  
> 1) `chunk_meta jsonb` 컬럼을 추가하거나,  
> 2) 메타는 Milvus payload로만 보관하고 DB에는 미저장  
> 중 하나로 확정해야 한다. (v2.1 권장: 1)

---

## 5) (내부) 임베딩 실패 로그 Bulk Upsert

카테고리 : RAG(DB 저장)  
method : POST  
URL : `/internal/rag/documents/{documentId}/fail-chunks:bulk`  

요청자 : FastAPI(AI)  
응답자 : Spring(백엔드)

# 📘 임베딩 실패 로그 Bulk Upsert

## Request Body

```json
{
  "fails": [
    {"chunkIndex": 12, "failReason": "OCR_EMPTY"},
    {"chunkIndex": 13, "failReason": "EMBEDDING_TIMEOUT"}
  ],
  "requestId": "uuid"
}
```

---

# C) 렌더(영상 생성) — DB 반영 정렬 포인트만 수정

> 렌더 관련 세부 API는 기존 명세를 유지하되, **Job 상태값을 DB와 동일하게 맞춘다**.  
> DB `education.video_generation_job.status`: `QUEUED, PROCESSING, COMPLETED, FAILED` (명시됨)

## 수정 포인트(권장)

1) `/internal/ai/render-jobs` 응답/상태값: `RENDERING` 대신 `PROCESSING` 사용  
2) `/internal/callbacks/render-jobs/{jobId}/complete` 성공 시 `generated_video_url` 필드에 저장(=DB 컬럼명과 매핑)  
3) `GET /internal/scripts/{scriptId}/render-spec` 응답에 `sourceSetId` 포함 권장(추적성)

---

# D) 멀티문서 DB 변경(필수/권장)

## D-1. source_set는 이미 존재(OK)
- `education.source_set.status`는 `CREATED, LOCKED, SCRIPT_READY, FAILED` 로 정의됨  
- `education.source_set_document.document_id`는 `infra.rag_document.id` 참조

## D-2. Script에 sourceSet 연결 (필수)
- 현재 `education.education_script`는 `source_doc_id`(레거시)만 존재함  
→ `source_set_id uuid` 컬럼 추가(필수), 레거시 필드는 nullable 유지 권장

## D-3. Scene 출처를 멀티문서로 (필수)
- 현재 `education.education_script_scene.source_chunk_indexes int[]`는 **문서 1개일 때만** 의미가 명확함  
→ 아래 중 1개를 확정해야 함  
  - (권장) `source_refs jsonb` 추가: `[{documentId, chunkIndex}, ...]`  
  - (대안) 별도 테이블 `education_script_scene_source_ref(scene_id, document_id, chunk_index)` 신설

## D-4. Video에 sourceSet 연결 (필수)
- 현재 `education.education_video.material_id`는 단일 RagDocument를 가리킴  
→ `source_set_id uuid` 추가(필수), `material_id`는 레거시/단일문서 용도로 nullable 유지 권장

## D-5. 스크립트 1차 승인 데이터(현재 스키마에 없음)
- 기존 정책 “SCRIPT_APPROVED일 때만 render”를 쓰려면,  
  `education.education_script`에 **승인 상태를 저장할 컬럼/테이블**이 추가로 필요함.  
  - (권장) `education_script.status (DRAFT, REVIEW_REQUESTED, APPROVED, REJECTED)` 컬럼 추가  
  - (권장) `education.education_script_review` 테이블을 `education_video_review` 패턴으로 추가

---

# (필수) 운영 규칙 5개 — 최종 정리

1. SourceSet 단위로 **멀티문서 → 스크립트 1개 → 영상 1개**  
2. 임베딩 벡터는 **Milvus**, DB는 **chunk_text(+meta)**  
3. FastAPI는 **RAGFlow 단일 접점**이며, Spring은 FastAPI만 호출한다  
4. 출처 트레이싱 키는 `(document_id, chunk_index)` 고정  
5. callback/벌크 업서트는 **멱등 처리**

