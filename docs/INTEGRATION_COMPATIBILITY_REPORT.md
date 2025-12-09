# 연동 호환성 분석 리포트

## 개요

본 문서는 `ctrlf-ai` (AI Gateway)가 다른 CTRL+F 프로젝트 컴포넌트들과 연동할 때 발생할 수 있는 호환성 문제를 분석한 리포트입니다.

**분석 일자**: 2025-12-09
**분석 대상**:
- ctrlf-back (Spring Boot 백엔드)
- ctrlf-ragflow (RAGFlow 기반 검색 서비스)
- ctrlf-front (React 프론트엔드)

---

## 1. 연동 대상 프로젝트 현황

### 1.1 프로젝트별 기술 스택

| 프로젝트 | 기술 스택 | 포트 | 역할 |
|----------|----------|------|------|
| **ctrlf-ai** | Python 3.12, FastAPI | 8000 | AI Gateway (PII/Intent/RAG/LLM) |
| **ctrlf-back** | Java 17, Spring Boot | 9001~9004 | 메인 백엔드, API Gateway |
| **ctrlf-ragflow** | Python, Flask (RAGFlow) | 8080 (예상) | RAG 문서 검색 서비스 |
| **ctrlf-front** | React, TypeScript, Vite | 3000 (예상) | 웹 프론트엔드 |

### 1.2 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CTRL+F 시스템 아키텍처                          │
│                                                                             │
│  ┌───────────────┐                                                          │
│  │ ctrlf-front   │                                                          │
│  │ (React)       │                                                          │
│  │ :3000         │                                                          │
│  └───────┬───────┘                                                          │
│          │ HTTP                                                             │
│          ▼                                                                  │
│  ┌───────────────┐         ┌───────────────┐         ┌───────────────┐     │
│  │ ctrlf-back    │         │ ctrlf-ai      │         │ ctrlf-ragflow │     │
│  │ (Spring Boot) │ ──────► │ (FastAPI)     │ ──────► │ (Flask/RAG)   │     │
│  │ :9001~9004    │         │ :8000         │         │ :8080         │     │
│  │               │ ◄────── │               │ ◄────── │               │     │
│  └───────────────┘         └───────┬───────┘         └───────────────┘     │
│          ▲                         │                                        │
│          │ AI Log 전송              │ LLM 호출                               │
│          │                         ▼                                        │
│          │                 ┌───────────────┐                                │
│          └──────────────── │ 내부 LLM 서버 │                                │
│                            │ :8001         │                                │
│                            └───────────────┘                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. ctrlf-back (Spring 백엔드) 연동 분석

### 2.1 백엔드 구조

ctrlf-back은 **멀티 모듈 Spring Boot 프로젝트**입니다:

| 서비스 | 포트 | 설명 |
|--------|------|------|
| chat-service | 9001 | 채팅 관련 API |
| education-service | 9002 | 교육 관련 API |
| infra-service | 9003 | 인프라 관련 API |
| quiz-service | 9004 | 퀴즈 관련 API |
| api-gateway | - | API 라우팅 |

**인증**: Keycloak (포트 8080)

### 2.2 ctrlf-ai → ctrlf-back 연동 (AI Log)

#### ctrlf-ai가 전송하는 API

| 항목 | 값 |
|------|-----|
| **엔드포인트** | `POST {BACKEND_BASE_URL}/api/ai-logs` |
| **환경변수** | `BACKEND_BASE_URL` |
| **담당 모듈** | `app/services/ai_log_service.py` |

#### 요청 스키마 (AILogRequest)

```json
{
  "log": {
    "session_id": "string",
    "user_id": "string",
    "turn_index": 0,
    "channel": "WEB",
    "user_role": "EMPLOYEE",
    "department": "개발팀",
    "domain": "POLICY",
    "intent": "POLICY_QA",
    "route": "ROUTE_RAG_INTERNAL",
    "has_pii_input": true,
    "has_pii_output": false,
    "model_name": "gpt-4",
    "rag_used": true,
    "rag_source_count": 3,
    "latency_ms": 1500,
    "error_code": null,
    "error_message": null,
    "question_masked": "[PHONE] 남기고 연차 규정 알려줘",
    "answer_masked": "연차휴가 이월은 최대 10일까지..."
  }
}
```

#### 예상 응답 스키마 (AILogResponse)

```json
{
  "success": true,
  "log_id": "log-0001",
  "message": "Log saved successfully"
}
```

### 2.3 호환성 상태

| 항목 | 상태 | 설명 |
|------|------|------|
| 엔드포인트 존재 | ⚠️ **확인 필요** | `/api/ai-logs` 구현 여부 불명 |
| 필드명 형식 | ⚠️ **확인 필요** | snake_case (Python) vs camelCase (Java) |
| 인증 | ⚠️ **확인 필요** | Keycloak 토큰 필요 여부 |

### 2.4 권장 조치

#### 백엔드 팀 확인 사항

1. **chat-service에 `/api/ai-logs` 엔드포인트 존재 여부 확인**
2. **필드명 매핑 확인** (아래 표 참조)

| ctrlf-ai (snake_case) | ctrlf-back 예상 (camelCase) |
|-----------------------|----------------------------|
| `session_id` | `sessionId` |
| `user_id` | `userId` |
| `turn_index` | `turnIndex` |
| `user_role` | `userRole` |
| `has_pii_input` | `hasPiiInput` |
| `has_pii_output` | `hasPiiOutput` |
| `model_name` | `modelName` |
| `rag_used` | `ragUsed` |
| `rag_source_count` | `ragSourceCount` |
| `latency_ms` | `latencyMs` |
| `error_code` | `errorCode` |
| `error_message` | `errorMessage` |
| `question_masked` | `questionMasked` |
| `answer_masked` | `answerMasked` |

#### 백엔드에 API가 없는 경우 구현 예시

```java
// AiLogController.java
@RestController
@RequestMapping("/api")
public class AiLogController {

    @Autowired
    private AiLogService aiLogService;

    @PostMapping("/ai-logs")
    public ResponseEntity<AiLogResponse> saveAiLog(
            @RequestBody AiLogRequest request) {

        AiLogEntry entry = request.getLog();
        String logId = aiLogService.save(entry);

        return ResponseEntity.ok(
            new AiLogResponse(true, logId, "Log saved successfully")
        );
    }
}
```

---

## 3. ctrlf-ragflow 연동 분석

### 3.1 RAGFlow 구조

ctrlf-ragflow는 **RAGFlow 오픈소스 기반**의 검색 서비스입니다:

```
api/
├── apps/
│   ├── search_app.py      # 검색 앱 관리
│   ├── chunk_app.py       # 청크/검색 API (/retrieval_test)
│   ├── conversation_app.py # 대화 API (/completion)
│   ├── document_app.py    # 문서 관리
│   ├── kb_app.py          # 지식베이스 관리
│   └── sdk/
│       ├── chat.py        # SDK 채팅 API
│       └── session.py     # SDK 세션 API
└── ragflow_server.py      # 서버 진입점
```

### 3.2 ctrlf-ai → ctrlf-ragflow 연동 (RAG 검색)

#### ctrlf-ai가 호출하는 API

| 항목 | 값 |
|------|-----|
| **엔드포인트** | `POST {RAGFLOW_BASE_URL}/search` |
| **환경변수** | `RAGFLOW_BASE_URL` |
| **담당 모듈** | `app/clients/ragflow_client.py` |

#### ctrlf-ai 요청 스키마

```json
{
  "query": "연차휴가 이월 규정 알려줘",
  "top_k": 5,
  "dataset": "POLICY",
  "user_role": "EMPLOYEE",
  "department": "개발팀"
}
```

#### ctrlf-ai 기대 응답 스키마

```json
{
  "results": [
    {
      "doc_id": "HR-001",
      "title": "연차휴가 관리 규정",
      "page": 12,
      "score": 0.92,
      "snippet": "연차휴가의 이월은 최대 10일을 초과할 수 없으며..."
    }
  ]
}
```

### 3.3 실제 ctrlf-ragflow API (확인된 것)

#### `/retrieval_test` (chunk_app.py)

```
POST /retrieval_test
Body: {
  "question": "검색 쿼리",
  "kb_id": "지식베이스 ID",
  "page": 1,
  "size": 30
}
Response: {
  "chunks": [
    {
      "chunk_id": "...",
      "doc_name": "...",
      "content": "...",
      "similarity": 0.92
    }
  ]
}
```

#### `/completion` (conversation_app.py)

```
POST /completion
Body: {
  "conversation_id": "...",
  "messages": [{"role": "user", "content": "..."}],
  "stream": true
}
Response: SSE 스트림
```

### 3.4 호환성 상태

| 항목 | 상태 | 설명 |
|------|------|------|
| `/search` 엔드포인트 | 🔴 **존재하지 않음** | ctrlf-ai가 기대하는 API 없음 |
| `/retrieval_test` | ✅ 존재 | 다른 형식의 검색 API |
| 요청 형식 | 🔴 **불일치** | `query` vs `question`, `dataset` vs `kb_id` |
| 응답 형식 | 🔴 **불일치** | `results` vs `chunks`, 필드명 다름 |

### 3.5 🔴 필수 조치 (택1)

#### 옵션 A: ctrlf-ragflow에 `/search` 래퍼 API 추가 (권장)

ctrlf-ragflow 레포에 아래 코드 추가:

```python
# api/apps/search_wrapper.py

from flask import Blueprint, request, jsonify
from api.apps.chunk_app import retrieval_test_internal

search_bp = Blueprint('search', __name__)

# dataset(도메인) → kb_id 매핑
DATASET_TO_KB = {
    "POLICY": "kb_policy_001",
    "INCIDENT": "kb_incident_001",
    "EDUCATION": "kb_education_001",
}

@search_bp.route('/search', methods=['POST'])
def search():
    """ctrlf-ai 호환 검색 API 래퍼"""
    data = request.json

    query = data.get('query', '')
    top_k = data.get('top_k', 5)
    dataset = data.get('dataset', 'POLICY')

    # dataset을 kb_id로 변환
    kb_id = DATASET_TO_KB.get(dataset, DATASET_TO_KB['POLICY'])

    # 내부 retrieval_test 호출
    chunks = retrieval_test_internal(
        question=query,
        kb_id=kb_id,
        size=top_k
    )

    # 응답 형식 변환
    results = []
    for chunk in chunks:
        results.append({
            "doc_id": chunk.get("chunk_id") or chunk.get("doc_id", "unknown"),
            "title": chunk.get("doc_name") or chunk.get("document_name", "Untitled"),
            "page": chunk.get("page_num"),
            "score": chunk.get("similarity") or chunk.get("score", 0.0),
            "snippet": chunk.get("content") or chunk.get("text", "")
        })

    return jsonify({"results": results})


@search_bp.route('/health', methods=['GET'])
def health():
    """헬스체크 엔드포인트"""
    return jsonify({"status": "ok", "service": "ragflow"})
```

```python
# ragflow_server.py에 추가
from api.apps.search_wrapper import search_bp
app.register_blueprint(search_bp)
```

#### 옵션 B: ctrlf-ai의 RagflowClient 수정

`app/clients/ragflow_client.py` 수정:

```python
async def search(
    self,
    query: str,
    top_k: int = 5,
    dataset: Optional[str] = None,
    ...
) -> List[RagDocument]:

    # dataset을 kb_id로 변환
    kb_id = self._dataset_to_kb_id(dataset)

    # /retrieval_test 호출
    url = f"{self._base_url}/retrieval_test"
    payload = {
        "question": query,  # query → question
        "kb_id": kb_id,     # dataset → kb_id
        "size": top_k,      # top_k → size
    }

    response = await self._client.post(url, json=payload)
    data = response.json()

    # 응답 변환
    documents = []
    for chunk in data.get("chunks", []):
        doc = RagDocument(
            doc_id=chunk.get("chunk_id", "unknown"),
            title=chunk.get("doc_name", "Untitled"),
            page=chunk.get("page_num"),
            score=chunk.get("similarity", 0.0),
            snippet=chunk.get("content"),
        )
        documents.append(doc)

    return documents

def _dataset_to_kb_id(self, dataset: Optional[str]) -> str:
    """도메인을 지식베이스 ID로 변환"""
    mapping = {
        "POLICY": "kb_policy_001",
        "INCIDENT": "kb_incident_001",
        "EDUCATION": "kb_education_001",
    }
    return mapping.get(dataset or "POLICY", "kb_policy_001")
```

---

## 4. ctrlf-front 연동 분석

### 4.1 프론트엔드 구조

```
src/
├── assets/          # 정적 자원
├── components/      # React 컴포넌트
├── pages/           # 페이지 컴포넌트
│   ├── Dashboard.tsx
│   ├── MessagePage.tsx    # 채팅 페이지 (추정)
│   ├── EventPage.tsx
│   ├── ApprovalPage.tsx
│   └── MyPage.tsx
├── keycloak.ts      # Keycloak 인증
└── main.tsx         # 진입점
```

### 4.2 연동 방식

프론트엔드는 **ctrlf-ai와 직접 통신하지 않습니다**.

```
[ctrlf-front] ──HTTP──► [ctrlf-back] ──HTTP──► [ctrlf-ai]
     │                      │                      │
     │   채팅 요청           │   AI 요청 프록시      │
     │   /api/chat/send     │   /ai/chat/messages  │
     ▼                      ▼                      ▼
```

### 4.3 호환성 상태

| 항목 | 상태 | 설명 |
|------|------|------|
| 직접 연동 | ✅ 해당 없음 | 백엔드 통해 간접 연동 |
| 인증 | ✅ Keycloak | 프론트/백엔드 동일 사용 |

### 4.4 권장 조치

- ctrlf-back이 ctrlf-ai를 프록시하는 API 구현 확인
- CORS 설정 확인 (백엔드에서 처리)

---

## 5. 호환성 종합 평가

### 5.1 요약 테이블

| 연동 경로 | 호환성 | 심각도 | 필요 조치 |
|----------|--------|--------|----------|
| ctrlf-ai → ctrlf-back (AI Log) | ⚠️ 불확실 | 중간 | API 스펙 확인 필요 |
| ctrlf-ai → ctrlf-ragflow (Search) | 🔴 불일치 | **높음** | API 래퍼 추가 필수 |
| ctrlf-ai → 내부 LLM | ✅ 준비됨 | 낮음 | OpenAI 호환 형식 |
| ctrlf-front → ctrlf-ai | ✅ 해당 없음 | 없음 | 백엔드 통해 간접 연동 |

### 5.2 위험도 매트릭스

```
높음 │ ████████████████████████████████████
     │ █  ctrlf-ragflow API 불일치        █
     │ ████████████████████████████████████
     │
중간 │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
     │ ░  ctrlf-back AI Log 스펙 미확인   ░
     │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
     │
낮음 │
     │
     └──────────────────────────────────────
       즉시 해결     확인 필요     문제 없음
```

---

## 6. 필수 조치 사항 체크리스트

### 6.1 🔴 즉시 해결 필요 (ctrlf-ragflow)

- [ ] `/search` 래퍼 API 추가 (옵션 A) 또는 ctrlf-ai 클라이언트 수정 (옵션 B)
- [ ] `/health` 엔드포인트 추가
- [ ] dataset → kb_id 매핑 테이블 정의
- [ ] 응답 형식 변환 로직 구현

### 6.2 ⚠️ 확인 필요 (ctrlf-back)

- [ ] `POST /api/ai-logs` 엔드포인트 존재 여부 확인
- [ ] 요청/응답 필드명 매핑 확인 (snake_case ↔ camelCase)
- [ ] Keycloak 인증 토큰 필요 여부 확인
- [ ] DB 스키마와 AILogEntry 필드 매칭 확인

### 6.3 ✅ 확인 완료 (ctrlf-ai)

- [x] RagflowClient 구현 완료 (단, API 스펙 조정 필요)
- [x] AILogService 구현 완료
- [x] LLMClient 구현 완료 (OpenAI 호환)
- [x] PiiService 구현 완료
- [x] IntentService 구현 완료

---

## 7. 연동 테스트 계획

### 7.1 단계별 테스트

| 단계 | 테스트 | 명령어/방법 |
|------|--------|------------|
| 1 | RAGFlow 헬스체크 | `curl http://ragflow:8080/health` |
| 2 | RAGFlow 검색 API | `curl -X POST http://ragflow:8080/search -d '{"query":"연차"}'` |
| 3 | LLM 헬스체크 | `curl http://llm:8001/health` |
| 4 | 백엔드 AI Log API | `curl -X POST http://backend:9001/api/ai-logs -d '{...}'` |
| 5 | AI Gateway E2E | `docker compose up -d && pytest -m integration` |

### 7.2 Docker Compose 통합 테스트

```bash
# 1. 서비스 시작
docker compose up -d

# 2. 헬스체크
curl http://localhost:8000/health  # AI Gateway
curl http://localhost:8080/health  # RAGFlow
curl http://localhost:8001/health  # LLM
curl http://localhost:9001/health  # Backend

# 3. 통합 테스트 실행
pytest -m integration -v

# 4. 수동 E2E 테스트
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "user_id": "emp-123",
    "user_role": "EMPLOYEE",
    "domain": "POLICY",
    "messages": [{"role": "user", "content": "연차휴가 규정 알려줘"}]
  }'
```

---

## 8. 결론 및 권장 사항

### 8.1 우선순위별 조치 사항

| 우선순위 | 조치 사항 | 담당 |
|---------|----------|------|
| **P0 (즉시)** | ctrlf-ragflow에 `/search` API 래퍼 추가 | RAGFlow 팀 |
| **P1 (1주 내)** | ctrlf-back에서 `/api/ai-logs` 스펙 확정 | 백엔드 팀 |
| **P1 (1주 내)** | 필드명 매핑 (snake_case ↔ camelCase) 결정 | 전체 팀 |
| **P2 (2주 내)** | Docker Compose 통합 환경 구축 | DevOps |
| **P2 (2주 내)** | E2E 통합 테스트 실행 | QA |

### 8.2 연동 성공 기준

- [ ] 모든 서비스 헬스체크 통과
- [ ] RAGFlow 검색 API 정상 동작
- [ ] AI Log 백엔드 전송 성공
- [ ] E2E 통합 테스트 5개 시나리오 통과
- [ ] PII 마스킹 검증 완료

---

**작성일**: 2025-12-09
**작성자**: Claude Opus 4.5 (AI Assistant)
**버전**: 1.0
