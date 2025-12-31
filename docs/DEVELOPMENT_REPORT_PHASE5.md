# Phase 5: AI 로그 시스템 구현 보고서

## 1. 개요

### 1.1 목적
AI 게이트웨이에서 생성되는 Intent/Route/PII 메타데이터를 턴 단위로 백엔드 DB에 저장하여, 도메인별/라우트별 질문 비율, PII 검출 비율, RAG 사용 비율 등의 지표를 추출하고 보안/컴플라이언스 확인에 활용할 수 있도록 합니다.

### 1.2 요구사항 출처
- `prompt.txt`: 백엔드 팀과 공유할 AI 로그 테이블 스키마 및 필드 정의

### 1.3 구현 범위
- AI 로그 데이터 모델 정의
- ChatResponse 메타데이터 확장
- 로그 생성 및 백엔드 전송 서비스
- 테스트 코드

---

## 2. 아키텍처

### 2.1 데이터 흐름

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         ChatService Pipeline                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  [1] User Query ──► [2] PII Mask (INPUT) ──► [3] Intent Classification  │
│                            │                          │                  │
│                            ▼                          ▼                  │
│                     has_pii_input              intent, domain, route     │
│                                                                          │
│  [4] RAG Search ──► [5] LLM Generation ──► [6] PII Mask (OUTPUT)        │
│         │                   │                        │                   │
│         ▼                   ▼                        ▼                   │
│   rag_used,            model_name              has_pii_output            │
│   rag_source_count                                                       │
│                                                                          │
│  [7] Create AI Log Entry ──► [8] PII Mask (LOG) ──► [9] Send to Backend │
│                                      │                      │            │
│                                      ▼                      ▼            │
│                              question_masked         POST /api/ai-logs   │
│                              answer_masked           (fire-and-forget)   │
│                                                                          │
│  [10] Return ChatResponse                                                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 컴포넌트 구조

```
app/
├── models/
│   ├── ai_log.py          # [신규] AI 로그 모델
│   ├── chat.py            # [수정] ChatAnswerMeta 확장
│   └── __init__.py        # [수정] 모델 export 추가
├── services/
│   ├── ai_log_service.py  # [신규] AI 로그 서비스
│   └── chat_service.py    # [수정] 로그 생성 통합
├── clients/
│   └── backend_client.py  # [신규] 백엔드 API 클라이언트
└── tests/
    └── test_ai_log.py     # [신규] 테스트 코드
```

---

## 3. 구현 상세

### 3.1 AI 로그 모델 (`app/models/ai_log.py`)

#### AILogEntry

백엔드 DB에 저장될 턴 단위 로그 데이터 스키마입니다.

| 필드 | 타입 | 필수 | 설명 |
|------|------|:----:|------|
| `session_id` | str | ✓ | 채팅 세션 ID |
| `user_id` | str | ✓ | 사용자 ID / 사번 |
| `turn_index` | int | - | 세션 내 턴 인덱스 (0부터) |
| `channel` | str | ✓ | 요청 채널 (WEB, MOBILE 등) |
| `user_role` | str | ✓ | 사용자 역할 (EMPLOYEE, MANAGER, ADMIN) |
| `department` | str | - | 사용자 부서 |
| `domain` | str | ✓ | 게이트웨이에서 보정한 도메인 |
| `intent` | str | ✓ | 질문 의도 (IntentType) |
| `route` | str | ✓ | 라우팅 결과 (RouteType) |
| `has_pii_input` | bool | ✓ | 입력 PII 검출 여부 |
| `has_pii_output` | bool | ✓ | 출력 PII 검출 여부 |
| `model_name` | str | - | 사용된 LLM 모델명 |
| `rag_used` | bool | ✓ | RAG 검색 사용 여부 |
| `rag_source_count` | int | ✓ | RAG 검색 문서 개수 |
| `latency_ms` | int | ✓ | 전체 처리 시간 (ms) |
| `error_code` | str | - | 에러 코드 |
| `error_message` | str | - | 에러 메시지 |
| `question_masked` | str | - | LOG 단계 마스킹된 질문 |
| `answer_masked` | str | - | LOG 단계 마스킹된 답변 |

#### AILogRequest / AILogResponse

```python
# 백엔드 전송용 요청 래퍼
class AILogRequest(BaseModel):
    log: AILogEntry

# 백엔드 응답
class AILogResponse(BaseModel):
    success: bool
    log_id: Optional[str]
    message: Optional[str]
```

### 3.2 ChatAnswerMeta 확장 (`app/models/chat.py`)

기존 필드에 추가된 새 필드:

| 필드 | 타입 | 설명 |
|------|------|------|
| `intent` | str | 분류된 의도 (POLICY_QA, INCIDENT_REPORT 등) |
| `domain` | str | 보정된 도메인 (POLICY, INCIDENT, EDUCATION) |
| `has_pii_input` | bool | 입력에서 PII 검출 여부 |
| `has_pii_output` | bool | 출력에서 PII 검출 여부 |
| `rag_used` | bool | RAG 검색 수행 여부 |
| `rag_source_count` | int | RAG 검색 문서 개수 |

**응답 예시:**
```json
{
  "answer": "연차는 다음 해로 최대 10일까지 이월 가능합니다.",
  "sources": [...],
  "meta": {
    "used_model": "internal-llm",
    "route": "ROUTE_RAG_INTERNAL",
    "intent": "POLICY_QA",
    "domain": "POLICY",
    "masked": true,
    "has_pii_input": true,
    "has_pii_output": false,
    "rag_used": true,
    "rag_source_count": 3,
    "latency_ms": 1500
  }
}
```

### 3.3 AI 로그 서비스 (`app/services/ai_log_service.py`)

#### 주요 메서드

| 메서드 | 설명 |
|--------|------|
| `create_log_entry()` | ChatRequest/Response로부터 AILogEntry 생성 |
| `mask_for_log()` | LOG 단계 PII 마스킹 적용 |
| `send_log()` | 백엔드로 로그 전송 (동기) |
| `send_log_async()` | 백엔드로 로그 전송 (비동기, fire-and-forget) |

#### 핵심 특징

1. **PII 원문 미저장**: LOG 단계에서 강화된 마스킹 적용
2. **Fire-and-forget**: 메인 응답 latency에 영향 없음
3. **Graceful Degradation**: 전송 실패 시 로컬 로그만 기록
4. **설정 유연성**: BACKEND_BASE_URL 미설정 시 로컬 로그만

### 3.4 ChatService 통합 (`app/services/chat_service.py`)

```python
# 파이프라인 마지막 단계에 추가
async def _send_ai_log(self, ...):
    # 1. LOG 단계 PII 마스킹
    question_masked, answer_masked = await self._ai_log.mask_for_log(...)

    # 2. 로그 엔트리 생성
    log_entry = self._ai_log.create_log_entry(...)

    # 3. 비동기 전송 (fire-and-forget)
    asyncio.create_task(self._ai_log.send_log_async(log_entry))
```

---

## 4. 백엔드 연동 가이드

### 4.1 환경 설정

```bash
# .env
BACKEND_BASE_URL=http://localhost:8080
```

### 4.2 백엔드 API 엔드포인트

**POST** `/api/ai-logs`

**Request Body:**
```json
{
  "log": {
    "session_id": "session-123",
    "user_id": "emp-001",
    "turn_index": 0,
    "channel": "WEB",
    "user_role": "EMPLOYEE",
    "department": "개발팀",
    "domain": "POLICY",
    "intent": "POLICY_QA",
    "route": "ROUTE_RAG_INTERNAL",
    "has_pii_input": true,
    "has_pii_output": false,
    "model_name": "internal-llm",
    "rag_used": true,
    "rag_source_count": 3,
    "latency_ms": 1500,
    "error_code": null,
    "error_message": null,
    "question_masked": "[NAME] 사원의 연차 규정이 어떻게 되나요?",
    "answer_masked": "연차는 입사일 기준으로 발생합니다."
  }
}
```

**Response:**
```json
{
  "success": true,
  "log_id": "log-abc-123",
  "message": "Log saved successfully"
}
```

### 4.3 백엔드 DB 테이블 스키마 (참고용)

```sql
CREATE TABLE ai_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- 세션/사용자 정보
    session_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    turn_index INTEGER,
    channel VARCHAR(50) DEFAULT 'WEB',
    user_role VARCHAR(50) NOT NULL,
    department VARCHAR(100),

    -- 의도/라우팅 정보
    domain VARCHAR(50) NOT NULL,
    intent VARCHAR(50) NOT NULL,
    route VARCHAR(50) NOT NULL,

    -- PII 마스킹 정보
    has_pii_input BOOLEAN DEFAULT FALSE,
    has_pii_output BOOLEAN DEFAULT FALSE,

    -- 모델/RAG 정보
    model_name VARCHAR(100),
    rag_used BOOLEAN DEFAULT FALSE,
    rag_source_count INTEGER DEFAULT 0,

    -- 성능 정보
    latency_ms INTEGER NOT NULL,

    -- 에러 정보
    error_code VARCHAR(50),
    error_message TEXT,

    -- LOG 단계 마스킹 텍스트
    question_masked TEXT,
    answer_masked TEXT
);

-- 인덱스
CREATE INDEX idx_ai_log_session ON ai_log(session_id);
CREATE INDEX idx_ai_log_user ON ai_log(user_id);
CREATE INDEX idx_ai_log_created ON ai_log(created_at);
CREATE INDEX idx_ai_log_domain ON ai_log(domain);
CREATE INDEX idx_ai_log_intent ON ai_log(intent);
CREATE INDEX idx_ai_log_route ON ai_log(route);
```

---

## 5. 테스트 결과

### 5.1 테스트 실행

```bash
$ python -m pytest tests/test_ai_log.py -v

tests/test_ai_log.py::TestAILogEntry::test_create_minimal_log_entry PASSED
tests/test_ai_log.py::TestAILogEntry::test_create_full_log_entry PASSED
tests/test_ai_log.py::TestAILogEntry::test_log_entry_serialization PASSED
tests/test_ai_log.py::TestAILogRequest::test_wrap_log_entry PASSED
tests/test_ai_log.py::TestAILogResponse::test_success_response PASSED
tests/test_ai_log.py::TestAILogResponse::test_failure_response PASSED
tests/test_ai_log.py::TestAILogService::test_create_log_entry PASSED
tests/test_ai_log.py::TestAILogService::test_send_log_without_backend PASSED
tests/test_ai_log.py::TestAILogService::test_mask_for_log PASSED
tests/test_ai_log.py::TestChatAnswerMetaExtended::test_extended_meta_fields PASSED
tests/test_ai_log.py::TestChatAnswerMetaExtended::test_meta_serialization PASSED

============================= 13 passed ==============================
```

### 5.2 전체 테스트

```bash
$ python -m pytest tests/ -v

============================= 75 passed ==============================
```

---

## 6. 파일 변경 목록

### 6.1 신규 파일

| 파일 | 라인 수 | 설명 |
|------|:------:|------|
| `app/models/ai_log.py` | 155 | AI 로그 모델 정의 |
| `app/services/ai_log_service.py` | 210 | 로그 생성/전송 서비스 |
| `app/clients/backend_client.py` | 140 | 백엔드 API 클라이언트 |
| `tests/test_ai_log.py` | 200 | 테스트 코드 |
| `docs/phase5-ai-log-report.md` | - | 본 보고서 |

### 6.2 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/models/chat.py` | ChatAnswerMeta에 6개 필드 추가 |
| `app/models/__init__.py` | 새 모델 export 추가 |
| `app/services/chat_service.py` | AILogService 통합, _send_ai_log() 메서드 추가 |
| `.env.example` | BACKEND_BASE_URL 설명 보완 |
| `tests/test_pii_http_integration.py` | LOG 단계 호출 반영 |

---

## 7. 활용 방안

### 7.1 지표 분석

- **도메인별 질문 비율**: `GROUP BY domain`
- **라우트별 사용 비율**: `GROUP BY route`
- **PII 검출 비율**: `AVG(has_pii_input::int)`
- **RAG 사용 비율**: `AVG(rag_used::int)`
- **평균 응답 시간**: `AVG(latency_ms)`

### 7.2 보안/컴플라이언스 확인

- INCIDENT 질문이 ROUTE_INCIDENT만 탔는지 검증
- POLICY 관련 질문이 외부 LLM으로 나가지 않았는지 확인
- PII가 포함된 요청의 마스킹 처리 여부 모니터링

### 7.3 모델 튜닝

- Intent/Route 오분류 케이스 추출
- 사용자 피드백과 연계하여 품질 개선

---

## 8. 향후 개선 사항

1. **배치 로그 전송**: 개별 전송 대신 일정 기간 모아서 전송
2. **로그 압축**: 대용량 텍스트 필드 압축
3. **메트릭 수집**: Prometheus/Grafana 연동
4. **실시간 대시보드**: 로그 기반 실시간 모니터링
5. **로그 보존 정책**: 오래된 로그 자동 아카이브/삭제

---

## 9. 결론

Phase 5에서 AI 로그 시스템을 성공적으로 구현했습니다. 이제 AI 게이트웨이의 모든 채팅 요청에 대해 Intent, Route, PII 마스킹 여부, RAG 사용 여부 등의 메타데이터가 자동으로 수집되어 백엔드로 전송됩니다.

백엔드 팀에서 `/api/ai-logs` 엔드포인트와 DB 테이블을 구현하면 즉시 연동 가능합니다.

---

**작성일**: 2025-12-08
**작성자**: AI Gateway Team
**버전**: 1.0
