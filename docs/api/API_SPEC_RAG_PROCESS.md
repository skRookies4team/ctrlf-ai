# API 설계서: /ai/rag/process

> **버전**: 1.0  
> **작성일**: 2025-12-16  
> **담당**: AI팀

---

## 1. 기본 정보

| 항목 | 내용 |
|------|------|
| **URL** | `POST /ai/rag/process` |
| **설명** | 문서를 RAGFlow에 인덱싱하여 RAG 검색이 가능하도록 처리 |
| **권한** | 내부 백엔드 전용 (관리자 권한 필요) |
| **인증** | 현재 미적용 (추후 IP 제한 또는 토큰 인증 예정) |

---

## 2. 상세 설명

### 용도
- 관리자가 새로운 사규/교육/사고 문서를 등록할 때 호출
- 백엔드에서 파일 업로드 완료 후, AI Gateway를 통해 RAGFlow에 인덱싱 요청
- 인덱싱 완료 후 해당 문서가 RAG 검색 대상에 포함됨

### 처리 흐름
```
Backend → AI Gateway → RAGFlow
   │          │            │
   │          │            ├─ 파일 다운로드
   │          │            ├─ 텍스트 추출
   │          │            ├─ 청킹 (Chunking)
   │          │            ├─ 임베딩 생성
   │          │            └─ 벡터 DB 저장
   │          │
   │          └─ 결과 반환
   │
   └─ 문서 상태 업데이트
```

---

## 3. Request

### 3.1 Headers

| Header | 값 | 필수 |
|--------|-----|------|
| `Content-Type` | `application/json` | ✅ |

### 3.2 Body

```json
{
  "doc_id": "DOC-2025-00001",
  "file_url": "https://files.internal/documents/DOC-2025-00001.pdf",
  "domain": "POLICY",
  "acl": {
    "roles": ["EMPLOYEE", "ADMIN"],
    "departments": ["전체"]
  }
}
```

### 3.3 필드 상세

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `doc_id` | string | ✅ | 백엔드에서 관리하는 문서 ID |
| `file_url` | string (URL) | ✅ | 문서 파일 다운로드 URL |
| `domain` | string | ✅ | 문서 도메인 (아래 목록 참조) |
| `acl` | object | ❌ | 문서 접근 제어 설정 |
| `acl.roles` | string[] | ❌ | 접근 가능 역할 목록 |
| `acl.departments` | string[] | ❌ | 접근 가능 부서 목록 |

### 3.4 domain 값

| 값 | 설명 |
|----|------|
| `POLICY` | 인사/경영 정책 문서 |
| `INCIDENT` | 사건/사고 관련 문서 |
| `EDUCATION` | 교육/훈련 자료 |
| `SECURITY` | 보안 정책 문서 |
| `TRAINING` | 교육 콘텐츠 |

---

## 4. Response

### 4.1 성공 응답

```json
{
  "doc_id": "DOC-2025-00001",
  "success": true,
  "message": "Document successfully processed and indexed"
}
```

### 4.2 실패 응답

```json
{
  "doc_id": "DOC-2025-00001",
  "success": false,
  "message": "RAGFlow integration failed: TimeoutError: Connection timed out"
}
```

### 4.3 필드 상세

| 필드 | 타입 | 설명 |
|------|------|------|
| `doc_id` | string | 요청에서 전달받은 문서 ID |
| `success` | boolean | 처리 성공 여부 |
| `message` | string? | 추가 설명 또는 에러 메시지 |

---

## 5. Status Code

| 코드 | 상태 | 설명 |
|------|------|------|
| `200` | OK | 요청 처리 완료 (success 필드로 성공/실패 구분) |
| `422` | Unprocessable Entity | 요청 유효성 검사 실패 (필수 필드 누락 등) |
| `500` | Internal Server Error | 서버 내부 오류 |

> **참고**: 이 API는 RAGFlow 실패 시에도 HTTP 200을 반환하고, `success: false`로 실패를 표시합니다.

---

## 6. 에러 케이스

| 케이스 | success | message 예시 |
|--------|---------|--------------|
| RAGFlow 미설정 | `true` | "RAG document processing dummy response. RAGFLOW_BASE_URL is not configured." |
| RAGFlow 타임아웃 | `false` | "RAGFlow integration failed: TimeoutError: ..." |
| RAGFlow 서버 오류 | `false` | "RAGFlow integration failed: HTTPStatusError: 500" |
| 파일 다운로드 실패 | `false` | "RAGFlow integration failed: File download error" |
| 예기치 않은 오류 | `false` | "RAGFlow integration failed: {ErrorType}: {message}" |

---

## 7. 사용 예시

### 7.1 curl

```bash
curl -X POST http://localhost:8000/ai/rag/process \
  -H "Content-Type: application/json" \
  -d '{
    "doc_id": "DOC-2025-00001",
    "file_url": "https://files.internal/documents/policy_v3.pdf",
    "domain": "POLICY",
    "acl": {
      "roles": ["EMPLOYEE"],
      "departments": ["전체"]
    }
  }'
```

### 7.2 Java/Spring

```java
@Data
public class RagProcessRequest {
    private String docId;
    private String fileUrl;
    private String domain;
    private RagAcl acl;
    
    @Data
    public static class RagAcl {
        private List<String> roles;
        private List<String> departments;
    }
}

@Data
public class RagProcessResponse {
    private String docId;
    private Boolean success;
    private String message;
}

// Service
@Service
public class RagProcessService {
    private final WebClient webClient;
    
    public Mono<RagProcessResponse> processDocument(RagProcessRequest request) {
        return webClient.post()
            .uri("/ai/rag/process")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(request)
            .retrieve()
            .bodyToMono(RagProcessResponse.class)
            .timeout(Duration.ofSeconds(60));
    }
}
```

---

## 8. 주의사항

### 8.1 타임아웃
- RAGFlow 문서 처리는 시간이 오래 걸릴 수 있음
- 백엔드에서 **60초 이상** 타임아웃 설정 권장
- 대용량 문서(100페이지 이상)는 더 긴 타임아웃 필요

### 8.2 파일 URL 접근성
- `file_url`은 AI Gateway 및 RAGFlow 서버에서 접근 가능해야 함
- 내부망 URL 사용 시 네트워크 설정 확인 필요
- 인증이 필요한 URL은 현재 미지원

### 8.3 중복 처리
- 동일한 `doc_id`로 재요청 시 기존 인덱스 덮어쓰기
- 문서 버전 관리가 필요하면 `doc_id`에 버전 포함 권장 (예: `DOC-001-v2`)

### 8.4 ACL 처리
- 현재 ACL은 메타데이터로만 저장
- 실제 접근 제어는 백엔드에서 구현 필요

---

## 9. 관련 API

| API | 설명 |
|-----|------|
| `/ingest` | Phase 19 신규 문서 인덱싱 API (권장) |
| `/search` | RAG 검색 API |
| `/ai/chat/messages` | 채팅 응답 생성 API |

---

## 10. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0 | 2025-12-16 | 최초 작성 |
