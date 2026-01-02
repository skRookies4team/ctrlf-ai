# Personalization Implementation Report

개인화(Personalization) 기능 구현에 관한 상세 보고서입니다.

---

## 1. 아키텍처 개요

### 1.1 전체 데이터 플로우

```
User Query
    │
    ▼
┌─────────────────┐
│  ChatService    │  (handle_chat)
│  - PII masking  │
│  - Intent 분류   │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│  RouterOrchestrator     │
│  - Tier0Intent 결정      │
│  - RouteType 결정        │
│  - sub_intent_id 반환    │
└────────┬────────────────┘
         │
         ▼
┌────────────────────────────┐
│  PersonalizationMapper     │
│  - SubIntentId → Q 변환     │
│  - 키워드 기반 세분화        │
│  - Period 추출              │
└────────┬───────────────────┘
         │
         ▼
┌─────────────────────────────┐
│  PersonalizationClient      │
│  - POST /api/personalization│
│  - X-User-Id 헤더           │
│  - Facts 조회               │
└────────┬────────────────────┘
         │
         ▼
┌──────────────────────┐
│  AnswerGenerator     │
│  - Facts → 자연어    │
│  - LLM 기반 생성      │
│  - Fallback 포맷터   │
└────────┬─────────────┘
         │
         ▼
   ChatResponse
```

### 1.2 핵심 컴포넌트

| 컴포넌트 | 파일 위치 | 역할 |
|----------|-----------|------|
| **ChatService** | `app/services/chat_service.py` | 메인 진입점, 개인화 분기 처리 |
| **PersonalizationMapper** | `app/services/personalization_mapper.py` | SubIntentId → Q 변환, 기간 추출 |
| **PersonalizationClient** | `app/clients/personalization_client.py` | Backend API 호출, Facts 조회 |
| **AnswerGenerator** | `app/services/answer_generator.py` | Facts → 자연어 답변 생성 |
| **Models** | `app/models/personalization.py` | 데이터 모델, 메타데이터 정의 |

---

## 2. Q1-Q20 인텐트 체계

### 2.1 전체 인텐트 정의

| Q코드 | 설명 | 도메인 | 기본 Period | 우선순위 |
|-------|------|--------|-------------|---------|
| **Q1** | 미이수 필수 교육 조회 | EDU | - | ✅ 데모 |
| Q2 | 내 교육 수료 현황 조회 | EDU | - | - |
| **Q3** | 이번 달 데드라인 필수 교육 | EDU | this-month | ✅ 데모 |
| Q4 | 특정 교육 진도율/시청률 조회 | EDU | - | - |
| **Q5** | 내 평균 vs 부서/전사 평균 | QUIZ | this-year | ✅ 데모 |
| **Q6** | 가장 많이 틀린 보안 토픽 TOP3 | QUIZ | 3m | ✅ 데모 |
| Q7 | 특정 교육 퀴즈 결과 조회 | QUIZ | - | - |
| Q8 | 내 퀴즈 점수 이력 조회 | QUIZ | - | - |
| **Q9** | 이번 주 교육/퀴즈 할 일 | EDU | this-week | ✅ 데모 |
| Q10 | 내 근태 현황 조회 | HR | - | - |
| **Q11** | 남은 연차 일수 | HR | this-year | ✅ 데모 |
| Q12 | 연차 사용 이력 조회 | HR | - | - |
| Q13 | 급여 명세서 요약 | HR | - | - |
| **Q14** | 복지/식대 포인트 잔액 | HR | this-year | ✅ 데모 |
| Q15 | 복지 포인트 사용 내역 | HR | - | - |
| Q16 | 내 인사 정보 조회 | HR | - | - |
| Q17 | 내 팀/부서 정보 조회 | HR | - | - |
| Q18 | 보안 교육 이수 현황 | EDU | - | - |
| Q19 | 필수 교육 전체 요약 | EDU | - | - |
| **Q20** | 올해 HR 할 일 (미완료) | HR | this-year | ✅ 데모 |

### 2.2 우선순위 구현 대상 (8개)

데모 완전 구현 대상:
- `Q1`: 미이수 필수 교육
- `Q3`: 이번 달 마감 교육
- `Q5`: 평균 점수 비교
- `Q6`: 취약 보안 토픽 TOP3
- `Q9`: 이번 주 할 일
- `Q11`: 남은 연차
- `Q14`: 복지/식대 포인트
- `Q20`: 올해 HR 할 일

```python
# app/models/personalization.py
PRIORITY_SUB_INTENTS = frozenset([
    "Q1", "Q3", "Q5", "Q6", "Q9", "Q11", "Q14", "Q20"
])
```

---

## 3. SubIntentId → Q 매핑 로직

### 3.1 직접 매핑

RuleRouter에서 반환하는 SubIntentId 중 일부는 1:1로 Q에 매핑됩니다:

```python
# app/services/personalization_mapper.py
SUBINTENT_TO_Q = {
    "HR_WELFARE_CHECK": "Q14",    # 복지/식대 포인트
    "HR_ATTENDANCE_CHECK": "Q10", # 근태 현황
}
```

### 3.2 키워드 기반 세분화

RuleRouter가 넓은 범위로 분류하는 경우, 사용자 쿼리의 키워드를 분석하여 세분화합니다:

#### HR_LEAVE_CHECK 세분화

```python
def _classify_hr_leave(query: str) -> str:
    """HR_LEAVE_CHECK를 Q11/Q14/Q10 중 하나로 세분화."""

    # Q14: 복지포인트/식대 키워드
    HR_WELFARE_KEYWORDS = ["복지", "복지포인트", "포인트 잔액", "식대", "선택복지"]

    # Q10: 근태 키워드
    HR_ATTENDANCE_KEYWORDS = ["근태", "출근", "퇴근", "근태현황"]

    # 기본값: Q11 (연차)
    return "Q11"
```

#### EDU_STATUS_CHECK 세분화

```python
def _classify_edu_status(query: str) -> str:
    """EDU_STATUS_CHECK를 Q1/Q2/Q3/Q9 중 하나로 세분화."""

    EDU_STATUS_KEYWORDS = {
        "Q1": ["미이수", "안 들은", "필수 미이수", "안한 교육"],
        "Q3": ["데드라인", "마감", "이번 달", "곧 마감"],
        "Q9": ["이번 주", "할 일", "해야 할", "금주"],
    }

    # 기본값: Q2 (수료현황)
    return "Q2"
```

### 3.3 매핑 플로우

```
to_personalization_q(sub_intent_id, query)
    │
    ├── 이미 Q1-Q20 형식? → 그대로 반환
    │
    ├── SUBINTENT_TO_Q에 있음? → 직접 매핑 반환
    │
    ├── HR_LEAVE_CHECK? → _classify_hr_leave(query)
    │
    ├── EDU_STATUS_CHECK? → _classify_edu_status(query)
    │
    └── 그 외? → None (개인화 대상 아님)
```

---

## 4. Period (기간) 처리

### 4.1 기간 유형

```python
class PeriodType(str, Enum):
    THIS_WEEK = "this-week"      # 이번 주
    THIS_MONTH = "this-month"    # 이번 달
    THREE_MONTHS = "3m"          # 3개월
    THIS_YEAR = "this-year"      # 올해
```

### 4.2 Q별 기본 Period

사용자가 기간을 명시하지 않으면 Q별 기본값을 사용합니다:

```python
DEFAULT_PERIOD_FOR_INTENT = {
    "Q3": PeriodType.THIS_MONTH,     # 이번 달 마감 교육
    "Q5": PeriodType.THIS_YEAR,      # 올해 평균 비교
    "Q6": PeriodType.THREE_MONTHS,   # 최근 3개월 틀린 토픽
    "Q9": PeriodType.THIS_WEEK,      # 이번 주 할 일
    "Q11": PeriodType.THIS_YEAR,     # 올해 연차
    "Q14": PeriodType.THIS_YEAR,     # 올해 포인트 (기간 무관)
    "Q20": PeriodType.THIS_YEAR,     # 올해 HR 할 일
}
```

### 4.3 쿼리에서 기간 추출

```python
PERIOD_KEYWORDS = {
    "이번 주": "this-week",
    "이번주": "this-week",
    "금주": "this-week",
    "이번 달": "this-month",
    "이번달": "this-month",
    "이달": "this-month",
    "3개월": "3m",
    "최근 3개월": "3m",
    "올해": "this-year",
    "금년": "this-year",
}
```

예시:
```python
extract_period_from_query("이번 달 연차 현황") → "this-month"
extract_period_from_query("올해 교육 이수 현황") → "this-year"
extract_period_from_query("연차 며칠?") → None (디폴트 사용)
```

---

## 5. Backend API 연동

### 5.1 PersonalizationClient

```python
class PersonalizationClient:
    RESOLVE_PATH = "/api/personalization/resolve"

    async def resolve_facts(
        self,
        sub_intent_id: str,  # Q1-Q20
        user_id: str,        # X-User-Id 헤더
        period: Optional[str] = None,
    ) -> PersonalizationFacts:
```

### 5.2 API 요청/응답

#### Request

```http
POST /api/personalization/resolve
X-User-Id: emp123
Authorization: Bearer {token}
Content-Type: application/json

{
    "sub_intent_id": "Q11",
    "period": "this-year"
}
```

#### Response (PersonalizationFacts)

```json
{
    "sub_intent_id": "Q11",
    "period_start": "2025-01-01",
    "period_end": "2025-12-31",
    "updated_at": "2025-01-15T10:30:00",
    "metrics": {
        "total_days": 15,
        "used_days": 8,
        "remaining_days": 7
    },
    "items": [],
    "extra": {},
    "error": null
}
```

### 5.3 에러 처리

```python
class PersonalizationErrorType(str, Enum):
    NOT_FOUND = "NOT_FOUND"           # 데이터 없음
    TIMEOUT = "TIMEOUT"               # 조회 지연
    PARTIAL = "PARTIAL"               # 일부만 조회됨
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"  # 미구현 인텐트
```

에러 메시지 템플릿:
```python
ERROR_RESPONSE_TEMPLATES = {
    "NOT_FOUND": "해당 기간에 조회할 데이터가 없어요.",
    "TIMEOUT": "지금 조회가 지연되고 있어요. 잠시 후 다시 시도해 주세요.",
    "PARTIAL": "일부 정보만 가져올 수 있었어요. 가능한 범위에서 정리해 드릴게요.",
    "NOT_IMPLEMENTED": "현재 데모 범위에서는 지원하지 않는 질문이에요. ...",
}
```

---

## 6. 답변 생성 (AnswerGenerator)

### 6.1 LLM 기반 생성

```python
ANSWER_GENERATOR_SYSTEM_PROMPT = """당신은 기업 내부 정보보호 AI 어시스턴트입니다.
주어진 facts 데이터를 바탕으로 사용자에게 친절하고 자연스러운 답변을 작성하세요.

## 중요 규칙
1. **facts에 있는 값만 사용**: 답변에는 facts에 있는 수치, 목록, 날짜만 포함합니다.
2. **추측 금지**: facts에 없는 정보는 절대 추측하거나 생성하지 않습니다.
3. **기간 포함**: period_start/end가 있으면 "~기준" 형태로 자연스럽게 포함합니다.
4. **업데이트 시점**: updated_at이 있으면 필요시 "마지막 업데이트: ~" 형태로 언급합니다.
5. **간결함**: 불필요한 인사나 부가 설명 없이 핵심 정보만 전달합니다.
6. **한국어 사용**: 모든 답변은 한국어로 작성합니다.
"""
```

### 6.2 Fallback 포맷터

LLM 호출 실패 시 Q별 전용 포맷터로 답변을 생성합니다:

| Q코드 | Fallback 함수 | 예시 출력 |
|-------|--------------|----------|
| Q1 | `_format_q1_fallback` | "미이수 필수 교육이 2건 있어요.\n- 개인정보보호 교육 (마감: 1/31)" |
| Q3 | `_format_q3_fallback` | "이번 달 마감되는 필수 교육이 1건 있어요." |
| Q5 | `_format_q5_fallback` | "교육 점수 평균 비교:\n- 내 평균: 85.5점\n- 부서 평균: 82.3점" |
| Q6 | `_format_q6_fallback` | "많이 틀린 보안 토픽 TOP3:\n1. 피싱 메일 식별 (오답률: 35.2%)" |
| Q9 | `_format_q9_fallback` | "이번 주 할 일이 3건 있어요." |
| Q11 | `_format_q11_fallback` | "남은 연차: 7일 (총 15일 중 8일 사용)" |
| Q14 | `_format_q14_fallback` | "포인트 잔액:\n- 복지 포인트: 150,000원\n- 식대: 280,000원" |
| Q20 | `_format_q20_fallback` | "올해 미완료 HR 항목이 4건 있어요." |

---

## 7. ChatService 통합

### 7.1 개인화 분기 조건

`chat_service.py` 라인 787-839:

```python
elif route in backend_api_routes:
    # 1) sub_intent_id 추출
    sub_intent_id = orchestration_result.router_result.sub_intent_id or ""

    # 2) 비어 있으면 clarify 응답
    if not sub_intent_id:
        return ChatResponse(answer=ClarifyTemplates.BACKEND_STATUS_CLARIFY[0], ...)

    # 3) Q 매핑
    personalization_q = to_personalization_q(sub_intent_id, masked_query)

    # 4) 개인화 요청이면 전용 핸들러 호출
    if personalization_q:
        extracted_period = extract_period_from_query(masked_query)
        return await self._handle_personalization(
            q=personalization_q,
            user_query=masked_query,
            period=extracted_period,
            ...
        )

    # 5) 개인화 아니면 기존 BackendHandler 사용
    backend_context = await self._fetch_backend_data_for_api(...)
```

### 7.2 _handle_personalization 메서드

`chat_service.py` 라인 1568-1647:

```python
async def _handle_personalization(self, q, user_query, period, ...):
    # 1) Facts 조회
    facts = await self._personalization_client.resolve_facts(
        sub_intent_id=q,
        user_id=req.user_id,
        period=period,
    )

    # 2) 답변 생성
    context = AnswerGeneratorContext(
        sub_intent_id=q,
        user_question=user_query,
        facts=facts,
    )
    raw_answer = await self._answer_generator.generate(context)

    # 3) PII 출력 마스킹
    pii_output = await self._pii.detect_and_mask(raw_answer, stage=OUTPUT)

    # 4) 응답 반환
    return ChatResponse(
        answer=pii_output.masked_text,
        meta=ChatAnswerMeta(
            route="BACKEND_API",
            personalization_q=q,
            rag_used=False,
            ...
        ),
    )
```

---

## 8. Mock 데이터 지원

백엔드 URL이 설정되지 않은 경우(`BACKEND_BASE_URL` 미설정), 개발/테스트용 mock 데이터를 반환합니다.

### 8.1 Mock 데이터 예시

```python
mock_responses = {
    "Q11": {  # 남은 연차 일수
        "metrics": {
            "total_days": 15,
            "used_days": 8,
            "remaining_days": 7,
        },
    },
    "Q14": {  # 복지/식대 포인트 잔액
        "metrics": {
            "welfare_points": 150000,
            "meal_allowance": 280000,
        },
    },
    "Q1": {  # 미이수 필수 교육
        "metrics": {"total_required": 5, "completed": 3, "remaining": 2},
        "items": [
            {"education_id": "EDU001", "title": "개인정보보호 교육", "deadline": "2025-01-31"},
            {"education_id": "EDU002", "title": "정보보안 교육", "deadline": "2025-02-15"},
        ],
    },
}
```

---

## 9. 테스트 전략

### 9.1 테스트 파일

| 파일 | 테스트 범위 |
|------|------------|
| `test_personalization.py` | 모델/에러 타입 단위 테스트 |
| `test_personalization_mapper.py` | Q 매핑 로직 단위 테스트 |
| `test_personalization_integration.py` | ChatService 통합 테스트 |

### 9.2 주요 테스트 케이스

```python
class TestChatServicePersonalization:
    async def test_personalization_client_called_for_hr_leave(self):
        """HR 연차 조회 시 PersonalizationClient.resolve_facts 호출 확인."""

    async def test_clarify_response_when_sub_intent_id_empty(self):
        """sub_intent_id가 비어있을 때 clarify 응답 반환."""

    async def test_personalization_for_edu_status_check(self):
        """EDU_STATUS_CHECK + 키워드 → Q1 변환 확인."""

    async def test_non_personalization_uses_backend_handler(self):
        """개인화 아닌 BACKEND_API는 기존 BackendHandler 사용."""
```

### 9.3 테스트 Mock 설정 주의사항

**PII Mock Pass-through 패턴**:
키워드 기반 Q 매핑 테스트에서 PII mock이 입력 텍스트를 그대로 반환해야 합니다.

```python
# 올바른 PII mock 설정
async def pii_passthrough(text, **kwargs):
    return MagicMock(masked_text=text, has_pii=False, tags=[])

mock_pii.detect_and_mask = AsyncMock(side_effect=pii_passthrough)
```

**Patch 경로 규칙**:
`get_settings`는 모듈 상단에서 import되므로, 테스트에서는 `app.services.chat_service.get_settings`로 패치합니다.

---

## 10. 응답 메타데이터

개인화 응답의 `ChatAnswerMeta`에는 다음 필드가 포함됩니다:

```python
ChatAnswerMeta(
    route="BACKEND_API",
    intent="BACKEND_STATUS",
    domain="HR",
    masked=False,
    has_pii_input=False,
    has_pii_output=False,
    latency_ms=150,
    rag_used=False,
    rag_source_count=0,
    personalization_q="Q11",  # 개인화 Q 코드
)
```

---

## 11. 향후 확장 포인트

### 11.1 추가 Q 구현

현재 8개 우선순위 Q만 구현되었으며, 나머지 12개는 `NOT_IMPLEMENTED` 반환:

- Q2, Q4, Q7, Q8, Q10, Q12, Q13, Q15, Q16, Q17, Q18, Q19

### 11.2 부서 비교 (Q5)

현재 Q5의 `target_dept_id`는 `None`으로 고정:

```python
# TODO: 부서 비교(Q5) 시 dept 파싱 필요
target_dept_id=None,
```

### 11.3 캐싱

자주 조회되는 facts에 대한 캐싱 전략 필요:
- 연차/포인트 잔액은 일정 시간 캐시 가능
- 실시간성이 중요한 데이터는 캐시 불가

---

## 부록: 파일 구조

```
app/
├── clients/
│   └── personalization_client.py   # Backend API 클라이언트
├── models/
│   └── personalization.py          # 데이터 모델, 메타데이터
├── services/
│   ├── chat_service.py             # 메인 서비스 (통합점)
│   ├── personalization_mapper.py   # SubIntentId → Q 매핑
│   └── answer_generator.py         # Facts → 답변 생성
└── ...

tests/unit/
├── test_personalization.py
├── test_personalization_mapper.py
└── test_personalization_integration.py
```
