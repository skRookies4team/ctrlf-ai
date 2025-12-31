# Telemetry Baseline (Current State)

> **문서 목적**: 현재 AI 레포에 구현된 로깅/텔레메트리의 실제 동작을 동결(Baseline)하여,
> 이후 v1 Telemetry 계약으로 리팩토링할 때 누락/중복/꼬임을 방지한다.
>
> **작성일**: 2024-12-30
> **코드 변경**: 없음 (정적 분석만 수행)

---

## 1. Summary (한 페이지 요약)

### 1.1 현재 로깅 경로 3종 요약

| 경로               | 대상             | 포맷                                | 트리거 조건                | 성능 영향                |
| ------------------ | ---------------- | ----------------------------------- | -------------------------- | ------------------------ |
| **Console Logger** | stdout           | 텍스트 (`시간\|레벨\|모듈\|메시지`) | 항상 (LOG_LEVEL 제어)      | 최소 (동기 I/O)          |
| **DEBUG_RAG**      | stderr           | JSON 1줄                            | `DEBUG_RAG=1` 환경변수     | 최소 (조건부 출력)       |
| **AI Log Service** | Backend HTTP API | JSON (camelCase)                    | `BACKEND_BASE_URL` 설정 시 | 비동기 (fire-and-forget) |

### 1.2 가장 큰 문제 3개

| 우선순위 | 문제                             | 근거                                                                                                                             | 영향                           |
| -------- | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| **1**    | **v1 Telemetry 계약과 불일치**   | 현재 `AILogEntry`는 `ChatTurnEvent` 스키마와 필드명/구조가 다름. `trace_id`, `conversation_id` 헤더 전파 미구현                  | 백엔드 집계/대시보드 연동 불가 |
| **2**    | **PII 원문 노출 위험 경로 존재** | `PII_ENABLED=false`이거나 PII 서비스 장애 시 원문이 그대로 `question_masked`/`answer_masked`에 저장됨 (`pii_service.py:277-294`) | 보안/컴플라이언스 위험         |
| **3**    | **디버그 로그와 운영 로그 혼재** | `DEBUG_RAG` 출력이 stderr로 나가지만, Console Logger와 별개 경로라 통합 모니터링 어려움. `request_id`만 있고 `trace_id` 없음     | 분산 추적 불가                 |

### 1.3 추가 이슈

- **로그 파일 저장 미구현**: 콘솔(stdout) 출력만 있음, 파일/ELK/Datadog 연동 없음
- **FeedbackEvent 미구현**: 사용자 좋아요/싫어요 피드백 이벤트 전송 없음
- **SecurityEvent 미구현**: PII 차단/외부 도메인 차단 이벤트 전송 없음
- **turn_id 자동 관리 미구현**: `turn_index`는 외부에서 전달받아야 하며, 내부 추적 없음

---

## 2. Inventory (구현 요소 목록)

### 2.1 중앙 서비스/모듈

| Category           | File                             | Symbol                                                                               | Description                           |
| ------------------ | -------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------- |
| **Logging Core**   | `app/core/logging.py`            | `setup_logging()`, `get_logger()`                                                    | Python logging 모듈 기반, stdout 출력 |
| **AI Log Service** | `app/services/ai_log_service.py` | `AILogService`                                                                       | 백엔드 HTTP 전송, fire-and-forget     |
| **AI Log Model**   | `app/models/ai_log.py`           | `AILogEntry`, `AILogRequest`, `AILogResponse`                                        | 로그 스키마 정의                      |
| **Debug RAG Log**  | `app/utils/debug_log.py`         | `dbg_route()`, `dbg_retrieval_target()`, `dbg_final_query()`, `dbg_retrieval_top5()` | JSON 디버그 출력                      |
| **PII Service**    | `app/services/pii_service.py`    | `PiiService`                                                                         | 3단계 마스킹 (INPUT/OUTPUT/LOG)       |

### 2.2 호출지점 목록 (기능별 분류)

#### A. 채팅 파이프라인 로깅

| File                               | Line             | Function/Context           | Log Type                         | Notes                            |
| ---------------------------------- | ---------------- | -------------------------- | -------------------------------- | -------------------------------- |
| `app/services/chat_service.py`     | 502-506, 524-527 | Intent classification 결과 | Console (INFO)                   | intent, route, domain 출력       |
| `app/services/chat_service.py`     | 509-517, 530-537 | 라우팅 결정 직후           | DEBUG_RAG (dbg_route)            | request_id, intent, domain, tool |
| `app/services/chat_service.py`     | 1078-1110        | `_send_ai_log()`           | AI Log Service                   | 턴 완료 후 백엔드 전송           |
| `app/services/chat/rag_handler.py` | 548-554          | 최종 검색 쿼리             | DEBUG_RAG (dbg_final_query)      | original_query, rewritten_query  |
| `app/services/chat/rag_handler.py` | 604-612          | Milvus 검색 직전           | DEBUG_RAG (dbg_retrieval_target) | collection, domain, top_k        |
| `app/services/chat/rag_handler.py` | 733-747          | 검색 결과                  | DEBUG_RAG (dbg_retrieval_top5)   | top5 results                     |
| `app/clients/milvus_client.py`     | 672-680          | Milvus 검색 직전           | DEBUG_RAG (dbg_retrieval_target) | collection, filter_expr          |
| `app/clients/milvus_client.py`     | 712-722          | Milvus 검색 결과           | DEBUG_RAG (dbg_retrieval_top5)   | top5 results                     |

#### B. PII 마스킹 로깅

| File                          | Line             | Function/Context | Log Type             | Notes                             |
| ----------------------------- | ---------------- | ---------------- | -------------------- | --------------------------------- |
| `app/services/pii_service.py` | 134-137, 141-145 | 마스킹 스킵      | Console (DEBUG)      | 빈 문자열/비활성화 시             |
| `app/services/pii_service.py` | 190-193          | PII 서비스 호출  | Console (DEBUG)      | url, stage, text_length           |
| `app/services/pii_service.py` | 222-228          | 마스킹 결과      | Console (INFO/DEBUG) | PII 검출 시 INFO, 미검출 시 DEBUG |
| `app/services/pii_service.py` | 232-270          | 에러 처리        | Console (ERROR)      | HTTP 에러, 타임아웃, 파싱 에러    |

#### C. 스트리밍 채팅 로깅

| File                                  | Line    | Function/Context | Log Type               | Notes                  |
| ------------------------------------- | ------- | ---------------- | ---------------------- | ---------------------- |
| `app/services/chat_stream_service.py` | 372     | LLM 스트림 시작  | Console (INFO)         | request_id, user_id    |
| `app/services/chat_stream_service.py` | 487-506 | `_log_metrics()` | Console (INFO/WARNING) | 메트릭 로깅 (PII 제외) |

#### D. 기타 서비스 로깅 (주요)

| File                                      | Approx Lines | Context           | Log Type |
| ----------------------------------------- | ------------ | ----------------- | -------- |
| `app/clients/llm_client.py`               | 다수         | LLM API 호출/에러 | Console  |
| `app/clients/backend_client.py`           | 다수         | Backend API 호출  | Console  |
| `app/services/render_job_runner.py`       | 다수         | 영상 렌더링       | Console  |
| `app/services/source_set_orchestrator.py` | 다수         | 소스셋 처리       | Console  |

### 2.3 로깅 사용 통계

- **총 파일 수**: 53개
- **총 로그 호출 수**: 588회 (logger.info/debug/warning/error)
- **AI Log Service 호출**: 1곳 (`chat_service.py:1106`)
- **DEBUG_RAG 호출**: 8곳 (chat_service, rag_handler, milvus_client)

---

## 3. Data Schema (현재 payload/필드)

### 3.1 AILogEntry 스키마

**파일**: `app/models/ai_log.py:31-166`

```python
class AILogEntry(BaseModel):
    # 세션/사용자 정보
    session_id: str                    # serialization_alias="sessionId"
    user_id: str                       # serialization_alias="userId"
    turn_index: Optional[int]          # serialization_alias="turnIndex"
    channel: str = "WEB"               # 그대로
    user_role: str                     # serialization_alias="userRole"
    department: Optional[str]          # 그대로

    # 의도/라우팅 정보
    domain: str                        # 그대로
    intent: str                        # 그대로
    route: str                         # 그대로

    # PII 마스킹 정보
    has_pii_input: bool = False        # serialization_alias="hasPiiInput"
    has_pii_output: bool = False       # serialization_alias="hasPiiOutput"

    # 모델/RAG 정보
    model_name: Optional[str]          # serialization_alias="modelName"
    rag_used: bool = False             # serialization_alias="ragUsed"
    rag_source_count: int = 0          # serialization_alias="ragSourceCount"

    # 성능 정보
    latency_ms: int                    # serialization_alias="latencyMs"

    # 에러 정보 (선택)
    error_code: Optional[str]          # serialization_alias="errorCode"
    error_message: Optional[str]       # serialization_alias="errorMessage"

    # LOG 단계 마스킹 텍스트 (선택)
    question_masked: Optional[str]     # serialization_alias="questionMasked"
    answer_masked: Optional[str]       # serialization_alias="answerMasked"

    # RAG Gap 분석
    rag_gap_candidate: bool = False    # serialization_alias="ragGapCandidate"
```

### 3.2 전송 Payload 예시 (실제 코드 기반 재구성)

```json
{
  "log": {
    "sessionId": "sess-abc123",
    "userId": "U-10293",
    "turnIndex": 3,
    "channel": "WEB",
    "userRole": "EMPLOYEE",
    "department": "영업팀",
    "domain": "POLICY",
    "intent": "POLICY_QA",
    "route": "RAG_INTERNAL",
    "hasPiiInput": false,
    "hasPiiOutput": false,
    "modelName": "meta-llama/Meta-Llama-3-8B-Instruct",
    "ragUsed": true,
    "ragSourceCount": 3,
    "latencyMs": 1250,
    "questionMasked": "연차 관련해서 알려줘",
    "answerMasked": "연차는 입사일 기준으로...",
    "ragGapCandidate": false
  }
}
```

### 3.3 v1 Telemetry 계약과의 Gap 분석

| v1 계약 필드                     | 현재 구현                | Gap                           |
| -------------------------------- | ------------------------ | ----------------------------- |
| `trace_id`                       | **미구현**               | 백엔드 헤더 전파 필요         |
| `conversation_id`                | `session_id`로 대체      | 의미적 동일, 필드명 변경 필요 |
| `turn_id`                        | `turn_index`             | 의미적 동일, 필드명 변경 필요 |
| `dept_id`                        | `department`             | 의미적 동일, 필드명 변경 필요 |
| `user_query_masked`              | `question_masked`        | 필드명 변경 필요              |
| `assistant_answer_masked`        | `answer_masked`          | 필드명 변경 필요              |
| `intent_main` / `intent_sub`     | `intent` 단일            | 분리 필요                     |
| `route_type`                     | `route`                  | 의미적 동일                   |
| `latency_ms_total`               | `latency_ms`             | 필드명 변경 필요              |
| `latency_ms_llm`                 | **미구현**               | 개별 측정 필요                |
| `latency_ms_retrieval`           | **미구현**               | 개별 측정 필요                |
| `pii_detected_input`             | `has_pii_input`          | 필드명 변경 필요              |
| `pii_detected_output`            | `has_pii_output`         | 필드명 변경 필요              |
| `oos` (out-of-scope)             | **미구현**               | 추가 필요                     |
| `rag.retriever`                  | **미구현**               | RAG 상세 정보 추가 필요       |
| `rag.topK`                       | **미구현**               | RAG 상세 정보 추가 필요       |
| `rag.minScore/maxScore/avgScore` | **미구현**               | RAG 상세 정보 추가 필요       |
| `rag.sources[]`                  | **미구현** (개수만 있음) | 상세 소스 목록 추가 필요      |

---

## 4. Flow (로그가 어떻게 흘러가는지)

### 4.1 채팅 1턴 기준 로그 생성→전송 단계

```
User Request
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 1: PII Masking (INPUT)                                     │
│   - pii_service.detect_and_mask(text, MaskingStage.INPUT)       │
│   - 로그: DEBUG (호출 정보), INFO/ERROR (결과/에러)              │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 2: Intent Classification                                    │
│   - intent_service.classify(query)                               │
│   - 로그: INFO (intent, route, domain)                           │
│   - DEBUG_RAG: dbg_route() [조건부]                              │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 3: RAG Search (route=RAG_INTERNAL 시)                       │
│   - DEBUG_RAG: dbg_final_query() [조건부]                        │
│   - DEBUG_RAG: dbg_retrieval_target() [조건부]                   │
│   - milvus_client.search_as_sources()                            │
│   - DEBUG_RAG: dbg_retrieval_top5() [조건부]                     │
│   - 로그: INFO/DEBUG/ERROR (검색 결과/에러)                       │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 4: LLM Generation                                           │
│   - llm_client.generate()                                        │
│   - 로그: INFO/DEBUG/ERROR (호출/응답/에러)                       │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 5: PII Masking (OUTPUT)                                     │
│   - pii_service.detect_and_mask(response, MaskingStage.OUTPUT)   │
│   - 로그: DEBUG/INFO/ERROR                                       │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 6: AI Log Generation & Send                                 │
│   - ai_log_service.mask_for_log(question, answer)                │
│   - ai_log_service.create_log_entry(...)                         │
│   - asyncio.create_task(ai_log_service.send_log_async(entry))    │
│   - 로그: INFO (AI Log 요약), DEBUG/WARNING (전송 결과/실패)      │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
Return ChatResponse
```

### 4.2 턴당 로그 발생 횟수 추정

**정상 케이스 (RAG 경로, DEBUG_RAG=0)**:

| 단계       | Console 로그     | DEBUG_RAG | AI Log HTTP |
| ---------- | ---------------- | --------- | ----------- |
| PII INPUT  | 1 (DEBUG)        | 0         | 0           |
| Intent     | 1 (INFO)         | 0         | 0           |
| RAG Search | 2-3 (INFO/DEBUG) | 0         | 0           |
| LLM        | 1-2 (INFO/DEBUG) | 0         | 0           |
| PII OUTPUT | 1 (DEBUG)        | 0         | 0           |
| AI Log     | 1 (INFO)         | 0         | **1**       |
| **합계**   | **7-9회**        | **0회**   | **1회**     |

**정상 케이스 (RAG 경로, DEBUG_RAG=1)**:

| 단계       | Console 로그 | DEBUG_RAG                         | AI Log HTTP |
| ---------- | ------------ | --------------------------------- | ----------- |
| PII INPUT  | 1            | 0                                 | 0           |
| Intent     | 1            | **1** (dbg_route)                 | 0           |
| RAG Search | 2-3          | **3** (final_query, target, top5) | 0           |
| LLM        | 1-2          | 0                                 | 0           |
| PII OUTPUT | 1            | 0                                 | 0           |
| AI Log     | 1            | 0                                 | **1**       |
| **합계**   | **7-9회**    | **4회**                           | **1회**     |

### 4.3 중복/누락 가능 지점

| 지점                             | 위험                                                                                             | 근거                                          |
| -------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------- |
| **DEBUG_RAG 중복**               | `rag_handler.py`와 `milvus_client.py` 모두에서 `dbg_retrieval_target`, `dbg_retrieval_top5` 호출 | 동일 request_id로 2번씩 출력될 수 있음        |
| **AI Log 누락**                  | `_send_ai_log()` 내부 예외 발생 시 try/except로 무시됨 (`chat_service.py:1108-1110`)             | 로그 손실 가능 (경고만 출력)                  |
| **PII 마스킹 실패 시 원문 저장** | `PII_ENABLED=false`이거나 서비스 장애 시 `_create_fallback_result()`가 원문 반환                 | 보안 위험 (원문이 `question_masked`에 저장됨) |

---

## 5. PII Handling (마스킹/비식별)

### 5.1 적용 지점/정책

| 단계       | 파일                | 함수                                         | 적용 시점                              |
| ---------- | ------------------- | -------------------------------------------- | -------------------------------------- |
| **INPUT**  | `pii_service.py`    | `detect_and_mask(text, MaskingStage.INPUT)`  | 사용자 질문 수신 직후, RAG/LLM 전달 전 |
| **OUTPUT** | `pii_service.py`    | `detect_and_mask(text, MaskingStage.OUTPUT)` | LLM 응답 생성 후, 사용자 반환 전       |
| **LOG**    | `ai_log_service.py` | `mask_for_log(question, answer)`             | AI Log 생성 시, 백엔드 전송 전         |

### 5.2 마스킹 전략

```
INPUT/OUTPUT: PII 서비스가 정의한 기본 마스킹 (토큰화)
LOG: PII 서비스가 정의한 강화 마스킹 (더 적극적인 비식별)
```

### 5.3 위험 경로 (Risk)

| 위험                       | 조건                          | 파일/라인                | 결과                                               |
| -------------------------- | ----------------------------- | ------------------------ | -------------------------------------------------- |
| **Risk 1: 원문 저장**      | `PII_ENABLED=false`           | `pii_service.py:140-145` | `_create_fallback_result(text)` → 원문 그대로 반환 |
| **Risk 2: 원문 저장**      | PII 서비스 HTTP 에러/타임아웃 | `pii_service.py:232-270` | `_create_fallback_result(text)` → 원문 그대로 반환 |
| **Risk 3: 원문 저장**      | PII 서비스 JSON 파싱 에러     | `pii_service.py:256-262` | `_create_fallback_result(text)` → 원문 그대로 반환 |
| **Risk 4: 콘솔 로그 원문** | LOG_LEVEL=DEBUG               | 다수 파일                | 디버그 로그에 원문 노출 가능                       |

### 5.4 마스킹 정책 요약

- **일부 로그만 적용**: ❌ (전 구간 시도)
- **전 구간 강제**: ✅ (단, fallback 시 원문 저장 위험)
- **fallback 정책**: 시스템 가용성 우선 (원문 반환)

---

## 6. Config Flags (.env / settings)

### 6.1 로깅/전송/디버그 관련 환경변수

| 환경변수                | 기본값                     | 파일            | 사용처                      | 설명                     |
| ----------------------- | -------------------------- | --------------- | --------------------------- | ------------------------ |
| `LOG_LEVEL`             | `INFO`                     | `config.py:50`  | `logging.py:38`             | 콘솔 로그 레벨           |
| `DEBUG_RAG`             | `0`                        | N/A             | `debug_log.py:38`           | RAG 디버그 로깅 활성화   |
| `PII_ENABLED`           | `true`                     | `config.py:97`  | `pii_service.py:97`         | PII 마스킹 활성화        |
| `PII_BASE_URL`          | (없음)                     | `config.py:94`  | `pii_service.py:88-91`      | PII 서비스 URL           |
| `BACKEND_BASE_URL`      | (없음)                     | `config.py:85`  | `ai_log_service.py:56-58`   | AI 로그 전송 대상        |
| `BACKEND_BASE_URL_MOCK` | `http://backend-mock:8081` | `config.py:60`  | `config.py:537-539`         | Mock 모드 백엔드 URL     |
| `BACKEND_BASE_URL_REAL` | (없음)                     | `config.py:66`  | `config.py:532-534`         | Real 모드 백엔드 URL     |
| `BACKEND_API_TOKEN`     | (없음)                     | `config.py:426` | `ai_log_service.py:199-200` | 백엔드 인증 토큰         |
| `AI_ENV`                | `mock`                     | `config.py:55`  | `config.py:521-539`         | AI 환경 모드 (mock/real) |
| `APP_NAME`              | `ctrlf-ai-gateway`         | `config.py:45`  | `logging.py:84`             | 앱 이름 (로그에 포함)    |
| `APP_ENV`               | `local`                    | `config.py:46`  | `logging.py:85`             | 앱 환경 (로그에 포함)    |

### 6.2 현재 .env 설정 (로컬)

```bash
LOG_LEVEL=DEBUG
AI_ENV=real
PII_ENABLED=false
BACKEND_BASE_URL=http://localhost:9002
# DEBUG_RAG 미설정 (비활성화)
# BACKEND_API_TOKEN 미설정
```

### 6.3 환경변수 의존 관계

```
AI_ENV ─┬─ mock → BACKEND_BASE_URL_MOCK 사용
        └─ real → BACKEND_BASE_URL_REAL 사용

BACKEND_BASE_URL (직접 설정) → AI_ENV 무시하고 직접 설정값 사용

PII_ENABLED=false → PII 서비스 호출 안 함 → 원문 그대로 사용 (Risk!)
```

---

## 7. Risks & Cleanup Candidates

### 7.1 리팩토링 시 반드시 손대야 할 곳 (우선순위 Top 10)

| 순위   | 대상                  | 파일                     | 이유                                                  | 액션                                     |
| ------ | --------------------- | ------------------------ | ----------------------------------------------------- | ---------------------------------------- |
| **1**  | AILogEntry 스키마     | `ai_log.py`              | v1 `ChatTurnEvent` 스키마와 불일치                    | 필드명/구조 변경                         |
| **2**  | trace_id 전파         | `chat_service.py` 외     | 백엔드 헤더에서 `X-Trace-Id` 수신/전파 미구현         | 헤더 파싱 및 전파 추가                   |
| **3**  | PII fallback 정책     | `pii_service.py:277-294` | 원문 저장 위험                                        | 실패 시 "[REDACTED]" 강제 또는 에러 반환 |
| **4**  | 텔레메트리 엔드포인트 | `ai_log_service.py:58`   | 현재 `/api/ai-logs` → v1 `/internal/telemetry/events` | URL 변경                                 |
| **5**  | 이벤트 배치 전송      | `ai_log_service.py`      | 현재 단일 전송 → v1 배치 전송                         | `events[]` 배열 지원                     |
| **6**  | eventId 생성          | `ai_log.py`              | 중복 방지용 eventId 미구현                            | UUID 생성 추가                           |
| **7**  | latency 분해          | `chat_service.py`        | `latency_ms_llm`, `latency_ms_retrieval` 미구현       | 개별 측정 추가                           |
| **8**  | RAG 상세 정보         | `ai_log.py`              | `rag.retriever`, `rag.topK`, `rag.scores` 미구현      | 필드 추가                                |
| **9**  | FeedbackEvent         | (미구현)                 | 사용자 피드백 이벤트 없음                             | 신규 구현                                |
| **10** | SecurityEvent         | (미구현)                 | PII/도메인 차단 이벤트 없음                           | 신규 구현                                |

### 7.2 유지할 것 / 제거할 것 / 바꿀 것

#### 유지할 것 (Keep)

- [ ] `app/core/logging.py` - 기본 로깅 인프라 (포맷/핸들러)
- [ ] `app/utils/debug_log.py` - RAG 디버그 로깅 (request_id 기반)
- [ ] 비동기 전송 방식 (fire-and-forget)
- [ ] 3단계 PII 마스킹 구조 (INPUT/OUTPUT/LOG)

#### 제거할 것 (Remove)

- [ ] `AILogEntry.rag_gap_candidate` - v1 스키마에 없음 (별도 이벤트로 분리 검토)
- [ ] `to_backend_log_payload()` - `{"log": {...}}` 래퍼 → v1은 `{"events": [...]}`

#### 바꿀 것 (Change)

- [ ] `AILogEntry` → `ChatTurnEvent` 스키마로 변경
- [ ] `/api/ai-logs` → `/internal/telemetry/events`
- [ ] `session_id` → `conversation_id`
- [ ] `turn_index` → `turn_id`
- [ ] `question_masked` → `user_query_masked`
- [ ] `answer_masked` → `assistant_answer_masked`
- [ ] `has_pii_input` → `pii_detected_input`
- [ ] `has_pii_output` → `pii_detected_output`
- [ ] `latency_ms` → `latency_ms_total` + `latency_ms_llm` + `latency_ms_retrieval`
- [ ] PII fallback 정책: 원문 반환 → "[REDACTED]" 강제

---

## Appendix A: 검색 키워드 근거

분석에 사용된 검색 키워드 및 결과:

```bash
# AI 로그 관련
rg "telemetry|ai-log|ai_log|AILog|AILogEntry|log_entry|send_log" --files-with-matches
# → 23개 파일

# DEBUG_RAG 관련
rg "DEBUG_RAG|rag_debug|retrieval_log|rerank_log" --files-with-matches
# → 4개 파일

# PII 관련
rg "pii|mask|sanitize|anonymize|redact" --files-with-matches
# → 66개 파일

# ID 추적 관련
rg "request_id|trace_id|correlation_id|conversation_id|session_id|turn_id" --files-with-matches
# → 59개 파일

# 로깅 라이브러리
rg "structlog|loguru|json.logger|JsonFormatter" --files-with-matches
# → 0개 파일 (표준 logging만 사용)
```

---

## Appendix B: 파일 위치 요약

```
app/
├── core/
│   ├── config.py          # 환경변수 설정
│   └── logging.py         # 로깅 설정 (106줄)
├── models/
│   ├── ai_log.py          # AI 로그 스키마 (232줄)
│   └── intent.py          # PII/Intent 모델 (203줄)
├── services/
│   ├── ai_log_service.py  # AI 로그 서비스 (243줄)
│   ├── chat_service.py    # 채팅 서비스 (로그 호출) (1200+줄)
│   ├── pii_service.py     # PII 마스킹 서비스 (328줄)
│   └── chat/
│       └── rag_handler.py # RAG 핸들러 (DEBUG_RAG 호출)
├── clients/
│   └── milvus_client.py   # Milvus 클라이언트 (DEBUG_RAG 호출)
└── utils/
    └── debug_log.py       # RAG 디버그 로깅 (255줄)

mock_backend/
└── main.py                # Mock 백엔드 (AI 로그 수신) (200줄)
```

---

## Appendix C: A7 구현 - PII Fail-Closed + Request-Scope Reset

> **추가일**: 2024-12-31
> **구현 목적**: PII 검출 서비스 장애 시 원문 노출 방지 (Fail-Closed) 및 요청 간 텔레메트리 상태 격리

### C.1 PII Fail-Closed

PII 서비스가 타임아웃/에러/예외 발생 시:

- **기존**: 원문 그대로 반환 (Fail-Open) → 보안 위험
- **A7**: `PiiDetectorUnavailableError` 예외 발생 → `chat_service`에서 안전한 fallback 메시지 반환

```python
# app/services/pii_service.py
class PiiDetectorUnavailableError(Exception):
    def __init__(self, stage: MaskingStage, reason: str):
        self.stage = stage
        self.reason = reason
```

### C.2 Request-Scope Reset

`RequestContextMiddleware`에서 요청 시작/종료 시 모든 contextvars 리셋:

```python
# app/telemetry/middleware.py
async def dispatch(self, request, call_next):
    # 요청 시작: clean slate
    reset_all_metrics()
    reset_chat_turn_emitted()
    reset_security_emitted()
    reset_feedback_emitted()

    try:
        return await call_next(request)
    finally:
        # 요청 종료: 방어적 리셋
        reset_request_context()
        reset_all_metrics()
        # ...
```

### C.3 샘플 이벤트

**SECURITY 이벤트 (PII Fail-Closed)**:

```json
{
  "eventId": "f157a610-3e75-49eb-a5a4-73c6fe13958b",
  "eventType": "SECURITY",
  "traceId": "trace-test-a7-001",
  "conversationId": "C-TEST-001",
  "turnId": 1,
  "userId": "U-TEST-001",
  "deptId": "D-TEST",
  "occurredAt": "2025-12-30T15:20:23.087452Z",
  "payload": {
    "blockType": "PII_BLOCK",
    "blocked": true,
    "ruleId": "PII_DETECTOR_UNAVAILABLE_INPUT"
  }
}
```

**CHAT_TURN 이벤트 (PII Fail-Closed)**:

```json
{
  "eventId": "54586717-ca46-4b70-b924-691824a1fd83",
  "eventType": "CHAT_TURN",
  "traceId": "trace-test-a7-001",
  "conversationId": "C-TEST-001",
  "turnId": 1,
  "userId": "U-TEST-001",
  "deptId": "D-TEST",
  "occurredAt": "2025-12-30T15:20:23.087452Z",
  "payload": {
    "intentMain": "UNKNOWN",
    "routeType": "API",
    "domain": "UNKNOWN",
    "ragUsed": false,
    "latencyMsTotal": 50,
    "errorCode": "PII_DETECTOR_UNAVAILABLE",
    "piiDetectedInput": false,
    "piiDetectedOutput": false
  }
}
```

---

## Appendix D: A8 구현 - Streaming Telemetry

> **추가일**: 2024-12-31
> **구현 목적**: 스트리밍 응답(/ai/chat/stream)에서도 텔레메트리 및 Fail-Closed 정책 동일 적용

### D.1 핵심 이슈

Starlette/FastAPI에서 middleware의 `finally`는 `call_next`가 `Response`를 반환하는 시점에 실행됩니다.
`StreamingResponse`의 경우 실제 body streaming이 끝나기 전에 `contextvars`가 리셋될 수 있습니다.

### D.2 해결 방안

**middleware.py 수정**:

- 일반 응답: 기존 `finally`에서 cleanup 수행
- `StreamingResponse`: `body_iterator`를 래핑하여 스트림 완료 후 cleanup 실행

```python
# app/telemetry/middleware.py
async def _wrap_streaming_body(body_iterator):
    try:
        async for chunk in body_iterator:
            yield chunk
    finally:
        _cleanup_telemetry_context()

# dispatch()에서:
if isinstance(response, StreamingResponse):
    response.body_iterator = _wrap_streaming_body(response.body_iterator)
```

### D.3 chat_stream 텔레메트리

**ChatStreamService.\_emit_telemetry_event()**:

- 스트리밍 정상 완료/예외/취소 시 `emit_chat_turn_once()` 호출
- `telemetry_emitted` 플래그로 중복 발행 방지

```python
# app/services/chat_stream_service.py
def _emit_telemetry_event(self, request, metrics, error_code):
    set_latency_metrics(
        total_ms=metrics.total_elapsed_ms or 0,
        llm_ms=metrics.total_elapsed_ms or 0,
        retrieval_ms=0,
    )
    emit_chat_turn_once(
        intent_main="STREAMING",
        route_type="LLM_ONLY",
        domain="CHAT",
        rag_used=False,
        latency_ms_total=metrics.total_elapsed_ms or 0,
        error_code=error_code,
    )
```

### D.4 테스트 커버리지

**tests/test_streaming_request_context_cleanup.py**:

- TEST-1: 스트리밍 중 `request_context` 유지
- TEST-2: 스트리밍 종료 후 cleanup 실행 및 상태 누수 없음
- TEST-3: 예외 발생 시에도 CHAT_TURN 1회 발행

### D.5 샘플 이벤트

**CHAT_TURN 이벤트 (Streaming 정상 완료)**:

```json
{
  "eventId": "d8e42f10-1234-5678-abcd-ef0123456789",
  "eventType": "CHAT_TURN",
  "traceId": "trace-streaming-001",
  "conversationId": "C-STREAM-001",
  "turnId": 1,
  "userId": "U-STREAM-001",
  "deptId": "D-SALES",
  "occurredAt": "2025-12-31T10:30:00Z",
  "payload": {
    "intentMain": "STREAMING",
    "routeType": "LLM_ONLY",
    "domain": "CHAT",
    "ragUsed": false,
    "latencyMsTotal": 1250,
    "latencyMsLlm": 1250,
    "latencyMsRetrieval": 0,
    "piiDetectedInput": false,
    "piiDetectedOutput": false
  }
}
```

**CHAT_TURN 이벤트 (Streaming 클라이언트 연결 끊김)**:

```json
{
  "eventId": "a1b2c3d4-5678-90ab-cdef-123456789abc",
  "eventType": "CHAT_TURN",
  "traceId": "trace-streaming-002",
  "conversationId": "C-STREAM-002",
  "turnId": 3,
  "userId": "U-STREAM-002",
  "deptId": "D-HR",
  "occurredAt": "2025-12-31T11:45:30Z",
  "payload": {
    "intentMain": "STREAMING",
    "routeType": "LLM_ONLY",
    "domain": "CHAT",
    "ragUsed": false,
    "latencyMsTotal": 850,
    "latencyMsLlm": 850,
    "latencyMsRetrieval": 0,
    "errorCode": "CLIENT_DISCONNECTED",
    "piiDetectedInput": false,
    "piiDetectedOutput": false
  }
}
```

### D.6 주의사항

1. **Middleware cleanup 순서**: StreamingResponse 여부를 `call_next` 후 확인하고, 스트리밍인 경우 `finally`에서 cleanup하지 않음
2. **예외 처리**: 스트리밍 도중 예외 발생 시 `_wrap_streaming_body`의 `finally`에서 cleanup 수행
3. **중복 발행 방지**: `is_chat_turn_emitted()` 플래그로 CHAT_TURN 이벤트 턴당 1회 보장

---

_문서 끝_
