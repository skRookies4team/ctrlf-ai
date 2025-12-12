# AI Gateway 백엔드 연동 가이드

> **작성일**: 2025-12-11
> **대상**: ctrlf-back (Spring Backend) 개발팀
> **버전**: Phase 15 완료

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [환경 설정 및 실행](#2-환경-설정-및-실행)
3. [API 엔드포인트 목록](#3-api-엔드포인트-목록)
4. [채팅 API 연동 가이드](#4-채팅-api-연동-가이드)
5. [RAG Gap 제안 API 연동 가이드](#5-rag-gap-제안-api-연동-가이드)
6. [에러 처리](#6-에러-처리)
7. [연동 체크리스트](#7-연동-체크리스트)

---

## 1. 프로젝트 개요

### 1.1 AI Gateway란?

AI Gateway는 사용자 질문을 받아 RAG(검색) + LLM(생성)을 거쳐 답변을 반환하는 FastAPI 서버입니다.

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│ ctrlf-back  │────▶│  AI Gateway     │────▶│  RAGFlow    │
│  (Spring)   │     │  (FastAPI)      │     │  (검색엔진)  │
└─────────────┘     └────────┬────────┘     └─────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  LLM Server     │
                    │  (Qwen2.5-7B)   │
                    └─────────────────┘
```

### 1.2 주요 기능

| 기능 | 설명 |
|------|------|
| 채팅 응답 생성 | 사규/교육/사고 관련 질문에 AI 답변 |
| PII 마스킹 | 개인정보 자동 탐지 및 마스킹 |
| 역할별 라우팅 | EMPLOYEE/ADMIN별 다른 처리 로직 |
| RAG Gap 분석 | 문서 부족 질문 식별 및 보완 제안 |

---

## 2. 환경 설정 및 실행

### 2.1 사전 요구사항

- Python 3.12.7
- pip (패키지 관리자)

### 2.2 설치 및 실행

```bash
# 1. 프로젝트 클론
git clone https://github.com/skRookies4team/ctrlf-ai.git
cd ctrlf-ai

# 2. 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 의존성 설치
pip install -r requirements.txt

# 4. 환경변수 설정
cp .env.example .env
# .env 파일 편집하여 필요한 값 설정

# 5. 서버 실행
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2.3 환경변수 (.env)

```env
# 앱 설정
APP_NAME=ctrlf-ai-gateway
APP_ENV=development

# LLM 서버 (필수)
LLM_BASE_URL=http://your-llm-server:port/v1

# RAGFlow 서버 (RAG 검색용)
RAGFLOW_BASE_URL=http://your-ragflow-server:9380

# PII 마스킹 서버 (선택)
PII_BASE_URL=http://your-pii-server:8000
PII_ENABLED=true

# 백엔드 서버 (로그 전송용)
BACKEND_BASE_URL=http://localhost:8080
```

### 2.4 API 문서 확인

서버 실행 후 브라우저에서:

| URL | 설명 |
|-----|------|
| http://localhost:8000/docs | **Swagger UI** (추천) |
| http://localhost:8000/redoc | ReDoc 문서 |
| http://localhost:8000/health | 헬스체크 |

---

## 3. API 엔드포인트 목록

| 엔드포인트 | 메서드 | 설명 | 용도 |
|-----------|--------|------|------|
| `/health` | GET | 서버 상태 확인 | 헬스체크 |
| `/health/ready` | GET | 준비 상태 확인 | K8s Readiness |
| `/ai/chat/messages` | POST | **채팅 응답 생성** | 메인 채팅 API |
| `/ai/rag/process` | POST | RAG 문서 처리 | 내부용 |
| `/ai/gap/policy-edu/suggestions` | POST | RAG Gap 보완 제안 | 관리자용 |

---

## 4. 채팅 API 연동 가이드

### 4.1 기본 플로우

```
[사용자] → [ctrlf-back] → [AI Gateway] → [RAGFlow + LLM] → [AI Gateway] → [ctrlf-back] → [사용자]
```

**상세 플로우:**

```
1. 사용자가 프론트엔드에서 질문 입력
2. ctrlf-back이 사용자 정보와 함께 AI Gateway 호출
3. AI Gateway 처리:
   ├─ PII 마스킹 (개인정보 제거)
   ├─ Intent 분류 (질문 유형 파악)
   ├─ RAG 검색 (관련 문서 찾기)
   ├─ LLM 응답 생성
   └─ PII 마스킹 (응답에서 개인정보 제거)
4. AI Gateway → ctrlf-back 응답 반환
5. ctrlf-back → 프론트엔드 → 사용자
```

### 4.2 요청 스펙

**엔드포인트:** `POST /ai/chat/messages`

**Request Body:**

```json
{
  "session_id": "sess-uuid-1234",
  "user_id": "emp-001",
  "user_role": "EMPLOYEE",
  "domain": "POLICY",
  "department": "개발팀",
  "channel": "WEB",
  "messages": [
    {
      "role": "user",
      "content": "연차 이월 규정이 어떻게 되나요?"
    }
  ]
}
```

**필드 설명:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | string | ✅ | 세션 식별자 (대화 컨텍스트 유지용) |
| `user_id` | string | ✅ | 사용자 ID |
| `user_role` | string | ✅ | 역할: `EMPLOYEE`, `ADMIN`, `INCIDENT_MANAGER` |
| `domain` | string | ❌ | 도메인 힌트: `POLICY`, `EDU`, `INCIDENT` |
| `department` | string | ❌ | 부서명 |
| `channel` | string | ❌ | 채널: `WEB`, `MOBILE`, `SLACK` |
| `messages` | array | ✅ | 메시지 배열 (최소 1개) |
| `messages[].role` | string | ✅ | `user` 또는 `assistant` |
| `messages[].content` | string | ✅ | 메시지 내용 |

### 4.3 응답 스펙

**Response Body:**

```json
{
  "answer": "연차휴가는 다음 해 말일까지 최대 10일까지 이월할 수 있습니다.\n\n[참고 근거]\n- 연차휴가 관리 규정 제10조 (연차 이월) 제2항",
  "sources": [
    {
      "doc_id": "doc-001",
      "title": "연차휴가 관리 규정",
      "page": 5,
      "score": 0.92,
      "snippet": "연차는 다음 해 말일까지 최대 10일까지 이월할 수 있다...",
      "article_label": "제10조 (연차 이월) 제2항",
      "article_path": "제2장 > 제10조 > 제2항"
    }
  ],
  "meta": {
    "user_role": "EMPLOYEE",
    "used_model": "internal-llm",
    "route": "RAG_INTERNAL",
    "intent": "POLICY_QA",
    "domain": "POLICY",
    "masked": false,
    "has_pii_input": false,
    "has_pii_output": false,
    "rag_used": true,
    "rag_source_count": 1,
    "latency_ms": 1250,
    "rag_latency_ms": 350,
    "llm_latency_ms": 850,
    "rag_gap_candidate": false
  }
}
```

**응답 필드 설명:**

| 필드 | 타입 | 설명 |
|------|------|------|
| `answer` | string | AI 생성 답변 |
| `sources` | array | RAG 검색 결과 (근거 문서) |
| `sources[].doc_id` | string | 문서 ID |
| `sources[].title` | string | 문서 제목 |
| `sources[].score` | float | 관련도 점수 (0~1) |
| `sources[].snippet` | string | 발췌 내용 |
| `sources[].article_label` | string | 조항 라벨 (예: 제10조 제2항) |
| `meta.route` | string | 처리 경로 |
| `meta.intent` | string | 분류된 의도 |
| `meta.rag_used` | boolean | RAG 사용 여부 |
| `meta.latency_ms` | int | 전체 처리 시간 (ms) |
| `meta.rag_gap_candidate` | boolean | RAG Gap 후보 여부 |

### 4.4 역할별 처리 차이

| 역할 | 설명 | 특징 |
|------|------|------|
| `EMPLOYEE` | 일반 직원 | 사규 질의, 교육 현황 조회, 사고 신고 |
| `ADMIN` | 관리자 | 부서 통계 조회, 전체 현황 파악 |
| `INCIDENT_MANAGER` | 사고 담당자 | 사고 현황 조회, 상세 분석 |

### 4.5 Intent(의도) 종류

| Intent | 설명 | Route |
|--------|------|-------|
| `POLICY_QA` | 사규/정책 질문 | RAG_INTERNAL |
| `EDUCATION_QA` | 교육 내용 질문 | RAG_INTERNAL |
| `EDU_STATUS` | 교육 현황 조회 | BACKEND_API |
| `INCIDENT_REPORT` | 사고 신고 | BACKEND_API |
| `INCIDENT_QA` | 사고 관련 질문 | MIXED_BACKEND_RAG |
| `GENERAL_CHAT` | 일반 대화 | LLM_ONLY |

### 4.6 Java/Spring 연동 예시

```java
@Service
public class AiGatewayClient {

    private final WebClient webClient;

    public AiGatewayClient(@Value("${ai.gateway.url}") String baseUrl) {
        this.webClient = WebClient.builder()
            .baseUrl(baseUrl)
            .build();
    }

    public Mono<ChatResponse> chat(ChatRequest request) {
        return webClient.post()
            .uri("/ai/chat/messages")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(request)
            .retrieve()
            .bodyToMono(ChatResponse.class);
    }
}

// DTO
@Data
public class ChatRequest {
    private String sessionId;
    private String userId;
    private String userRole;
    private String domain;
    private String department;
    private List<Message> messages;

    @Data
    public static class Message {
        private String role;
        private String content;
    }
}

@Data
public class ChatResponse {
    private String answer;
    private List<Source> sources;
    private Meta meta;

    @Data
    public static class Source {
        private String docId;
        private String title;
        private Integer page;
        private Double score;
        private String snippet;
        private String articleLabel;
        private String articlePath;
    }

    @Data
    public static class Meta {
        private String userRole;
        private String route;
        private String intent;
        private String domain;
        private Boolean ragUsed;
        private Integer ragSourceCount;
        private Integer latencyMs;
        private Boolean ragGapCandidate;
    }
}
```

### 4.7 curl 테스트 예시

```bash
# 기본 채팅 요청
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-001",
    "user_id": "emp-001",
    "user_role": "EMPLOYEE",
    "domain": "POLICY",
    "messages": [
      {"role": "user", "content": "연차 이월 규정이 어떻게 되나요?"}
    ]
  }'

# 교육 현황 조회 (EMPLOYEE)
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-002",
    "user_id": "emp-001",
    "user_role": "EMPLOYEE",
    "messages": [
      {"role": "user", "content": "내 교육 이수 현황 알려줘"}
    ]
  }'

# 관리자 부서 통계 조회
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-003",
    "user_id": "admin-001",
    "user_role": "ADMIN",
    "department": "개발팀",
    "messages": [
      {"role": "user", "content": "우리 부서 교육 이수율 알려줘"}
    ]
  }'
```

---

## 5. RAG Gap 제안 API 연동 가이드

### 5.1 용도

- 관리자 대시보드에서 "RAG Gap 질문"을 수집한 후
- AI Gateway에 보내면 "어떤 사규/교육을 보완하면 좋을지" 제안

### 5.2 요청 스펙

**엔드포인트:** `POST /ai/gap/policy-edu/suggestions`

**Request Body:**

```json
{
  "timeRange": {
    "from": "2025-12-01T00:00:00",
    "to": "2025-12-10T23:59:59"
  },
  "domain": "POLICY",
  "questions": [
    {
      "questionId": "log-123",
      "text": "재택근무할 때 VPN 안 쓰면 어떻게 되나요?",
      "userRole": "EMPLOYEE",
      "intent": "POLICY_QA",
      "domain": "POLICY",
      "askedCount": 5
    },
    {
      "questionId": "log-456",
      "text": "개인 휴대폰으로 회사 메일 보면 보안 위반인가요?",
      "userRole": "EMPLOYEE",
      "intent": "POLICY_QA",
      "domain": "POLICY",
      "askedCount": 3
    }
  ]
}
```

### 5.3 응답 스펙

```json
{
  "summary": "재택근무 시 보안 규정과 BYOD 정책에 대한 문서가 부족합니다.",
  "suggestions": [
    {
      "id": "SUG-001",
      "title": "재택근무 시 정보보호 수칙 상세 예시 추가",
      "description": "VPN 사용 의무, 공용 Wi-Fi 금지, 화면 잠금 기준 등을 포함한 조문을 신설하는 것이 좋습니다.",
      "relatedQuestionIds": ["log-123"],
      "priority": "HIGH"
    },
    {
      "id": "SUG-002",
      "title": "개인 휴대폰/노트북 사용 가이드 조항 신설",
      "description": "BYOD(Bring Your Own Device) 정책을 명확히 하고, 어떤 경우가 위반인지 예시를 추가해야 합니다.",
      "relatedQuestionIds": ["log-456"],
      "priority": "MEDIUM"
    }
  ]
}
```

---

## 6. 에러 처리

### 6.1 HTTP 상태 코드

| 코드 | 설명 | 대응 |
|------|------|------|
| 200 | 성공 | 정상 처리 |
| 400 | 잘못된 요청 | 요청 데이터 확인 |
| 422 | 유효성 검사 실패 | 필수 필드 누락 확인 |
| 500 | 서버 내부 오류 | 재시도 또는 fallback |
| 503 | 서비스 불가 | LLM/RAG 서버 상태 확인 |

### 6.2 에러 응답 예시

```json
{
  "detail": "Validation error: messages field is required"
}
```

### 6.3 Fallback 응답

LLM/RAG 장애 시에도 응답은 반환됩니다:

```json
{
  "answer": "죄송합니다. 현재 AI 서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.",
  "sources": [],
  "meta": {
    "route": "ERROR",
    "error_type": "UPSTREAM_TIMEOUT",
    "error_message": "LLM service timeout"
  }
}
```

---

## 7. 연동 체크리스트

### 7.1 기본 연동

- [ ] AI Gateway 서버 URL 설정
- [ ] `/health` 엔드포인트로 연결 확인
- [ ] `/ai/chat/messages` 기본 호출 테스트
- [ ] 응답 파싱 및 화면 표시

### 7.2 사용자 정보 연동

- [ ] `session_id` 생성 및 관리
- [ ] `user_id` 전달
- [ ] `user_role` 매핑 (EMPLOYEE/ADMIN/INCIDENT_MANAGER)
- [ ] `department` 전달 (선택)

### 7.3 대화 컨텍스트

- [ ] 이전 대화 내역 `messages` 배열로 전달
- [ ] 멀티턴 대화 테스트

### 7.4 에러 처리

- [ ] HTTP 에러 핸들링
- [ ] Fallback 응답 처리
- [ ] 타임아웃 설정 (권장: 30초)

### 7.5 로깅/모니터링

- [ ] AI 로그 수신 API 구현 (선택)
- [ ] `meta.rag_gap_candidate=true` 질문 수집

---

## 문의

AI Gateway 관련 문의사항은 AI 팀에 연락해주세요.

- GitHub: https://github.com/skRookies4team/ctrlf-ai
- Swagger: http://[AI_GATEWAY_HOST]:8000/docs
