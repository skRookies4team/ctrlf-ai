# 소스셋 완료 콜백 API 명세

> **버전**: v1.0  
> **최종 수정일**: 2025-12-23  
> **담당 서비스**: education-service (Spring)  
> **호출자**: ctrlf-ai (FastAPI)

---

## 1. 개요

소스셋 오케스트레이션 완료 후, FastAPI가 Spring 백엔드로 결과를 전달하는 내부 콜백 API입니다.

### 1.1 흐름도

```
[FastAPI ctrlf-ai]
     │
     │ 1. 문서 처리 & 스크립트 생성 완료
     │
     ▼
POST /internal/callbacks/source-sets/{sourceSetId}/complete
     │
     ▼
[Spring education-service]
     │
     ├─ SourceSet 상태 업데이트
     ├─ SourceSetDocument 상태 업데이트
     ├─ EducationScript + Chapter + Scene 저장
     └─ EducationVideo에 scriptId 연결
```

---

## 2. API 상세

### POST /internal/callbacks/source-sets/{sourceSetId}/complete

소스셋 오케스트레이션 완료 결과를 Spring에 전달합니다.

| 항목 | 값 |
|------|-----|
| **Method** | `POST` |
| **URL** | `/internal/callbacks/source-sets/{sourceSetId}/complete` |
| **인증** | `X-Internal-Token` 헤더 |
| **Content-Type** | `application/json` |

---

### 2.1 Path Parameters

| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| `sourceSetId` | UUID | ✅ | 소스셋 ID |

---

### 2.2 Request Body

```json
{
  "videoId": "97fb8f4e-7812-40a1-8cbc-0eed5d7876ad",
  "status": "COMPLETED",
  "sourceSetStatus": "SCRIPT_READY",
  "documents": [
    {
      "documentId": "877f4068-8b38-4958-9d44-5d6fa390f1a0",
      "status": "COMPLETED",
      "failReason": null
    }
  ],
  "script": {
    "educationId": "0f1beedd-05ad-4717-a9da-508e98c556a8",
    "sourceSetId": "d0eba687-43d6-461b-8967-a5f0322025a5",
    "title": "직장내괴롭힘 교육",
    "totalDurationSec": 720,
    "version": 1,
    "llmModel": "gpt-4o",
    "chapters": [
      {
        "chapterIndex": 0,
        "title": "괴롭힘이란",
        "durationSec": 180,
        "scenes": [
          {
            "sceneIndex": 0,
            "purpose": "hook",
            "narration": "직장 내 괴롭힘이란...",
            "caption": "직장 내 괴롭힘이란...",
            "visual": "텍스트 강조 화면",
            "durationSec": 30,
            "confidenceScore": 0.95,
            "sourceRefs": [
              {
                "documentId": "877f4068-8b38-4958-9d44-5d6fa390f1a0",
                "chunkIndex": 0
              }
            ]
          }
        ]
      }
    ]
  },
  "errorCode": null,
  "errorMessage": null,
  "requestId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "traceId": "trace-abc123"
}
```

---

### 2.3 Request Body 필드 설명

#### 최상위 필드

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `videoId` | UUID | ✅ | 대상 영상 ID |
| `status` | String | ✅ | 처리 결과 (`COMPLETED` \| `FAILED`) |
| `sourceSetStatus` | String | ✅ | DB에 저장될 소스셋 상태 (`SCRIPT_READY` \| `FAILED`) |
| `documents` | Array | ✅ | 문서별 처리 결과 목록 |
| `script` | Object | ❌ | 생성된 스크립트 (성공 시에만) |
| `errorCode` | String | ❌ | 에러 코드 (실패 시) |
| `errorMessage` | String | ❌ | 에러 메시지 (실패 시) |
| `requestId` | UUID | ❌ | 멱등성 키 |
| `traceId` | String | ❌ | 분산 추적 ID |

#### documents[] (문서별 결과)

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `documentId` | String | ✅ | 문서 ID |
| `status` | String | ✅ | 처리 상태 (`COMPLETED` \| `FAILED`) |
| `failReason` | String | ❌ | 실패 사유 |

#### script (생성된 스크립트)

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `educationId` | String | ✅ | 교육 ID |
| `sourceSetId` | String | ✅ | 소스셋 ID |
| `title` | String | ✅ | 스크립트 제목 |
| `totalDurationSec` | Integer | ✅ | 총 길이(초) |
| `version` | Integer | ✅ | 스크립트 버전 |
| `llmModel` | String | ❌ | 사용된 LLM 모델 |
| `chapters` | Array | ✅ | 챕터 목록 |

#### script.chapters[] (챕터)

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `chapterIndex` | Integer | ✅ | 챕터 순서 (0-based) |
| `title` | String | ✅ | 챕터 제목 |
| `durationSec` | Integer | ✅ | 챕터 길이(초) |
| `scenes` | Array | ✅ | 씬 목록 |

> ⚠️ **참고**: `chapterId`는 백엔드에서 JPA `@GeneratedValue`로 자동 생성됩니다.

#### script.chapters[].scenes[] (씬)

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `sceneIndex` | Integer | ✅ | 씬 순서 (0-based) |
| `purpose` | String | ✅ | 씬 목적 (hook, explain, example 등) |
| `narration` | String | ✅ | 내레이션 텍스트 |
| `caption` | String | ❌ | 자막 텍스트 |
| `visual` | String | ❌ | 시각 연출 설명 |
| `durationSec` | Integer | ✅ | 씬 길이(초) |
| `confidenceScore` | Float | ❌ | AI 신뢰도 점수 (0.0~1.0) |
| `sourceRefs` | Array | ❌ | 출처 참조 목록 |

> ⚠️ **참고**: `sceneId`는 백엔드에서 JPA `@GeneratedValue`로 자동 생성됩니다.

#### script.chapters[].scenes[].sourceRefs[] (출처 참조)

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `documentId` | String | ✅ | 원본 문서 ID |
| `chunkIndex` | Integer | ✅ | 청크 인덱스 |

---

### 2.4 Response

#### 성공 응답 (200 OK)

```json
{
  "saved": true,
  "scriptId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `saved` | Boolean | 저장 성공 여부 |
| `scriptId` | UUID | 생성된 스크립트 ID (성공 시) |

#### 에러 응답

| 상태 코드 | 설명 |
|----------|------|
| `400` | 잘못된 요청 (필수 필드 누락 등) |
| `401` | 내부 토큰 인증 실패 |
| `404` | 소스셋을 찾을 수 없음 |
| `500` | 서버 내부 오류 |

---

## 3. 백엔드 처리 로직

### 3.1 상태 업데이트 로직

```java
// 1. SourceSet 상태 업데이트
sourceSet.setStatus(callback.sourceSetStatus());

// 2. 실패 시 에러 정보 저장
if ("FAILED".equals(callback.status()) || "FAILED".equals(callback.sourceSetStatus())) {
    sourceSet.setErrorCode(callback.errorCode());
    sourceSet.setFailReason(callback.errorMessage());
}

// 3. 문서별 상태 업데이트
callback.documents().forEach(doc -> {
    if ("FAILED".equals(doc.status())) {
        ssd.markFailed(doc.status(), doc.failReason());
    } else if ("COMPLETED".equals(doc.status())) {
        ssd.markCompleted();
    }
});

// 4. 성공 시 스크립트 저장
if ("COMPLETED".equals(callback.status()) && callback.script() != null) {
    scriptId = saveScriptFromCallback(sourceSet, callback.script());
    video.setScriptId(scriptId);
    video.setStatus("SCRIPT_READY");
}
```

### 3.2 에러 처리

| 시나리오 | errorCode | failReason |
|----------|-----------|------------|
| AI 콜백 상태 FAILED | `callback.errorCode()` | `callback.errorMessage()` |
| 스크립트 저장 실패 | `SCRIPT_SAVE_ERROR` | `스크립트 저장 실패: {exception}` |
| 스크립트가 null | `SCRIPT_NULL` | `콜백에 스크립트 데이터가 없습니다` |
| 알 수 없는 오류 | `UNKNOWN_ERROR` | `알 수 없는 오류` |

---

## 4. 실패 케이스 예시

### 4.1 문서 처리 실패

```json
{
  "videoId": "97fb8f4e-7812-40a1-8cbc-0eed5d7876ad",
  "status": "FAILED",
  "sourceSetStatus": "FAILED",
  "documents": [
    {
      "documentId": "877f4068-8b38-4958-9d44-5d6fa390f1a0",
      "status": "FAILED",
      "failReason": "PDF 파싱 실패: 암호화된 문서"
    }
  ],
  "script": null,
  "errorCode": "DOCUMENT_PARSE_ERROR",
  "errorMessage": "일부 문서 처리에 실패했습니다",
  "requestId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "traceId": "trace-abc123"
}
```

### 4.2 스크립트 생성 실패

```json
{
  "videoId": "97fb8f4e-7812-40a1-8cbc-0eed5d7876ad",
  "status": "FAILED",
  "sourceSetStatus": "FAILED",
  "documents": [
    {
      "documentId": "877f4068-8b38-4958-9d44-5d6fa390f1a0",
      "status": "COMPLETED",
      "failReason": null
    }
  ],
  "script": null,
  "errorCode": "SCRIPT_GENERATION_ERROR",
  "errorMessage": "LLM 호출 타임아웃",
  "requestId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "traceId": "trace-abc123"
}
```

---

## 5. 관련 DB 테이블

### 5.1 source_set

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID | PK |
| `status` | VARCHAR(20) | `CREATED`, `PROCESSING`, `SCRIPT_READY`, `FAILED`, `LOCKED` |
| `error_code` | VARCHAR(50) | 에러 코드 |
| `fail_reason` | VARCHAR(1000) | 실패 사유 |

### 5.2 source_set_document

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID | PK |
| `source_set_id` | UUID | FK → source_set |
| `document_id` | UUID | 문서 ID |
| `status` | VARCHAR(20) | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED` |
| `error_code` | VARCHAR(50) | 에러 코드 |
| `fail_reason` | VARCHAR(1000) | 실패 사유 |

### 5.3 education_script

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID | PK (자동 생성) |
| `education_id` | UUID | 교육 ID |
| `source_set_id` | UUID | 소스셋 ID |
| `title` | VARCHAR | 스크립트 제목 |
| `version` | INTEGER | 버전 |

### 5.4 education_script_chapter

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID | PK (자동 생성 = chapterId) |
| `script_id` | UUID | FK → education_script |
| `chapter_index` | INTEGER | 챕터 순서 |

### 5.5 education_script_scene

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID | PK (자동 생성 = sceneId) |
| `chapter_id` | UUID | FK → education_script_chapter |
| `scene_index` | INTEGER | 씬 순서 |
| `source_refs` | JSONB | 출처 참조 목록 |

---

## 6. 호출 예시 (cURL)

```bash
curl -X POST "http://localhost:9002/internal/callbacks/source-sets/d0eba687-43d6-461b-8967-a5f0322025a5/complete" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: ${INTERNAL_TOKEN}" \
  -d '{
    "videoId": "97fb8f4e-7812-40a1-8cbc-0eed5d7876ad",
    "status": "COMPLETED",
    "sourceSetStatus": "SCRIPT_READY",
    "documents": [
      {
        "documentId": "877f4068-8b38-4958-9d44-5d6fa390f1a0",
        "status": "COMPLETED",
        "failReason": null
      }
    ],
    "script": {
      "educationId": "0f1beedd-05ad-4717-a9da-508e98c556a8",
      "sourceSetId": "d0eba687-43d6-461b-8967-a5f0322025a5",
      "title": "직장내괴롭힘 교육",
      "totalDurationSec": 720,
      "version": 1,
      "llmModel": "gpt-4o",
      "chapters": []
    },
    "requestId": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
  }'
```

---

## 7. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2025-12-23 | v1.0 | 최초 작성 |
| 2025-12-23 | v1.0 | `chapterId`, `sceneId` 요청에서 제외 (백엔드 자동 생성) |
| 2025-12-23 | v1.0 | SourceSet, SourceSetDocument에 에러 필드 추가 |

