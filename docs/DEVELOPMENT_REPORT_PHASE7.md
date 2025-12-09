# Phase 7: HTTP E2E 테스트 완성 보고서

## 개요

Phase 7에서는 FastAPI `/ai/chat/messages` 엔드포인트를 직접 호출하는 HTTP 레벨 E2E 테스트를 구현했습니다.

**목표**: HTTP 레벨에서 전체 파이프라인(PII → Intent → RAG → LLM → AILog) 검증

## 변경 사항 요약

### 1. Fake 서비스 클래스 (`tests/test_chat_http_e2e.py`)

#### FakePiiService
```python
class FakePiiService(PiiService):
    """
    규칙 기반 PII 마스킹:
    - 전화번호: 010-XXXX-XXXX → [PHONE]
    - 이메일: xxx@xxx.xxx → [EMAIL]

    호출 기록 저장:
    - input_calls: INPUT 단계 호출 기록
    - output_calls: OUTPUT 단계 호출 기록
    - log_calls: LOG 단계 호출 기록
    """
```

#### FakeIntentService
```python
class FakeIntentService(IntentService):
    """
    미리 설정된 IntentResult 반환:
    - intent: IntentType (POLICY_QA, GENERAL_CHAT 등)
    - domain: 도메인 문자열
    - route: RouteType (ROUTE_RAG_INTERNAL, ROUTE_LLM_ONLY 등)
    """
```

#### FakeRagflowClient
```python
class FakeRagflowClient(RagflowClient):
    """
    - documents: 반환할 문서 리스트
    - should_fail: True면 ConnectionError 발생
    - fail_if_called: True면 호출 자체가 테스트 실패
    """
```

#### FakeLLMClient
```python
class FakeLLMClient(LLMClient):
    """
    - response: 반환할 응답 문자열
    - should_fail: True면 ConnectionError 발생
    - 호출된 messages 기록 저장
    """
```

#### FakeAILogService
```python
class FakeAILogService(AILogService):
    """
    - 실제 HTTP 호출 없이 메모리에 로그 저장
    - logs: 저장된 AILogEntry 리스트
    - last_log: 가장 최근 로그
    """
```

### 2. Dependency Injection Override 패턴

```python
# ChatService에 Fake 의존성 주입
test_service = create_test_chat_service(
    pii_service=fake_pii,
    intent_service=fake_intent,
    ragflow_client=fake_rag,
    llm_client=fake_llm,
    ai_log_service=fake_log,
)

# FastAPI dependency override
app.dependency_overrides[get_chat_service] = lambda: test_service

try:
    client = TestClient(app)
    response = client.post("/ai/chat/messages", json={...})
finally:
    app.dependency_overrides.clear()
```

### 3. 테스트 시나리오

| # | 시나리오 | 검증 항목 |
|---|----------|----------|
| 1 | POLICY + RAG + LLM + PII + 로그 | 전체 해피패스, PII 마스킹, RAG 결과 |
| 2 | POLICY + RAG 0건 + fallback | fallback 안내, rag_used=False |
| 3 | ROUTE_LLM_ONLY + PII | RAG 스킵, LLM-only 응답 |
| 4 | RAG 에러 + LLM fallback | RAG 장애 시 LLM-only 진행 |
| 5 | 응답 스키마 완전성 | 모든 필드 존재 확인 |

### 4. 시나리오별 상세 검증

#### 시나리오 1: POLICY + RAG + LLM + PII + 로그 해피패스
```python
# 검증 항목
- HTTP 200 응답
- PII INPUT 마스킹: "010-1234-5678" → "[PHONE]"
- RAG 쿼리에 원본 PII 없음
- LLM 메시지에 원본 PII 없음
- 최종 응답에 원본 PII 없음
- sources에 RAG 결과 포함
- meta.rag_used == True
- 로그에 원본 PII 없음
```

#### 시나리오 2: POLICY + RAG 0건 + fallback
```python
# 검증 항목
- sources == []
- meta.rag_used == False
- meta.rag_source_count == 0
- answer에 fallback 안내 포함
- RAG 호출됨 (결과만 없음)
```

#### 시나리오 3: ROUTE_LLM_ONLY + PII
```python
# 검증 항목
- RagflowClient 호출 안 됨
- meta.route == "ROUTE_LLM_ONLY"
- 이메일 PII 마스킹: "test@example.com" → "[EMAIL]"
- LLM 메시지에 [EMAIL] 포함
```

#### 시나리오 4: RAG 에러 + LLM fallback
```python
# 검증 항목
- HTTP 200 (에러가 아닌 정상 응답)
- sources == []
- meta.rag_used == False
- LLM 응답 정상 반환
```

#### 시나리오 5: 응답 스키마 완전성
```python
# 검증 필드
- answer: str
- sources: List[ChatSource]
- meta.used_model, route, intent, domain
- meta.masked, has_pii_input, has_pii_output
- meta.rag_used, rag_source_count, latency_ms
```

## 테스트 결과

```
$ pytest --tb=short -q
........................................................................ [ 82%]
...............                                                          [100%]
87 passed in 2.55s
```

**기존 82개 + 새로 추가 5개 = 총 87개 테스트 통과**

## 파일 변경 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `tests/test_chat_http_e2e.py` | 신규 | HTTP E2E 테스트 5개 시나리오 |

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         HTTP E2E Test Layer                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    FastAPI TestClient                            │   │
│  │                           │                                      │   │
│  │              POST /ai/chat/messages                              │   │
│  │                           │                                      │   │
│  │              ┌────────────▼────────────┐                        │   │
│  │              │  app.dependency_overrides │                       │   │
│  │              └────────────┬────────────┘                        │   │
│  └───────────────────────────│─────────────────────────────────────┘   │
│                              ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    Fake ChatService                              │   │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐       │   │
│  │  │FakePii    │ │FakeIntent │ │FakeRAG    │ │FakeLLM    │       │   │
│  │  │Service    │ │Service    │ │Client     │ │Client     │       │   │
│  │  └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘       │   │
│  │        │             │             │             │               │   │
│  │        └──────────────┴─────────────┴─────────────┘               │   │
│  │                              │                                    │   │
│  │                    FakeAILogService                               │   │
│  └──────────────────────────────│────────────────────────────────────┘   │
│                                 ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      Test Assertions                             │   │
│  │  - HTTP Response (200, JSON schema)                              │   │
│  │  - PII Masking (input/output/log)                                │   │
│  │  - RAG Integration (sources, rag_used)                           │   │
│  │  - LLM Response (answer)                                         │   │
│  │  - AI Logging (log entries)                                      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Fake vs Mock 선택 이유

### Fake 클래스 사용
- **예측 가능한 동작**: 미리 정의된 규칙/데이터로 deterministic 테스트
- **호출 기록**: 서비스 간 상호작용 검증 가능
- **유연한 설정**: 시나리오별 다른 동작 설정 (`should_fail`, `fail_if_called`)
- **실제 인터페이스 구현**: 타입 안전성 보장

### dependency_overrides 패턴
- FastAPI 공식 권장 테스트 패턴
- 실제 라우트 로직 검증 (미들웨어, 검증 등 포함)
- 통합 테스트 수준의 신뢰성

## 테스트 커버리지 분석

| 컴포넌트 | Phase 6 (Service) | Phase 7 (HTTP) |
|----------|-------------------|----------------|
| PiiService | ✓ | ✓ (마스킹 + 호출 기록) |
| IntentService | ✓ | ✓ |
| RagflowClient | ✓ | ✓ (호출/미호출 검증) |
| LLMClient | ✓ | ✓ (메시지 검증) |
| AILogService | ✓ | ✓ (로그 내용 검증) |
| FastAPI Route | - | ✓ |
| HTTP Request/Response | - | ✓ |
| JSON Schema | - | ✓ |

## 다음 단계 (Phase 8 후보)

1. **Streaming 응답 지원**: SSE 기반 스트리밍 E2E 테스트
2. **에러 케이스 확장**: 400, 422, 500 에러 시나리오
3. **멀티턴 대화 테스트**: 대화 히스토리 처리 검증
4. **성능 테스트**: 응답 시간, 동시 요청 처리
5. **실제 서버 통합 테스트**: Docker Compose 환경

---

**작성일**: 2025-12-09
**작성자**: Claude Opus 4.5 (AI Assistant)
