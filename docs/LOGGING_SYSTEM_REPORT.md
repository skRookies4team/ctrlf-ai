# ctrlf-ai 프로젝트 로그 시스템 상세 보고서

## 1. 로깅 시스템 개요

### 1.1 아키텍처 요약

```
┌─────────────────────────────────────────────────────────────────┐
│                    ctrlf-ai Application                         │
│                      (53개 파일, 588개 로그 호출)                 │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
┌─────────────────┐ ┌───────────────┐ ┌─────────────────────┐
│ Console Logger  │ │ DEBUG_RAG     │ │ AI Log Service      │
│ (stdout)        │ │ (stderr)      │ │ (HTTP → Backend)    │
│                 │ │               │ │                     │
│ 포맷:           │ │ 포맷: JSON    │ │ 포맷: camelCase JSON│
│ 시간|레벨|모듈  │ │ 1줄           │ │                     │
└─────────────────┘ └───────────────┘ └─────────────────────┘
        │                   │                   │
        ▼                   ▼                   ▼
   [터미널/Docker]   [디버그 분석]    [Backend DB 저장]
```

### 1.2 로깅 채널 구분

| 채널 | 대상 | 용도 | 활성화 조건 |
|------|------|------|-------------|
| Console Logger | stdout | 일반 운영 로그 | 항상 (LOG_LEVEL 제어) |
| DEBUG_RAG | stderr | RAG 파이프라인 디버깅 | `DEBUG_RAG=1` |
| AI Log Service | Backend API | 채팅 메타데이터 저장 | `BACKEND_BASE_URL` 설정 시 |

---

## 2. 핵심 로깅 모듈

### 2.1 파일 위치
`app/core/logging.py` (106줄)

### 2.2 주요 함수

#### `setup_logging(settings: Settings)`
애플리케이션 시작 시 호출되어 로깅을 초기화합니다.

```python
# 로그 포맷
log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
date_format = "%Y-%m-%d %H:%M:%S"

# 출력 예시
2024-12-30 15:30:45 | INFO     | app.services.chat_service | Chat request processed
```

**설정 내용:**
- 루트 로거 레벨: `settings.LOG_LEVEL` (기본값: INFO)
- 출력 대상: `sys.stdout` (콘솔)
- Uvicorn 로거 통합 (`uvicorn`, `uvicorn.error`, `uvicorn.access`)

#### `get_logger(name: str)`
모듈별 로거 인스턴스를 반환합니다.

```python
# 사용 패턴 (전체 53개 파일에서 일관되게 사용)
from app.core.logging import get_logger
logger = get_logger(__name__)

logger.info("처리 시작")
logger.debug("상세 정보")
logger.warning("경고 메시지")
logger.error("에러 발생")
```

### 2.3 로깅 초기화 시점
`app/main.py:61`에서 lifespan 이벤트로 초기화:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings)
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.APP_ENV}")
    logger.info(f"AI_ENV: {settings.AI_ENV}")
    logger.info(f"LLM_BASE_URL: {settings.llm_base_url}")
    # ...
```

---

## 3. AI 로그 수집 시스템 (구조화된 로그)

### 3.1 목적
채팅 요청의 메타데이터를 백엔드 DB에 저장하여 다음 분석에 활용:
- 도메인별/라우트별 질문 비율
- PII 검출 비율
- RAG 사용 비율
- 성능(latency) 모니터링
- 보안/컴플라이언스 감사

### 3.2 관련 파일

| 파일 | 역할 |
|------|------|
| `app/services/ai_log_service.py` | 로그 생성 및 전송 서비스 |
| `app/models/ai_log.py` | 로그 데이터 스키마 정의 |

### 3.3 AILogEntry 스키마

`app/models/ai_log.py:31-166`

```python
class AILogEntry(BaseModel):
    # 세션/사용자 정보
    session_id: str                    # 채팅 세션 ID
    user_id: str                       # 사용자 ID / 사번
    turn_index: Optional[int]          # 세션 내 턴 인덱스 (0부터)
    channel: str = "WEB"               # 요청 채널 (WEB, MOBILE 등)
    user_role: str                     # 사용자 역할 (EMPLOYEE, MANAGER, ADMIN)
    department: Optional[str]          # 사용자 부서

    # 의도/라우팅 정보
    domain: str                        # 도메인 (POLICY, INCIDENT, EDUCATION 등)
    intent: str                        # 질문 의도 (POLICY_QA, INCIDENT_REPORT 등)
    route: str                         # 라우팅 결과 (RAG_INTERNAL, LLM_ONLY 등)

    # PII 마스킹 정보
    has_pii_input: bool = False        # 입력에서 PII 검출 여부
    has_pii_output: bool = False       # 출력에서 PII 검출 여부

    # 모델/RAG 정보
    model_name: Optional[str]          # 사용된 LLM 모델 이름
    rag_used: bool = False             # RAG 검색 사용 여부
    rag_source_count: int = 0          # RAG로 검색된 문서 개수

    # 성능 정보
    latency_ms: int                    # 전체 처리 시간 (ms)

    # 에러 정보 (선택)
    error_code: Optional[str]          # 에러 코드
    error_message: Optional[str]       # 에러 메시지

    # LOG 단계 마스킹 텍스트 (PII 원문 절대 저장 안 함)
    question_masked: Optional[str]     # 마스킹된 질문
    answer_masked: Optional[str]       # 마스킹된 답변

    # RAG Gap 분석
    rag_gap_candidate: bool = False    # RAG Gap 후보 여부
```

**직렬화 (camelCase):**
백엔드(Spring)가 기대하는 JSON 형식으로 변환:
```python
payload = entry.model_dump(by_alias=True, exclude_none=True)
# 결과: {"sessionId": "...", "userId": "...", "turnIndex": 0, ...}
```

### 3.4 AILogService 클래스

`app/services/ai_log_service.py:28-243`

#### 주요 메서드

| 메서드 | 기능 |
|--------|------|
| `create_log_entry()` | ChatRequest/Response에서 AILogEntry 생성 |
| `mask_for_log()` | LOG 단계 PII 마스킹 적용 |
| `send_log()` | 백엔드로 로그 전송 (동기) |
| `send_log_async()` | 백엔드로 로그 전송 (fire-and-forget) |

#### 로컬 로그 출력 (항상)
```python
# ai_log_service.py:171-183
logger.info(
    f"AI Log: session={log_entry.session_id}, "
    f"user={log_entry.user_id}, "
    f"intent={log_entry.intent}, "
    f"route={log_entry.route}, "
    f"domain={log_entry.domain}, "
    f"pii_input={log_entry.has_pii_input}, "
    f"pii_output={log_entry.has_pii_output}, "
    f"rag_used={log_entry.rag_used}, "
    f"rag_sources={log_entry.rag_source_count}, "
    f"latency_ms={log_entry.latency_ms}"
)
```

#### 백엔드 전송
```python
# ai_log_service.py:190-219
response = await client.post(
    self._backend_log_endpoint,  # "{backend_base_url}/api/ai-logs"
    json=payload,
    headers={"Authorization": f"Bearer {token}"},
    timeout=5.0,  # 로그 전송은 빠르게
)
```

### 3.5 PII 보안 정책
- **PII 원문 절대 저장 금지**
- LOG 단계에서 강화된 마스킹 적용
- `question_masked`, `answer_masked` 필드에만 마스킹된 텍스트 저장

---

## 4. RAG 디버그 로깅

### 4.1 파일 위치
`app/utils/debug_log.py` (255줄)

### 4.2 활성화 방법
```bash
# 환경변수 설정
DEBUG_RAG=1

# Windows PowerShell
$env:DEBUG_RAG="1"

# .env 파일
DEBUG_RAG=1
```

### 4.3 출력 형식
JSON 한 줄 형식으로 stderr에 출력:
```json
[DEBUG_RAG] {"ts":"2024-12-30T15:30:45.123","event":"route","request_id":"uuid","user_message":"연차 관련","intent":"POLICY_QA","domain":"POLICY","tool":"RAG_INTERNAL","reason":"키워드 '연차' 감지"}
```

### 4.4 이벤트 타입

| 이벤트 | 시점 | 함수 |
|--------|------|------|
| `route` | 라우팅 결정 직후 | `dbg_route()` |
| `retrieval_target` | Milvus 검색 직전 | `dbg_retrieval_target()` |
| `final_query` | 최종 검색 쿼리 확정 | `dbg_final_query()` |
| `retrieval_top5` | Milvus 결과 수신 직후 | `dbg_retrieval_top5()` |

### 4.5 이벤트별 상세

#### A. Route 로그
```python
dbg_route(
    request_id="uuid",
    user_message="연차 관련해서 알려줘",  # 200자 제한
    intent="POLICY_QA",
    domain="POLICY",
    tool="RAG_INTERNAL",
    reason="키워드 '연차' 감지",
    confidence=0.95,
)
```

#### B. Retrieval Target 로그
```python
dbg_retrieval_target(
    request_id="uuid",
    collection="ragflow_chunks_sroberta",
    partition=None,
    filter_expr='dataset_id == "사내규정"',
    top_k=5,
    domain="POLICY",
)
```

#### C. Final Query 로그
```python
dbg_final_query(
    request_id="uuid",
    original_query="연차 관련해서 알려줘",
    rewritten_query="연차 휴가 일수 규정",
    keywords=["연차", "휴가", "규정"],
)
```

#### D. Retrieval Top5 로그
```python
dbg_retrieval_top5(
    request_id="uuid",
    results=[
        {"doc_title": "인사규정", "chunk_id": "chunk_001", "score": 0.89},
        {"doc_title": "복리후생", "chunk_id": "chunk_002", "score": 0.85},
        # ... 최대 5개
    ],
)
```

### 4.6 민감정보 제한 (Sanitization)

```python
# debug_log.py:85-119
def _sanitize_value(key: str, value: Any) -> Any:
    # user_message, query: 200자 제한
    if key in ("user_message", "original_query", "rewritten_query"):
        return value[:200] + "..." if len(value) > 200 else value

    # text, content: 완전 제거
    if key in ("text", "content", "snippet"):
        return "[REDACTED]"

    # results: 본문 제거, 메타데이터만 유지
    if key == "results":
        return [{k: v for k, v in item.items()
                 if k not in ("text", "content", "chunk_text")}
                for item in value[:5]]
```

### 4.7 사용 위치
- `app/services/chat_service.py:509, 530` - 라우팅 로그
- `app/services/chat/rag_handler.py:549, 605, 747` - RAG 검색 로그
- `app/clients/milvus_client.py:673, 722` - Milvus 검색 로그

---

## 5. 로깅 설정

### 5.1 환경변수

| 변수 | 기본값 | 설명 | 파일 |
|------|--------|------|------|
| `LOG_LEVEL` | `INFO` | 로그 레벨 (DEBUG/INFO/WARNING/ERROR) | config.py:50 |
| `DEBUG_RAG` | `0` | RAG 디버그 로깅 활성화 | debug_log.py |
| `BACKEND_BASE_URL` | - | AI 로그 전송 대상 URL | config.py:85 |
| `BACKEND_API_TOKEN` | - | 백엔드 인증 토큰 | config.py:426 |
| `APP_ENV` | `local` | 앱 환경 (local/dev/prod/docker) | config.py:46 |
| `APP_NAME` | `ctrlf-ai-gateway` | 앱 이름 (로그에 포함) | config.py:45 |

### 5.2 설정 파일별 기본값

| 파일 | LOG_LEVEL | DEBUG_RAG |
|------|-----------|-----------|
| `.env.example` | INFO | 미설정 |
| `docker-compose.yml` | DEBUG | 미설정 |

### 5.3 환경별 권장 설정

| 환경 | LOG_LEVEL | DEBUG_RAG |
|------|-----------|-----------|
| 개발 (local) | DEBUG | 1 |
| 스테이징 (dev) | INFO | 0 |
| 프로덕션 (prod) | INFO 또는 WARNING | 0 |

---

## 6. Mock 백엔드 로그 API

### 6.1 파일 위치
`mock_backend/main.py` (200줄)

### 6.2 엔드포인트

| 메서드 | 경로 | 기능 |
|--------|------|------|
| POST | `/api/ai-logs` | AI 로그 저장 |
| GET | `/api/ai-logs` | 저장된 로그 조회 (테스트용) |
| GET | `/stats` | 호출 통계 |
| POST | `/stats/reset` | 통계 초기화 |
| GET | `/health` | 헬스체크 |

### 6.3 로그 저장 처리
```python
# mock_backend/main.py:115-151
@app.post("/api/ai-logs")
async def create_ai_log(log_entry: AILogEntry):
    state.log_call_count += 1
    state.logs.append(log_entry)

    logger.info(
        f"[Mock Backend] AI Log received: session_id={log_entry.session_id}, "
        f"intent={log_entry.intent}, route={log_entry.route}"
    )

    # PII 원문 경고
    if log_entry.question_original:
        logger.warning("[Mock Backend] WARNING: question_original contains raw data")

    return LogResponse(status="ok", log_id=f"log-{state.log_call_count:04d}")
```

---

## 7. 로거 사용 현황

### 7.1 통계
- **총 파일 수**: 53개
- **총 로그 호출 수**: 588회
- **로그 메서드별 분포**: `logger.info()`, `logger.debug()`, `logger.warning()`, `logger.error()`

### 7.2 주요 파일별 로그 호출 수

| 파일 | 호출 수 |
|------|---------|
| `app/services/source_set_orchestrator.py` | 55 |
| `app/clients/backend_client.py` | 55 |
| `app/services/render_job_runner.py` | 35 |
| `app/clients/milvus_client.py` | 32 |
| `app/services/faq_service.py` | 23 |
| `app/services/video_renderer_mvp.py` | 23 |
| `app/services/chat_service.py` | 22 |
| `app/services/video_renderer_real.py` | 19 |
| `app/clients/llm_client.py` | 16 |
| `app/api/v1/rag_documents.py` | 15 |

### 7.3 로거 초기화 패턴
모든 파일에서 일관된 패턴 사용:
```python
from app.core.logging import get_logger
logger = get_logger(__name__)
```

---

## 8. 로그 레벨별 사용 가이드

### 8.1 현재 구현된 로그 레벨 활용

| 레벨 | 용도 | 예시 |
|------|------|------|
| **DEBUG** | 상세 디버깅 정보 | RAG 검색 결과, HTTP 요청/응답 상세 |
| **INFO** | 일반 운영 정보 | 서비스 시작/종료, 요청 처리 완료 |
| **WARNING** | 잠재적 문제 | 설정 누락, 재시도 발생, PII 원문 감지 |
| **ERROR** | 에러 발생 | 서비스 호출 실패, 예외 발생 |

### 8.2 코드 예시

```python
# INFO: 정상 흐름
logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
logger.info(f"AI Log: session={session_id}, intent={intent}")

# DEBUG: 상세 정보
logger.debug(f"RAG search results: {len(results)} documents")
logger.debug(f"AI log sent successfully: session={session_id}")

# WARNING: 잠재적 문제
logger.warning(f"BACKEND_BASE_URL not configured, skipping remote log")
logger.warning(f"AI log send failed: status={status_code}")

# ERROR: 에러 상황
logger.error(f"Background AI log send failed: {e}")
logger.error(f"LLM call failed after {max_retries} retries")
```

---

## 9. 미구현 사항

### 9.1 로그 파일 저장
현재 콘솔(stdout) 출력만 구현되어 있으며, 파일 저장은 미구현입니다.

```python
# 향후 추가 필요 (미구현)
import logging.handlers

file_handler = logging.handlers.RotatingFileHandler(
    "logs/app.log",
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5
)
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)
```

### 9.2 외부 로그 수집 서비스
다음 서비스들과의 연동이 구현되어 있지 않습니다:
- ELK (Elasticsearch, Logstash, Kibana)
- Fluentd / Fluent Bit
- Datadog
- AWS CloudWatch
- Splunk
- OpenTelemetry

### 9.3 구조화된 로깅 (JSON 포맷)
현재 Console Logger는 텍스트 포맷만 지원합니다. JSON 구조화 로깅은 DEBUG_RAG에서만 사용됩니다.

---

## 10. 요약

| 항목 | 상태 | 상세 |
|------|------|------|
| 기본 로깅 | O 구현됨 | Python logging + stdout |
| 로그 레벨 제어 | O 구현됨 | LOG_LEVEL 환경변수 |
| AI 로그 수집 | O 구현됨 | Backend API 전송 (fire-and-forget) |
| PII 마스킹 | O 구현됨 | LOG 단계 강화 마스킹 |
| RAG 디버그 로깅 | O 구현됨 | DEBUG_RAG=1로 활성화 |
| Mock 백엔드 | O 구현됨 | 통합 테스트용 |
| 파일 로그 저장 | X 미구현 | 콘솔만 출력 |
| 외부 로그 서비스 | X 미구현 | ELK, Datadog 등 미연동 |
| JSON 구조화 로깅 | 부분 구현 | DEBUG_RAG만 JSON |

---

*작성일: 2025-12-30*
