# Phase 21 구현 보고서: Intent Router 시스템

**작성일**: 2025-12-16
**담당**: AI팀

---

## 1. 개요

Phase 21은 기업 내부 정보보호 AI 어시스턴트의 **의도 분류 정확도 및 안전성**을 강화하기 위한 Intent Router 시스템을 구현합니다.

### 목표
- Tier-0 Intent 기반 라우팅 체계 구축
- 애매한 질문에 대한 되묻기(Clarification) 기능
- 치명 액션(퀴즈 3종)에 대한 확인 게이트(Confirmation Gate)
- Rule Router → LLM Router 2단계 분류 체계

---

## 2. 아키텍처

### 2.1 시스템 흐름

```
사용자 질문
    ↓
┌─────────────────┐
│   Rule Router   │ ← 키워드 기반 1차 분류
└────────┬────────┘
         ↓
    confidence >= 0.85?
         │
    Yes ─┴─ No
     ↓       ↓
  최종결과  ┌─────────────────┐
           │   LLM Router    │ ← LLM 기반 2차 분류
           └────────┬────────┘
                    ↓
              needs_clarify?
                    │
               Yes ─┴─ No
                ↓       ↓
           되묻기    requires_confirmation?
           응답반환        │
                     Yes ─┴─ No
                      ↓       ↓
                   확인게이트  최종라우팅
                   응답반환    실행
```

### 2.2 Tier-0 Intent (6개 고정)

| Intent | 설명 | RouteType |
|--------|------|-----------|
| `POLICY_QA` | 사규/규정/정책 관련 Q&A | RAG_INTERNAL |
| `EDUCATION_QA` | 교육 내용/규정 관련 질문 | RAG_INTERNAL |
| `BACKEND_STATUS` | HR/근태/복지/연차/교육현황 개인화 조회 | BACKEND_API |
| `GENERAL_CHAT` | 일반 잡담, Small talk | LLM_ONLY |
| `SYSTEM_HELP` | 시스템 사용법, 메뉴 설명 | ROUTE_SYSTEM_HELP |
| `UNKNOWN` | 분류 불가 | ROUTE_UNKNOWN |

### 2.3 Domain (5개)

| Domain | 설명 |
|--------|------|
| `POLICY` | 사규/보안 정책 |
| `EDU` | 4대 교육/직무 교육 |
| `HR` | 인사/근태/복지/연차/급여 |
| `QUIZ` | 퀴즈/시험 관련 |
| `GENERAL` | 일반 |

---

## 3. 구현 상세

### 3.1 RouterResult JSON 스키마

```json
{
  "tier0_intent": "POLICY_QA | EDUCATION_QA | BACKEND_STATUS | GENERAL_CHAT | SYSTEM_HELP | UNKNOWN",
  "domain": "POLICY | EDU | HR | QUIZ | GENERAL",
  "route_type": "RAG_INTERNAL | BACKEND_API | LLM_ONLY | ROUTE_SYSTEM_HELP | ROUTE_UNKNOWN",
  "sub_intent_id": "",
  "confidence": 0.0,
  "needs_clarify": false,
  "clarify_question": "",
  "requires_confirmation": false,
  "confirmation_prompt": "",
  "debug": {
    "rule_hits": [],
    "keywords": []
  }
}
```

### 3.2 되묻기 경계 (Clarification Boundaries)

#### 경계 A: 교육 내용 vs 이수현황

| 상황 | 분류 |
|------|------|
| "교육 알려줘" | 애매함 → 되묻기 |
| "4대교육 내용이 뭐야" | 명확 → EDUCATION_QA |
| "내 수료율 확인해줘" | 명확 → BACKEND_STATUS |

**되묻기 템플릿:**
- "교육 내용 설명이 필요하신가요, 아니면 내 이수현황/진도 조회가 필요하신가요?"

#### 경계 B: 규정 질문 vs HR 개인화

| 상황 | 분류 |
|------|------|
| "연차 알려줘" | 애매함 → 되묻기 |
| "연차 이월 규정 알려줘" | 명확 → POLICY_QA |
| "내 연차 며칠 남았어?" | 명확 → BACKEND_STATUS |

**되묻기 템플릿:**
- "회사 규정(정책) 설명을 원하시나요, 아니면 내 HR 정보(연차/근태/복지) 조회를 원하시나요?"

### 3.3 확인 게이트 (Confirmation Gate)

치명 액션(퀴즈 3종)에 대해 사용자 확인을 요구합니다:

| Sub Intent | 확인 프롬프트 |
|------------|--------------|
| `QUIZ_START` | "퀴즈를 지금 시작할까요? (예/아니오)" |
| `QUIZ_SUBMIT` | "답안을 제출하고 채점할까요? (예/아니오)" |
| `QUIZ_GENERATION` | "문항을 생성해서 저장할까요? (예/아니오)" |

### 3.4 유효성 검증 규칙

1. **BACKEND_STATUS + 빈 sub_intent_id** → `needs_clarify=true` 강제
2. **치명 액션** → `requires_confirmation=true` 강제
3. **route_type과 tier0_intent 불일치** → 자동 보정

---

## 4. 파일 변경 요약

### 신규 파일

| 파일 | 설명 |
|------|------|
| `app/models/router_types.py` | Tier0Intent, Domain, RouteType enum + RouterResult 스키마 |
| `app/services/rule_router.py` | 키워드 기반 1차 분류 |
| `app/services/llm_router.py` | LLM 기반 2차 분류 + JSON 파싱 |
| `app/services/router_orchestrator.py` | 통합 오케스트레이터 + 대기 상태 관리 |
| `tests/test_router_phase21.py` | Phase 21 테스트 (32개) |

### 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/core/config.py` | Phase 21 설정 추가 |

---

## 5. 환경변수 요약

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ROUTER_USE_LLM` | `true` | LLM Router 사용 여부 |
| `ROUTER_RULE_CONFIDENCE_THRESHOLD` | `0.85` | Rule Router만 사용할 신뢰도 임계값 |
| `ROUTER_PENDING_TIMEOUT_SECONDS` | `300` | 되묻기/확인 대기 만료 시간 (초) |

---

## 6. 테스트 결과

### Phase 21 테스트

```
tests/test_router_phase21.py - 32 passed (1.19s)
```

#### 테스트 카테고리

| 카테고리 | 테스트 수 | 설명 |
|---------|----------|------|
| 애매한 경계 감지 | 6 | 경계 A, B 되묻기 테스트 |
| 치명 액션 | 3 | 퀴즈 시작/제출/생성 확인 게이트 |
| 기본 분류 | 4 | POLICY_QA, GENERAL_CHAT, SYSTEM_HELP, UNKNOWN |
| LLM Router | 5 | JSON 파싱, 유효성 검증 |
| Orchestrator | 6 | 통합 플로우, 확인 수락/거부 |
| PendingActionStore | 3 | 상태 저장/조회/삭제 |
| 예시 입력 | 5 | prompt.txt 명시 예시 |

---

## 7. 예시 입력/출력

### 예시 1: 명확한 정책 질문

```
입력: "연차 이월 규정 알려줘"
출력:
{
  "tier0_intent": "POLICY_QA",
  "domain": "POLICY",
  "route_type": "RAG_INTERNAL",
  "confidence": 0.85,
  "needs_clarify": false,
  "requires_confirmation": false
}
```

### 예시 2: HR 개인화 조회

```
입력: "내 연차 며칠 남았어?"
출력:
{
  "tier0_intent": "BACKEND_STATUS",
  "domain": "HR",
  "route_type": "BACKEND_API",
  "sub_intent_id": "HR_LEAVE_CHECK",
  "confidence": 0.9,
  "needs_clarify": false
}
```

### 예시 3: 애매한 교육 질문

```
입력: "교육 알려줘"
출력:
{
  "tier0_intent": "UNKNOWN",
  "domain": "EDU",
  "route_type": "ROUTE_UNKNOWN",
  "confidence": 0.3,
  "needs_clarify": true,
  "clarify_question": "교육 내용 설명이 필요하신가요, 아니면 내 이수현황/진도 조회가 필요하신가요?"
}
```

### 예시 4: 퀴즈 시작 (치명 액션)

```
입력: "퀴즈 시작해줘"
출력:
{
  "tier0_intent": "BACKEND_STATUS",
  "domain": "QUIZ",
  "route_type": "BACKEND_API",
  "sub_intent_id": "QUIZ_START",
  "confidence": 0.95,
  "requires_confirmation": true,
  "confirmation_prompt": "퀴즈를 지금 시작할까요? (예/아니오)"
}
```

### 예시 5: 일반 잡담

```
입력: "안녕하세요"
출력:
{
  "tier0_intent": "GENERAL_CHAT",
  "domain": "GENERAL",
  "route_type": "LLM_ONLY",
  "confidence": 0.8,
  "needs_clarify": false
}
```

---

## 8. 사용 방법

### 기본 사용

```python
from app.services.router_orchestrator import RouterOrchestrator

orchestrator = RouterOrchestrator()

# 라우팅 요청
result = await orchestrator.route(
    user_query="연차 규정 알려줘",
    session_id="session-123",
)

if result.needs_user_response:
    # 되묻기 또는 확인 프롬프트 표시
    print(result.response_message)
else:
    # 라우팅 실행
    execute_route(result.router_result)
```

### 확인 응답 처리

```python
# 사용자가 '예' 응답
result = await orchestrator.handle_confirmation(
    session_id="session-123",
    confirmed=True,
)

if result.can_execute:
    execute_route(result.router_result)
```

---

## 9. 완료 조건 체크리스트

- [x] Tier-0 Intent 6개 정의 및 구현
- [x] Domain 5개 정의 및 구현
- [x] RouteType 5개 정의 및 구현
- [x] Rule Router 키워드 기반 1차 분류
- [x] LLM Router JSON 파싱 및 유효성 검증
- [x] 경계 A (교육 내용 vs 이수현황) 되묻기
- [x] 경계 B (규정 vs HR 개인화) 되묻기
- [x] 퀴즈 3종 확인 게이트
- [x] BACKEND_STATUS + 빈 sub_intent_id → clarify
- [x] 32개 테스트 통과

---

## 10. 후속 작업

- ChatService에 RouterOrchestrator 통합 (선택)
- Redis 기반 PendingActionStore 구현 (운영 환경)
- 영상 상태전이 서버검증 로직 구현 (Phase 22 예정)
