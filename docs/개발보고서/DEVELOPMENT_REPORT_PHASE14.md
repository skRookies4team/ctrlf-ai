# Phase 14: RAG Gap Candidate 플래그 구현

## 개요

Phase 14에서는 **RAG Gap Candidate** 플래그를 추가하여 POLICY/EDU 도메인에서 RAG 검색 결과가 없거나 점수가 낮은 질문을 식별하는 기능을 구현했습니다.

이 플래그는 **추후 사규/교육 콘텐츠 보완 추천**을 위한 것이며, 최종 사용자 답변 내용에는 직접적인 영향을 주지 않습니다. 백엔드에서 이 플래그를 수집하여 "어떤 질문에 대한 문서가 부족한지" 분석할 수 있습니다.

## 구현 내용

### 1. RAG Gap 판정 헬퍼 함수

`app/services/chat_service.py`에 `is_rag_gap_candidate()` 함수 추가:

```python
# RAG Gap 후보로 간주할 도메인 (POLICY, EDU만 대상)
RAG_GAP_TARGET_DOMAINS = {Domain.POLICY.value, Domain.EDU.value, "POLICY", "EDU"}

# RAG Gap 후보로 간주할 의도 (사규/교육 관련 QA만 대상)
RAG_GAP_TARGET_INTENTS = {
    IntentType.POLICY_QA.value,
    IntentType.EDUCATION_QA.value,
    "POLICY_QA", "EDUCATION_QA", "EDU_QA", "EDU_COURSE_QA", "JOB_EDU_QA",
}

# RAG Gap 판정용 점수 임계값
RAG_GAP_SCORE_THRESHOLD = 0.4

def is_rag_gap_candidate(
    domain: str,
    intent: str,
    rag_source_count: int,
    rag_max_score: Optional[float],
    score_threshold: float = RAG_GAP_SCORE_THRESHOLD,
) -> bool:
    """
    RAG Gap 후보 여부를 판정합니다.

    판정 기준:
    1. 도메인 필터: POLICY, EDU만 대상
    2. 의도 필터: POLICY_QA, EDUCATION_QA 등 QA 유형만 대상
    3. RAG 결과 기준:
       - 결과 0건 → True
       - 최고 점수 < 0.4 → True
       - 최고 점수 >= 0.4 → False
    """
```

### 2. ChatAnswerMeta 필드 추가

`app/models/chat.py`의 `ChatAnswerMeta`에 필드 추가:

```python
# Phase 14: RAG Gap 후보 플래그
rag_gap_candidate: bool = Field(
    default=False,
    description="Whether this is a RAG gap candidate (POLICY/EDU domain with no/low-score RAG results)",
)
```

### 3. AILogEntry 필드 추가

`app/models/ai_log.py`의 `AILogEntry`에 필드 추가:

```python
# Phase 14: RAG Gap 후보 플래그
rag_gap_candidate: bool = Field(
    default=False,
    description="RAG Gap 후보 여부 (POLICY/EDU 도메인에서 RAG 결과 없거나 점수 낮음)",
    serialization_alias="ragGapCandidate",  # 백엔드 API용 camelCase
)
```

### 4. ChatService 파이프라인 적용

`app/services/chat_service.py`의 `handle_chat()` 메서드에 RAG Gap 판정 로직 추가:

```python
# Phase 14: RAG Gap 후보 판정
# RAG 결과에서 최고 점수 추출
rag_max_score: Optional[float] = None
if sources:
    scores = [s.score for s in sources if s.score is not None]
    rag_max_score = max(scores) if scores else None

# RAG Gap 후보 여부 계산
rag_gap_candidate_flag = is_rag_gap_candidate(
    domain=domain,
    intent=intent.value,
    rag_source_count=len(sources),
    rag_max_score=rag_max_score,
)
```

### 5. AI Log 전파

`_send_ai_log()` 메서드에 `rag_gap_candidate` 파라미터 추가 및 전파:

```python
await self._send_ai_log(
    ...
    rag_gap_candidate=rag_gap_candidate_flag,
)
```

## RAG Gap 판정 예시

| 도메인 | 의도 | 결과 수 | 최고 점수 | Gap 후보? |
|--------|------|--------|----------|----------|
| POLICY | POLICY_QA | 0 | - | **True** |
| POLICY | POLICY_QA | 3 | 0.3 | **True** |
| POLICY | POLICY_QA | 5 | 0.85 | False |
| EDU | EDUCATION_QA | 0 | - | **True** |
| INCIDENT | INCIDENT_REPORT | 0 | - | False |
| GENERAL | GENERAL_CHAT | 0 | - | False |

## HTTP 응답 예시

```json
{
  "answer": "연차 규정에 대한 정확한 답변을 찾지 못했습니다...",
  "sources": [],
  "meta": {
    "route": "RAG_INTERNAL",
    "intent": "POLICY_QA",
    "domain": "POLICY",
    "rag_used": false,
    "rag_source_count": 0,
    "rag_gap_candidate": true,
    "latency_ms": 150
  }
}
```

## 백엔드 로그 JSON 예시

```json
{
  "log": {
    "sessionId": "session-123",
    "userId": "user-456",
    "domain": "POLICY",
    "intent": "POLICY_QA",
    "route": "RAG_INTERNAL",
    "ragUsed": false,
    "ragSourceCount": 0,
    "ragGapCandidate": true,
    "latencyMs": 150
  }
}
```

## 테스트 결과

### Phase 14 테스트 (32개 통과)

```
tests/test_phase14_rag_gap_candidate.py::TestIsRagGapCandidate::test_policy_qa_with_zero_sources_returns_true PASSED
tests/test_phase14_rag_gap_candidate.py::TestIsRagGapCandidate::test_policy_qa_with_low_score_returns_true PASSED
tests/test_phase14_rag_gap_candidate.py::TestIsRagGapCandidate::test_policy_qa_with_high_score_returns_false PASSED
tests/test_phase14_rag_gap_candidate.py::TestIsRagGapCandidate::test_incident_domain_always_returns_false PASSED
tests/test_phase14_rag_gap_candidate.py::TestIsRagGapCandidate::test_general_chat_intent_always_returns_false PASSED
...
tests/test_phase14_rag_gap_candidate.py::TestChatServiceRagGapIntegration::test_rag_gap_true_when_policy_qa_no_sources PASSED
tests/test_phase14_rag_gap_candidate.py::TestChatServiceRagGapIntegration::test_rag_gap_false_for_general_chat PASSED
tests/test_phase14_rag_gap_candidate.py::TestAILogServiceRagGapPropagation::test_create_log_entry_includes_rag_gap_candidate PASSED
```

### 전체 테스트 (261개 통과)

```
============================= 261 passed in 3.66s =============================
```

## 변경된 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/services/chat_service.py` | `is_rag_gap_candidate()` 함수, 상수, 파이프라인 로직 |
| `app/models/chat.py` | `ChatAnswerMeta.rag_gap_candidate` 필드 |
| `app/models/ai_log.py` | `AILogEntry.rag_gap_candidate` 필드 (camelCase alias) |
| `app/services/ai_log_service.py` | `create_log_entry()` 파라미터 |
| `tests/test_phase14_rag_gap_candidate.py` | 32개 테스트 케이스 |

## 향후 활용 방안

1. **백엔드 대시보드**: `rag_gap_candidate=true`인 로그를 집계하여 "문서 보완이 필요한 질문 유형" 파악
2. **문서 관리 시스템 연동**: RAG Gap이 자주 발생하는 키워드/주제를 문서 관리 팀에 전달
3. **사규/교육 콘텐츠 보완**: 부족한 문서 영역 식별 및 우선순위 지정

## 결론

Phase 14 구현을 통해 RAG 검색 결과의 품질을 모니터링할 수 있는 기반을 마련했습니다. 이 플래그는 사용자 응답에 영향을 주지 않으면서 백엔드 분석용으로 활용됩니다.
