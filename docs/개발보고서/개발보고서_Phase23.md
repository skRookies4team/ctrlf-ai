# Phase 23 개발 보고서: 개인화(Personalization) 구현

## 개요

Phase 23에서는 개인화(Personalization) 기능을 구현했습니다.
사용자의 개인 정보(연차, 교육 현황, 복지 포인트 등)를 조회하고 자연어 답변을 생성하는 전체 파이프라인을 구현했습니다.

### 주요 기능
- **Q1-Q20 개인화 인텐트**: 20개 세부 의도 분류 (우선순위 8개 완전 구현)
- **Clarify 템플릿**: 애매한 경계에서 되묻기 (6개 고정 문장)
- **BACKEND_API 라우팅**: 백엔드 facts 조회 → LLM 답변 생성
- **부서 검색**: Q5 타부서 평균 비교 지원

---

## 1. 변경/추가된 파일 목록

### 신규 파일
| 파일 경로 | 설명 |
|-----------|------|
| `app/models/personalization.py` | 개인화 모델 (PeriodType, Facts, SubIntentMetadata 등) |
| `app/clients/personalization_client.py` | 개인화 API 클라이언트 (Resolve + Dept Search) |
| `app/services/answer_generator.py` | Facts 기반 자연어 답변 생성 서비스 |
| `tests/test_personalization.py` | 개인화 기능 테스트 (34개) |

### 수정된 파일
| 파일 경로 | 변경 내용 |
|-----------|-----------|
| `app/models/router_types.py` | SubIntentId에 Q1-Q20 추가, ClarifyTemplates 업데이트 |
| `app/services/llm_router.py` | 시스템 프롬프트에 Q1-Q20 추가, Few-shot 예시 확장 |
| `app/services/rule_router.py` | Q1-Q20 키워드 추가, `_check_personalization_intents()` 메서드 |
| `app/services/router_orchestrator.py` | BACKEND_API 실행 로직, 부서 검색 처리 |

---

## 2. 핵심 코드

### 2.1 개인화 모델 (`app/models/personalization.py`)

```python
class PeriodType(str, Enum):
    """기간 유형 (4개 고정)."""
    THIS_WEEK = "this-week"
    THIS_MONTH = "this-month"
    THREE_MONTHS = "3m"
    THIS_YEAR = "this-year"

class PersonalizationSubIntentId(str, Enum):
    """Q1-Q20 개인화 인텐트."""
    Q1 = "Q1"   # 미이수 필수 교육 조회
    Q3 = "Q3"   # 이번 달 데드라인 필수 교육
    Q5 = "Q5"   # 내 평균 vs 부서 평균 vs 전사 평균
    Q6 = "Q6"   # 가장 많이 틀린 보안 토픽 TOP3
    Q9 = "Q9"   # 이번 주 교육/퀴즈 할 일
    Q11 = "Q11" # 남은 연차 일수
    Q14 = "Q14" # 복지/식대 포인트 잔액
    Q20 = "Q20" # 올해 HR 할 일 (미완료)
    # ... Q2-Q19

class PersonalizationFacts(BaseModel):
    """백엔드에서 반환하는 정답 데이터."""
    sub_intent_id: str
    period_start: Optional[str]
    period_end: Optional[str]
    updated_at: Optional[str]
    metrics: Dict[str, Any]       # 수치 데이터
    items: List[Dict[str, Any]]   # 목록 데이터
    extra: Dict[str, Any]         # 추가 데이터
    error: Optional[PersonalizationError]  # 에러 정보
```

### 2.2 개인화 클라이언트 (`app/clients/personalization_client.py`)

```python
class PersonalizationClient:
    """개인화 API 클라이언트."""

    RESOLVE_PATH = "/api/personalization/resolve"
    DEPT_SEARCH_PATH = "/api/org/departments/search"

    async def resolve_facts(
        self,
        sub_intent_id: str,
        period: Optional[str] = None,
        target_dept_id: Optional[str] = None,
    ) -> PersonalizationFacts:
        """개인화 facts 조회."""
        # 기본 period 설정
        if period is None:
            period = DEFAULT_PERIOD_FOR_INTENT.get(sub_intent_id, PeriodType.THIS_YEAR).value

        # 우선순위 인텐트가 아닌 경우 NOT_IMPLEMENTED
        if sub_intent_id not in PRIORITY_SUB_INTENTS:
            return PersonalizationFacts(
                sub_intent_id=sub_intent_id,
                error=PersonalizationError(type="NOT_IMPLEMENTED", message="...")
            )

        # 백엔드 호출 또는 mock 응답
        ...

    async def search_departments(self, query: str) -> DepartmentSearchResponse:
        """부서 검색."""
        ...
```

### 2.3 Answer Generator (`app/services/answer_generator.py`)

```python
class AnswerGenerator:
    """Facts 기반 답변 생성기."""

    async def generate(self, context: AnswerGeneratorContext) -> str:
        """facts 기반으로 자연어 답변 생성."""
        facts = context.facts

        # 에러가 있으면 에러 템플릿 반환
        if facts.error:
            return ERROR_RESPONSE_TEMPLATES.get(facts.error.type, "조회 중 오류가 발생했어요.")

        # facts가 비어있으면 기본 메시지
        if not facts.metrics and not facts.items:
            return "조회된 데이터가 없어요."

        # LLM으로 답변 생성
        return await self._generate_with_llm(context)

    async def _generate_with_llm(self, context: AnswerGeneratorContext) -> str:
        """LLM을 사용하여 답변 생성."""
        # 시스템 프롬프트: facts에 있는 값만 사용, 추측 금지
        ...
```

### 2.4 Rule Router 개인화 키워드 (`app/services/rule_router.py`)

```python
# Q11: 남은 연차 일수
Q11_KEYWORDS = frozenset([
    "연차 몇 개", "연차 몇 일", "연차 며칠",
    "남은 연차", "잔여 연차", "연차 남았",
])

# Q14: 복지/식대 포인트 잔액
Q14_KEYWORDS = frozenset([
    "복지 포인트", "복지포인트", "포인트 얼마",
    "포인트 잔액", "식대", "식대 얼마",
])

def _check_personalization_intents(self, query_lower: str, debug_info) -> Optional[RouterResult]:
    """Q1-Q20 개인화 인텐트 체크."""
    if self._contains_any(query_lower, Q11_KEYWORDS):
        return RouterResult(
            tier0_intent=Tier0Intent.BACKEND_STATUS,
            sub_intent_id=SubIntentId.Q11.value,
            route_type=RouterRouteType.BACKEND_API,
            confidence=0.95,
            ...
        )
    # ... Q1, Q3, Q5, Q6, Q9, Q14, Q20 체크
```

### 2.5 Orchestrator BACKEND_API 처리 (`app/services/router_orchestrator.py`)

```python
async def _execute_backend_api(self, router_result: RouterResult, user_query: str) -> OrchestrationResult:
    """BACKEND_API 라우트 실행."""
    sub_intent_id = router_result.sub_intent_id

    # Q5 특수 처리: 부서 검색
    target_dept_id = None
    if sub_intent_id == "Q5":
        dept_result = await self._handle_q5_department_search(user_query)
        if dept_result.needs_clarify:
            return OrchestrationResult(
                needs_user_response=True,
                response_message=dept_result.clarify_message,
            )
        target_dept_id = dept_result.dept_id

    # 개인화 facts 조회
    facts = await self._personalization_client.resolve_facts(
        sub_intent_id=sub_intent_id,
        target_dept_id=target_dept_id,
    )

    # Answer Generator로 답변 생성
    context = AnswerGeneratorContext(
        sub_intent_id=sub_intent_id,
        user_question=user_query,
        facts=facts,
    )
    answer = await self._answer_generator.generate(context)

    return OrchestrationResult(
        router_result=router_result,
        answer=answer,
        facts=facts,
    )
```

---

## 3. Clarify 템플릿 (확정 6문장)

### 경계 A: 교육 내용 vs 이수현황
```python
EDUCATION_CONTENT_VS_STATUS = [
    "교육 내용 설명이 필요해요, 아니면 내 이수현황/진도 조회가 필요해요?",
    "교육을 요약/설명해드릴까요, 아니면 내 기록(수료/시청률/점수)을 조회해드릴까요?",
    "지금은 교육 안내가 필요해요, 아니면 내가 어디까지 했는지 확인이 필요해요?",
]
```

### 경계 B: 규정 vs HR 개인화
```python
POLICY_VS_HR_PERSONAL = [
    "이건 회사 규정(정책) 설명을 원하세요, 아니면 내 HR 정보(연차/근태/복지) 조회를 원하세요?",
    "규정의 기준/원칙을 찾을까요, 아니면 내 잔여/내역 같은 개인 데이터를 볼까요?",
    "질문이 정책(허용/금지/절차) 쪽인가요, 아니면 내 연차/급여/포인트 같은 개인화 쪽인가요?",
]
```

### 부서 검색 관련
```python
MULTIPLE_MATCHES = "어느 부서 기준으로 볼까요? 부서명을 정확히 입력해 주세요."
NO_MATCHES = "해당 부서를 찾지 못했어요. 부서명을 정확히 입력해 주세요."
```

### 파싱 실패/분류 불가
```python
UNKNOWN_FALLBACK = "원하시는 작업을 조금만 더 구체적으로 말해 주세요."
```

---

## 4. 테스트 실행 방법

### 전체 테스트 실행
```bash
python -m pytest tests/ -v
```

### 개인화 테스트만 실행
```bash
python -m pytest tests/test_personalization.py -v
```

### 테스트 결과
```
627 passed, 12 skipped in 35.80s
```

**Phase 23 신규 테스트 (34개):**
- `TestPersonalizationModels`: 11개 (모델 테스트)
- `TestRouterTypesPersonalization`: 4개 (라우터 타입 테스트)
- `TestPersonalizationClient`: 5개 (클라이언트 테스트)
- `TestRuleRouterPersonalization`: 7개 (Rule Router 테스트)
- `TestAnswerGenerator`: 2개 (Answer Generator 테스트)
- `TestDepartmentClarifyTemplates`: 2개 (부서 템플릿 테스트)
- `TestOrchestratorPersonalization`: 3개 (통합 테스트)

---

## 5. 대표 시나리오 요청/응답 예시

### 시나리오 1: 연차 조회 (Q11)

**요청 (POST /ai/chat/messages)**
```json
{
  "session_id": "sess-001",
  "user_id": "user-123",
  "user_role": "EMPLOYEE",
  "messages": [
    {"role": "user", "content": "내 연차 며칠 남았어?"}
  ]
}
```

**라우팅 결과**
```json
{
  "tier0_intent": "BACKEND_STATUS",
  "domain": "HR",
  "route_type": "BACKEND_API",
  "sub_intent_id": "Q11",
  "confidence": 0.95
}
```

**Facts (백엔드 응답)**
```json
{
  "sub_intent_id": "Q11",
  "period_start": "2025-01-01",
  "period_end": "2025-12-31",
  "metrics": {
    "total_days": 15,
    "used_days": 8,
    "remaining_days": 7
  }
}
```

**최종 응답**
```json
{
  "answer": "2025년 기준, 남은 연차는 7일입니다. (총 15일 중 8일 사용)",
  "meta": {
    "intent": "BACKEND_STATUS",
    "route": "BACKEND_API",
    "sub_intent_id": "Q11",
    "confidence": 0.95
  }
}
```

### 시나리오 2: 교육 애매 → 되묻기 → 2턴 응답

**Turn 1: 요청**
```json
{
  "messages": [{"role": "user", "content": "교육 알려줘"}]
}
```

**Turn 1: 응답 (needs_clarify=true)**
```json
{
  "answer": "교육 내용 설명이 필요해요, 아니면 내 이수현황/진도 조회가 필요해요?",
  "meta": {
    "needs_clarify": true,
    "domain": "EDU",
    "clarify_group": "EDU"
  }
}
```
→ PendingAction 저장: `{original_query: "교육 알려줘", clarify_group: EDU, expires_at: +5분}`

**Turn 2: 유저 응답 "이수현황"**
```json
{
  "messages": [{"role": "user", "content": "이수현황"}]
}
```

**Turn 2: 처리 과정**
1. `PendingActionStore.get(session_id)` → pending 존재
2. 응답 길이: 4자 ≤ 20자 → 짧은 응답
3. 키워드 매핑: `"이수" in CLARIFY_KEYWORD_MAPPING[EDU]` → `BACKEND_STATUS`
4. pending 삭제 (one-shot)
5. BACKEND_STATUS 라우트 실행 → PersonalizationClient 호출

**Turn 2: 최종 응답**
```json
{
  "answer": "현재 이수한 교육은 3건입니다.\n- 정보보호교육 (완료, 2025-01-15)\n- 개인정보보호교육 (완료, 2025-01-10)\n미이수 필수 교육: 보안교육 (마감 2025-01-31)",
  "meta": {
    "route": "BACKEND_API",
    "sub_intent_id": "EDU_STATUS_CHECK",
    "confidence": 0.9
  }
}
```

### 시나리오 3: 부서 평균 비교 (Q5)

**요청**
```json
{
  "messages": [{"role": "user", "content": "마케팅팀 평균이랑 비교해줘"}]
}
```

**처리 과정**
1. Rule Router: Q5 (평균 비교) 감지
2. 부서 검색: "마케팅" → 1건 매칭
3. Facts 조회: target_dept_id="D004"
4. Answer 생성

**응답**
```json
{
  "answer": "교육 점수 평균 비교:\n- 내 평균: 85.5점\n- 마케팅팀 평균: 82.3점\n- 전사 평균: 80.1점",
  "meta": {
    "sub_intent_id": "Q5"
  }
}
```

### 시나리오 4: 미이수 교육 조회 (Q1)

**요청**
```json
{
  "messages": [{"role": "user", "content": "아직 안 들은 필수 교육 뭐 있어?"}]
}
```

**응답**
```json
{
  "answer": "미이수 필수 교육이 2건 있어요.\n- 개인정보보호 교육 (마감: 1/31)\n- 정보보안 교육 (마감: 2/15)",
  "meta": {
    "sub_intent_id": "Q1"
  }
}
```

---

## 6. 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Router Orchestrator                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  route()                                                             │
│    │                                                                 │
│    ├─► [1] pending 체크 (PendingActionStore.get)                    │
│    │         │                                                       │
│    │         └─ pending 있으면 → _handle_pending_response()         │
│    │               │                                                 │
│    │               ├─ 짧은 응답(≤20자) → 키워드 매핑 → 라우팅 결정  │
│    │               │     예: "이수현황" → BACKEND_STATUS             │
│    │               │     예: "내용" → RAG_INTERNAL                   │
│    │               │                                                 │
│    │               ├─ 긴 응답(>20자) → 새 질문으로 처리 (재라우팅)   │
│    │               │                                                 │
│    │               └─ 매핑 실패 → 원문+응답 결합 후 재라우팅         │
│    │                                                                 │
│    ├─► [2] RuleRouter.route() (키워드 기반 1차 분류)                │
│    │         │                                                       │
│    │         ├─ 개인화 키워드 (EDU_STATUS, HR_PERSONAL 등)          │
│    │         │    → confidence=0.9, BACKEND_API                      │
│    │         │                                                       │
│    │         ├─ 치명 액션 (QUIZ 3종)                                │
│    │         │    → confidence=0.95, requires_confirmation=True      │
│    │         │                                                       │
│    │         ├─ 콘텐츠 키워드 (POLICY, EDU_CONTENT 등)              │
│    │         │    → confidence=0.85, RAG_INTERNAL                    │
│    │         │                                                       │
│    │         └─ 애매한 경계 감지                                     │
│    │              → confidence=0.3, needs_clarify=True               │
│    │                                                                 │
│    ├─► [3] needs_clarify? ──Yes──► _create_clarify_result()         │
│    │         │                      │                                │
│    │         │                      └─ pending 저장 (TTL=5분)       │
│    │         │                          original_query, clarify_group│
│    │                                                                 │
│    ├─► [4] confidence < 0.85? ──► LLMRouter.route() (fallback)      │
│    │                                                                 │
│    ├─► [5] route_type별 실행                                        │
│    │     │                                                           │
│    │     ├─ BACKEND_API ──────► _execute_backend_api()              │
│    │     │     │                                                     │
│    │     │     ├─► Q5? → _handle_q5_department_search()             │
│    │     │     ├─► PersonalizationClient.resolve_facts()            │
│    │     │     └─► AnswerGenerator.generate()                       │
│    │     │                                                           │
│    │     ├─ RAG_INTERNAL → RAGFlowClient                            │
│    │     └─ LLM_ONLY → LLMClient                                    │
│    │                                                                 │
│    └─► OrchestrationResult (answer, facts)                          │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     PersonalizationClient                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  resolve_facts(sub_intent_id, period, target_dept_id)               │
│    │                                                                 │
│    ├─► 기본 period 설정 (DEFAULT_PERIOD_FOR_INTENT)                 │
│    │     Q3: this-month, Q5: this-year, Q6: 3m, Q9: this-week       │
│    │                                                                 │
│    ├─► NOT in PRIORITY_SUB_INTENTS? → NOT_IMPLEMENTED 에러          │
│    │                                                                 │
│    ├─► Mock 모드? → _get_mock_facts()                               │
│    │                                                                 │
│    └─► HTTP POST /api/personalization/resolve                       │
│                                                                      │
│  search_departments(query)                                           │
│    │                                                                 │
│    └─► HTTP GET /api/org/departments/search?query=...               │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                       AnswerGenerator                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  generate(context)                                                   │
│    │                                                                 │
│    ├─► facts.error? → ERROR_RESPONSE_TEMPLATES[error.type]          │
│    │     NOT_FOUND: "해당 기간에 조회할 데이터가 없어요."           │
│    │     TIMEOUT: "지금 조회가 지연되고 있어요..."                  │
│    │     PARTIAL: "일부 정보만 가져올 수 있었어요..."               │
│    │                                                                 │
│    ├─► facts 비어있음? → "조회된 데이터가 없어요."                  │
│    │                                                                 │
│    ├─► _generate_with_llm(context)                                  │
│    │     │                                                           │
│    │     └─► 시스템 프롬프트: "facts에 있는 값만 사용, 추측 금지"   │
│    │                                                                 │
│    └─► 실패 시 → _generate_fallback(context)                        │
│          Q11: "남은 연차: {remaining}일 (총 {total}일 중 {used}일)" │
│          Q14: "포인트 잔액:\n- 복지: {welfare}원\n- 식대: {meal}원" │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. Q1-Q20 개인화 스코프

| ID | 설명 | 기본 Period | Domain | 우선순위 |
|----|------|------------|--------|---------|
| Q1 | 미이수 필수 교육 조회 | - | EDU | ✅ |
| Q2 | 내 교육 수료 현황 조회 | - | EDU | |
| Q3 | 이번 달 데드라인 필수 교육 | this-month | EDU | ✅ |
| Q4 | 특정 교육 진도율/시청률 조회 | - | EDU | |
| Q5 | 내 평균 vs 부서/전사 평균 | this-year | QUIZ | ✅ |
| Q6 | 가장 많이 틀린 보안 토픽 TOP3 | 3m | QUIZ | ✅ |
| Q7 | 특정 교육 퀴즈 결과 조회 | - | QUIZ | |
| Q8 | 내 퀴즈 점수 이력 조회 | - | QUIZ | |
| Q9 | 이번 주 교육/퀴즈 할 일 | this-week | EDU | ✅ |
| Q10 | 내 근태 현황 조회 | - | HR | |
| Q11 | 남은 연차 일수 | this-year | HR | ✅ |
| Q12 | 연차 사용 이력 조회 | - | HR | |
| Q13 | 급여 명세서 요약 | - | HR | |
| Q14 | 복지/식대 포인트 잔액 | this-year | HR | ✅ |
| Q15 | 복지 포인트 사용 내역 | - | HR | |
| Q16 | 내 인사 정보 조회 | - | HR | |
| Q17 | 내 팀/부서 정보 조회 | - | HR | |
| Q18 | 보안 교육 이수 현황 | - | EDU | |
| Q19 | 필수 교육 전체 요약 | - | EDU | |
| Q20 | 올해 HR 할 일 (미완료) | this-year | HR | ✅ |

---

## 8. 백엔드 API 연동 스펙

### POST /api/personalization/resolve
```json
// Request
{
  "sub_intent_id": "Q11",
  "period": "this-year",
  "target_dept_id": null
}

// Response
{
  "sub_intent_id": "Q11",
  "period_start": "2025-01-01",
  "period_end": "2025-12-31",
  "updated_at": "2025-01-18T10:30:00Z",
  "metrics": {"total_days": 15, "used_days": 8, "remaining_days": 7},
  "items": [],
  "extra": {},
  "error": null
}
```

### GET /api/org/departments/search
```json
// Request: ?query=마케팅

// Response
{
  "items": [
    {"dept_id": "D004", "dept_name": "마케팅팀"},
    {"dept_id": "D005", "dept_name": "마케팅기획팀"}
  ]
}
```

---

## 9. 피드백 기반 개선 (2025-12-18)

### A. Q5/Q6 Domain 불일치 수정
- Q5(평균 비교), Q6(오답 토픽 TOP3)의 Domain을 `EDU` → `QUIZ`로 변경
- 이유: "교육 콘텐츠 질의"가 아닌 "퀴즈/평가 지표" 성격이므로 QUIZ가 더 적합
- 변경 파일: `app/models/personalization.py` (SUB_INTENT_METADATA)

### B. NOT_IMPLEMENTED 에러 메시지 개선
- 기존: "아직 준비 중인 기능이에요. 곧 제공해 드릴게요."
- 변경: "현재 데모 범위에서는 지원하지 않는 질문이에요. 지원되는 질문 예시: 남은 연차, 복지 포인트 잔액, 미이수 필수 교육, 이번 주 할 일 등"
- 장점: 데모 시 "우선순위만 풀구현" 의도가 자연스럽게 전달됨

### C. DepartmentInfo에 dept_path 필드 추가
- 동명이부서/유사부서 대비용으로 "부서 경로(path)" 필드 추가 (옵션)
- 예시: `"본사 > 개발본부 > 개발팀"`
- 장점: "마케팅팀" 같은 애매 검색어에서 되묻기 품질 상승

### D. 작성일/기간 표기 정정
- 문서 작성일: `2025-01-18` → `2025-12-18`로 수정

### E. 테스트 추가 (3개)
- `test_not_implemented_error_message`: NOT_IMPLEMENTED 메시지 내용 검증
- `test_department_info_with_path`: dept_path 필드 포함 DepartmentInfo 테스트
- `test_q5_q6_domain_is_quiz`: Q5/Q6 Domain이 QUIZ인지 확인

---

## 10. 향후 개선 사항

1. **나머지 Q2-Q19 구현**: 백엔드 준비 후 순차 구현
2. **세션 기반 컨텍스트**: 이전 대화 컨텍스트 활용
3. **캐싱**: 자주 조회되는 facts 캐싱
4. **분석**: 개인화 질의 패턴 분석 및 최적화

---

---

## 11. 되묻기 후 2턴 처리 방식 (Clarify Answer Handler)

### 11.1 개요

Phase 23에서 "되묻기(clarify)" 후 사용자의 짧은 응답을 처리하는 2턴 대화 흐름을 구현했습니다.

**문제 상황:**
- 사용자: "교육 알려줘"
- AI: "교육 내용 설명이 필요해요, 아니면 내 이수현황/진도 조회가 필요해요?"
- 사용자: "이수현황" ← 이 짧은 응답을 어떻게 처리할 것인가?

**해결 방안:**
- PendingActionStore에 clarify 상태를 저장
- 다음 요청에서 pending이 있으면 ClarifyAnswerHandler로 처리
- 짧은 응답(≤20자)은 키워드 매핑으로 라우팅 결정
- 긴 응답(>20자)은 새 질문으로 처리

### 11.2 PendingAction 모델 확장

```python
@dataclass
class PendingAction:
    action_type: PendingActionType
    trace_id: str
    pending_intent: Tier0Intent
    sub_intent_id: str = ""
    router_result: Optional[RouterResult] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Phase 23: 되묻기 후 2턴 처리를 위한 필드
    expires_at: Optional[datetime] = None      # TTL 만료 시각
    original_query: str = ""                   # 원래 질문 ("교육 알려줘")
    clarify_group: ClarifyGroup = ClarifyGroup.UNKNOWN  # 카테고리
    user_id: str = ""                          # 사용자 ID
```

### 11.3 ClarifyGroup 및 키워드 매핑

```python
class ClarifyGroup(str, Enum):
    EDU = "EDU"         # 교육 관련
    FAQ = "FAQ"         # 일반 FAQ
    PROFILE = "PROFILE" # 프로필/인사정보
    POLICY = "POLICY"   # 규정/정책
    INCIDENT = "INCIDENT"  # 사건/사고
    UNKNOWN = "UNKNOWN"

CLARIFY_KEYWORD_MAPPING: dict[ClarifyGroup, dict[str, str]] = {
    ClarifyGroup.EDU: {
        "이수": "BACKEND_STATUS",      # 이수현황 → 백엔드 조회
        "진도": "BACKEND_STATUS",      # 진도율 → 백엔드 조회
        "조회": "BACKEND_STATUS",      # 조회 → 백엔드 조회
        "내용": "RAG_INTERNAL",        # 내용 → RAG 검색
        "설명": "RAG_INTERNAL",        # 설명 → RAG 검색
        "요약": "RAG_INTERNAL",        # 요약 → RAG 검색
        "교육과정": "RAG_INTERNAL",    # 교육과정 → RAG 검색
    },
    ClarifyGroup.POLICY: {
        "규정": "RAG_INTERNAL",
        "정책": "RAG_INTERNAL",
        "개인": "BACKEND_STATUS",
        "조회": "BACKEND_STATUS",
    },
    # ... 기타 그룹
}

CLARIFY_SHORT_RESPONSE_MAX_LENGTH = 20  # 짧은 응답 기준
```

### 11.4 TTL (Time-To-Live) 설정

```python
# app/core/config.py
ROUTER_PENDING_TIMEOUT_SECONDS: int = 300  # 5분

# PendingActionStore에서 TTL 체크
def get(self, session_id: str) -> Optional[PendingAction]:
    pending = self._store.get(session_id)
    if pending is None:
        return None

    # TTL 만료 체크
    if pending.expires_at and datetime.now(timezone.utc) > pending.expires_at:
        self.delete(session_id)
        return None

    return pending
```

### 11.5 ClarifyAnswerHandler 처리 흐름

```python
async def _handle_pending_response(
    self,
    pending: PendingAction,
    user_query: str,
    session_id: str,
    ...
) -> OrchestrationResult:
    """pending clarify에 대한 응답 처리."""

    # 1. pending 삭제 (one-shot)
    self._pending_store.delete(session_id)

    # 2. 응답 길이 체크
    response_length = len(user_query.strip())

    # 3. 긴 응답(20자 초과)이면 새 질문으로 처리
    if response_length > CLARIFY_SHORT_RESPONSE_MAX_LENGTH:
        return await self.route(user_query=user_query, ...)

    # 4. 짧은 응답: 키워드 매핑
    resolved_route = self._resolve_clarify_answer(
        user_response=user_query,
        clarify_group=pending.clarify_group,
    )

    if resolved_route:
        # 매핑 성공 → 해당 라우트로 실행
        return await self._execute_route(resolved_route, ...)
    else:
        # 매핑 실패 → 원문+응답 결합 재라우팅
        combined_query = f"{pending.original_query} {user_query}"
        return await self.route(user_query=combined_query, ...)
```

### 11.6 시나리오 예시

#### 시나리오 1: 교육 → 이수현황

**Turn 1 (되묻기 발생)**
```
User: "교육 알려줘"
→ Router: needs_clarify=true, clarify_group=EDU
→ PendingAction 저장: {original_query: "교육 알려줘", clarify_group: EDU, expires_at: +5분}
→ AI: "교육 내용 설명이 필요해요, 아니면 내 이수현황/진도 조회가 필요해요?"
```

**Turn 2 (응답 처리)**
```
User: "이수현황"
→ pending 조회: {original_query: "교육 알려줘", clarify_group: EDU}
→ 짧은 응답(4자) → 키워드 매핑
→ "이수" in CLARIFY_KEYWORD_MAPPING[EDU] → "BACKEND_STATUS"
→ pending 삭제 (one-shot)
→ BACKEND_STATUS 라우트 실행
→ AI: "현재 이수한 교육은 3건입니다. 미이수 필수 교육: 보안교육(마감 1/31)"
```

#### 시나리오 2: TTL 만료

```
User: "교육 알려줘"
→ pending 저장 (expires_at: 10:00:00 + 5분 = 10:05:00)
→ AI: "교육 내용 설명이 필요해요..."

(5분 후, 10:06:00)

User: "이수현황"
→ pending 조회: expires_at(10:05:00) < now(10:06:00) → 만료
→ pending 삭제, None 반환
→ 새 질문으로 처리: "이수현황" 라우팅
```

#### 시나리오 3: 긴 응답 (새 질문)

```
User: "교육 알려줘"
→ pending 저장
→ AI: "교육 내용 설명이 필요해요..."

User: "연차 휴가 규정이 어떻게 되는지 자세하게 알려주세요"
→ pending 조회
→ 긴 응답(25자 > 20자) → 새 질문으로 처리
→ pending 삭제
→ 새 라우팅: "연차 휴가 규정이 어떻게 되는지..." → POLICY_QA
```

### 11.7 테스트 커버리지

**Phase 23 Clarify Flow 테스트 (22개)**

| 테스트 클래스 | 테스트 수 | 설명 |
|-------------|----------|------|
| TestPendingActionModel | 4 | 모델 필드 검증 |
| TestPendingActionStoreTTL | 3 | TTL 만료/삭제 검증 |
| TestClarifyFlowPendingCreation | 3 | clarify시 pending 생성 검증 |
| TestClarifyAnswerHandler | 4 | 키워드 매핑 검증 |
| TestLongResponseHandling | 2 | 긴 응답 처리 검증 |
| TestCombinedQueryRouting | 1 | 결합 쿼리 라우팅 검증 |
| TestClarifyGroupDetermination | 5 | Intent→ClarifyGroup 매핑 |

```bash
# 테스트 실행
python -m pytest tests/test_phase23_clarify_flow.py -v

# 결과
22 passed in 1.48s
```

---

**작성일**: 2025-12-18
**Phase**: 23
**테스트 결과**: 652 passed (22 clarify flow tests + 37 personalization tests)
