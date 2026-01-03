# CTRL+F AI 의도/세부인텐트 기능정의서

> **버전**: v2.0
> **작성일**: 2025-12-20
> **적용 Phase**: Phase 10 (기본) + Phase 21-23 (Tier-0 라우터)

---

## 1. 개요

### 1.1 인텐트 시스템 아키텍처

CTRL+F AI는 **2계층 인텐트 분류 시스템**을 채택합니다:

```
┌─────────────────────────────────────────────────────────────────┐
│                      사용자 질문 입력                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  [Layer 1] RuleRouter (규칙 기반)                               │
│  ─────────────────────────────────────────────────────────────  │
│  • 키워드 매칭으로 1차 분류                                      │
│  • 신뢰도(confidence) 산출                                      │
│  • 애매한 경계 감지 → 되묻기(clarify)                           │
│  • 치명 액션 감지 → 확인 게이트(confirm)                        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                    confidence < 0.85?
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  [Layer 2] LLMRouter (LLM 기반)                                 │
│  ─────────────────────────────────────────────────────────────  │
│  • Few-shot 프롬프트로 정밀 분류                                │
│  • 자연어 이해 기반 세부 의도 추출                              │
│  • 구조화된 JSON 응답                                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    RouterResult 반환                            │
│  ─────────────────────────────────────────────────────────────  │
│  • tier0_intent: 주 의도                                       │
│  • sub_intent_id: 세부 의도                                    │
│  • domain: 도메인                                              │
│  • route_type: 처리 경로                                       │
│  • confidence: 신뢰도                                          │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 핵심 설계 원칙

| 원칙 | 설명 |
|------|------|
| **신뢰도 우선** | confidence ≥ 0.85이면 LLMRouter 스킵 (비용/지연 최적화) |
| **명확성 우선** | 애매한 입력은 추론하지 않고 사용자에게 되묻기 |
| **안전성 우선** | 치명 액션(퀴즈 등)은 반드시 사용자 확인 후 실행 |
| **역할 인식** | 사용자 역할(직원/관리자)에 따라 라우팅 차별화 |

---

## 2. Tier-0 인텐트 (주 의도)

### 2.1 인텐트 정의 표

| ID | 인텐트 | 영문 코드 | 설명 | 예시 질문 |
|----|--------|-----------|------|----------|
| T0-01 | **정책 Q&A** | `POLICY_QA` | 사규/규정/정책/지침에 대한 질문 | "연차 이월 규정 알려줘", "보안정책 위반시 제재는?" |
| T0-02 | **교육 Q&A** | `EDUCATION_QA` | 교육 내용/커리큘럼/강의에 대한 질문 | "4대 교육 내용이 뭐야?", "보안교육 강의 요약해줘" |
| T0-03 | **백엔드 조회** | `BACKEND_STATUS` | 개인화된 현황 조회 (연차/근태/교육이수 등) | "내 연차 며칠 남았어?", "교육 이수율 확인해줘" |
| T0-04 | **일반 잡담** | `GENERAL_CHAT` | 업무 외 일상 대화, 인사, Small talk | "안녕하세요", "오늘 날씨 어때?" |
| T0-05 | **시스템 도움말** | `SYSTEM_HELP` | 시스템 사용법/메뉴/기능 안내 | "이 시스템 어떻게 사용해?", "메뉴 설명해줘" |
| T0-06 | **분류 불가** | `UNKNOWN` | 어떤 카테고리에도 명확히 해당하지 않음 | (의미 불명확한 입력) |

### 2.2 레거시 인텐트 (Phase 10 호환)

> Phase 21 이전 시스템과의 호환을 위해 유지되는 인텐트 타입입니다.

| ID | 인텐트 | 영문 코드 | 매핑 Tier-0 | 비고 |
|----|--------|-----------|-------------|------|
| L-01 | 사고 신고 | `INCIDENT_REPORT` | `BACKEND_STATUS` | 신고 플로우 진입점 |
| L-02 | 사고 문의 | `INCIDENT_QA` | `POLICY_QA` | RAG 기반 사고 관련 문의 |
| L-03 | 교육 현황 | `EDU_STATUS` | `BACKEND_STATUS` | 이수/진도 조회 |

---

## 3. 세부 인텐트 (Sub-Intent)

### 3.1 세부 인텐트 정의 표

| ID | 세부 인텐트 | 영문 코드 | 상위 인텐트 | 치명 여부 | 설명 |
|----|------------|-----------|-------------|-----------|------|
| **퀴즈 관련** |||||
| S-01 | 퀴즈 시작 | `QUIZ_START` | `BACKEND_STATUS` | ⚠️ **치명** | 새 퀴즈 세션 생성 및 시작 |
| S-02 | 퀴즈 제출 | `QUIZ_SUBMIT` | `BACKEND_STATUS` | ⚠️ **치명** | 답안 제출 및 채점 요청 |
| S-03 | 퀴즈 생성 | `QUIZ_GENERATION` | `BACKEND_STATUS` | ⚠️ **치명** | 퀴즈 문항 자동 생성 |
| **교육 관련** |||||
| S-04 | 교육 이수현황 조회 | `EDU_STATUS_CHECK` | `BACKEND_STATUS` | - | 교육 수료율/진도 조회 |
| **HR/인사 관련** |||||
| S-05 | 연차/휴가 조회 | `HR_LEAVE_CHECK` | `BACKEND_STATUS` | - | 연차 잔여일수 조회 |
| S-06 | 근태 현황 조회 | `HR_ATTENDANCE_CHECK` | `BACKEND_STATUS` | - | 출퇴근/근태 기록 조회 |
| S-07 | 복지 포인트 조회 | `HR_WELFARE_CHECK` | `BACKEND_STATUS` | - | 복지포인트/혜택 조회 |

### 3.2 치명 액션 (Critical Actions)

치명 액션은 **되돌리기 불가능한 작업**으로, 반드시 사용자 확인을 거쳐야 합니다.

```python
CRITICAL_ACTION_SUB_INTENTS = frozenset([
    "QUIZ_START",       # 퀴즈 시작 - 세션 생성
    "QUIZ_SUBMIT",      # 퀴즈 제출 - 채점 완료
    "QUIZ_GENERATION",  # 퀴즈 생성 - 문항 DB 저장
])
```

| 세부 인텐트 | 확인 프롬프트 예시 |
|------------|-------------------|
| `QUIZ_START` | "퀴즈를 시작하시겠습니까? 시작하면 시간이 측정됩니다." |
| `QUIZ_SUBMIT` | "답안을 제출하시겠습니까? 제출 후에는 수정할 수 없습니다." |
| `QUIZ_GENERATION` | "새 퀴즈 문항을 생성하시겠습니까?" |

---

## 4. 도메인 분류

### 4.1 도메인 정의 표

| ID | 도메인 | 영문 코드 | 설명 | 관련 인텐트 |
|----|--------|-----------|------|-------------|
| D-01 | **정책/사규** | `POLICY` | 사내규정, 보안정책, 업무지침 | `POLICY_QA` |
| D-02 | **교육** | `EDU` | 법정교육, 직무교육, 4대 교육 | `EDUCATION_QA`, `BACKEND_STATUS` |
| D-03 | **인사** | `HR` | 연차, 근태, 급여, 복지 | `BACKEND_STATUS` |
| D-04 | **퀴즈** | `QUIZ` | 퀴즈/시험 세션 관리 | `BACKEND_STATUS` |
| D-05 | **일반** | `GENERAL` | 기타 일반 대화 | `GENERAL_CHAT`, `SYSTEM_HELP`, `UNKNOWN` |

### 4.2 도메인-인텐트 매핑

```
POLICY  ─┬─ POLICY_QA
         └─ (레거시) INCIDENT_QA

EDU     ─┬─ EDUCATION_QA
         └─ BACKEND_STATUS (교육 이수현황)

HR      ─── BACKEND_STATUS (연차/근태/복지)

QUIZ    ─── BACKEND_STATUS (퀴즈 시작/제출/생성)

GENERAL ─┬─ GENERAL_CHAT
         ├─ SYSTEM_HELP
         └─ UNKNOWN
```

---

## 5. 라우트 타입

### 5.1 라우트 정의 표

| ID | 라우트 | 영문 코드 | 백엔드 처리 | 사용 시점 |
|----|--------|-----------|-------------|----------|
| R-01 | **RAG 내부** | `RAG_INTERNAL` | RAG 검색 → LLM 생성 | 정책/교육 내용 질문 |
| R-02 | **백엔드 API** | `BACKEND_API` | 백엔드 API 직접 호출 | 개인화 조회 (연차/근태/이수) |
| R-03 | **LLM 전용** | `LLM_ONLY` | RAG 없이 LLM만 사용 | 일반 잡담 |
| R-04 | **시스템 도움말** | `ROUTE_SYSTEM_HELP` | 도움말 전용 로직 | 시스템 사용법 |
| R-05 | **분류 불가** | `ROUTE_UNKNOWN` | 폴백 응답 | 분류 실패 |

### 5.2 레거시 라우트 (Phase 10 호환)

| ID | 라우트 | 영문 코드 | 설명 |
|----|--------|-----------|------|
| LR-01 | 혼합 (API+RAG) | `MIXED_BACKEND_RAG` | 백엔드 데이터 + RAG 조합 (관리자 전용) |
| LR-02 | 사고 처리 | `INCIDENT` | 사고 신고 전용 경로 |
| LR-03 | 훈련/퀴즈 | `TRAINING` | 퀴즈/영상 생성 경로 |
| LR-04 | 폴백 | `FALLBACK` | 설정 미비 시 |
| LR-05 | 에러 | `ERROR` | 예외 발생 시 |

---

## 6. 인텐트-라우트 매핑 정책

### 6.1 기본 라우팅 정책

```python
TIER0_ROUTING_POLICY = {
    # 인텐트           → (라우트,           기본 도메인)
    "POLICY_QA":       ("RAG_INTERNAL",     "POLICY"),
    "EDUCATION_QA":    ("RAG_INTERNAL",     "EDU"),
    "BACKEND_STATUS":  ("BACKEND_API",      "HR"),      # 도메인은 컨텍스트에 따라 가변
    "GENERAL_CHAT":    ("LLM_ONLY",         "GENERAL"),
    "SYSTEM_HELP":     ("ROUTE_SYSTEM_HELP", "GENERAL"),
    "UNKNOWN":         ("ROUTE_UNKNOWN",    "GENERAL"),
}
```

### 6.2 상세 매핑 표

| Tier-0 인텐트 | 세부 인텐트 | 도메인 | 라우트 | 신뢰도 |
|--------------|------------|--------|--------|--------|
| `POLICY_QA` | - | `POLICY` | `RAG_INTERNAL` | 0.85 |
| `EDUCATION_QA` | - | `EDU` | `RAG_INTERNAL` | 0.85 |
| `BACKEND_STATUS` | `EDU_STATUS_CHECK` | `EDU` | `BACKEND_API` | 0.90 |
| `BACKEND_STATUS` | `HR_LEAVE_CHECK` | `HR` | `BACKEND_API` | 0.90 |
| `BACKEND_STATUS` | `HR_ATTENDANCE_CHECK` | `HR` | `BACKEND_API` | 0.90 |
| `BACKEND_STATUS` | `HR_WELFARE_CHECK` | `HR` | `BACKEND_API` | 0.90 |
| `BACKEND_STATUS` | `QUIZ_START` | `QUIZ` | `BACKEND_API` | 0.95 |
| `BACKEND_STATUS` | `QUIZ_SUBMIT` | `QUIZ` | `BACKEND_API` | 0.95 |
| `BACKEND_STATUS` | `QUIZ_GENERATION` | `QUIZ` | `BACKEND_API` | 0.95 |
| `GENERAL_CHAT` | - | `GENERAL` | `LLM_ONLY` | 0.80 |
| `SYSTEM_HELP` | - | `GENERAL` | `ROUTE_SYSTEM_HELP` | 0.90 |
| `UNKNOWN` | - | `GENERAL` | `ROUTE_UNKNOWN` | 0.30 |

---

## 7. 키워드 패턴 매칭

### 7.1 정책/규정 키워드

```python
POLICY_KEYWORDS = {
    # 규정 관련
    "규정", "사규", "정책", "규칙", "지침", "매뉴얼", "가이드",
    "절차", "프로세스", "승인", "결재",

    # 보안 관련
    "보안정책", "개인정보보호", "정보보안",

    # 위반/제재 관련
    "허용", "금지", "위반", "제재", "징계",
}

LEAVE_POLICY_KEYWORDS = {
    # 연차 규정 (내용 질문)
    "연차규정", "휴가규정", "연차제도",
    "연차 이월", "휴가 이월",
    "연차가 뭐", "연차란", "연차 어떻게 계산",
}
```

**매칭 예시:**
- "보안정책 위반시 제재가 뭐야?" → `POLICY_QA`
- "연차 이월 규정 알려줘" → `POLICY_QA`

### 7.2 교육 관련 키워드

```python
EDU_CONTENT_KEYWORDS = {
    # 교육 내용
    "교육내용", "교육자료", "교육규정", "학습내용",
    "강의내용", "교육과정", "커리큘럼",

    # 법정 교육
    "4대교육", "법정교육", "의무교육",
    "성희롱예방", "산업안전", "직장내괴롭힘", "장애인인식개선",
}

EDU_STATUS_KEYWORDS = {
    # 이수 현황
    "수료", "이수", "미이수", "수료율", "이수율",
    "진도", "진행률", "시청률", "완료율",

    # 개인화 조회
    "내 교육", "교육현황", "수강현황", "남은 교육",
}
```

**매칭 예시:**
- "4대교육 내용이 뭐야?" → `EDUCATION_QA`
- "내 교육 이수율 확인해줘" → `BACKEND_STATUS` + `EDU_STATUS_CHECK`

### 7.3 HR/개인화 키워드

```python
HR_PERSONAL_KEYWORDS = {
    # 연차/휴가 조회
    "내 연차", "연차 잔여", "연차 남은", "연차 몇일",
    "휴가 잔여", "내 휴가", "휴가 남은",

    # 급여/복지
    "급여", "월급", "내 급여",
    "복지", "복지포인트", "내 포인트",

    # 근태
    "근태", "내 근태", "출퇴근",

    # 일반 개인화
    "내 정보", "내 현황",
}
```

**매칭 예시:**
- "내 연차 며칠 남았어?" → `BACKEND_STATUS` + `HR_LEAVE_CHECK`
- "이번 달 근태 현황 보여줘" → `BACKEND_STATUS` + `HR_ATTENDANCE_CHECK`

### 7.4 퀴즈 관련 키워드

```python
QUIZ_START_KEYWORDS = {
    "퀴즈 시작", "퀴즈 시작해", "시험 시작",
    "퀴즈를 시작", "퀴즈 풀", "테스트 시작",
}

QUIZ_SUBMIT_KEYWORDS = {
    "퀴즈 제출", "답안 제출", "정답 제출",
    "채점해", "채점 해", "점수 확인",
    "제출할게", "제출하겠",
}

QUIZ_GENERATION_KEYWORDS = {
    "퀴즈 생성", "문제 생성", "문항 생성",
    "퀴즈 만들", "문제 만들", "퀴즈 출제",
}
```

**매칭 예시:**
- "퀴즈 시작해줘" → `BACKEND_STATUS` + `QUIZ_START` ⚠️ 확인 필요
- "답안 제출할게" → `BACKEND_STATUS` + `QUIZ_SUBMIT` ⚠️ 확인 필요

### 7.5 시스템 도움말 키워드

```python
SYSTEM_HELP_KEYWORDS = {
    "사용법", "메뉴", "화면", "버튼", "기능",
    "어떻게 사용", "찾기", "안내", "도움말",
    "이 시스템", "이거 뭐야",
}
```

### 7.6 일반 잡담 키워드

```python
GENERAL_CHAT_KEYWORDS = {
    # 인사
    "안녕", "안녕하세요", "반갑",

    # 감탄/이모티콘성
    "ㅎㅎ", "ㅋㅋ", "ㅠㅠ", "ㅜㅜ",

    # 일상
    "날씨", "농담", "심심",

    # 감사
    "감사", "고마워", "고맙습니다",
}
```

### 7.7 사고/신고 키워드

```python
INCIDENT_REPORT_KEYWORDS = {
    # 신고 액션
    "신고", "신고하", "보고", "보고하",
    "접수", "등록", "제보",
}

INCIDENT_QA_KEYWORDS = {
    # 사고 유형
    "사고", "유출", "침해", "해킹", "보안사고",
    "정보유출", "랜섬웨어", "악성코드", "피싱", "스팸",

    # 기타
    "분실", "도난", "위반",
}
```

---

## 8. 경계 조건 및 되묻기 (Clarification)

### 8.1 애매한 경계 감지

특정 질문은 **교육 내용** vs **교육 현황**, **정책 규정** vs **개인 현황** 경계에서 모호합니다.

| 경계 ID | 경계 설명 | 애매한 입력 예시 | 명확한 입력 예시 |
|---------|----------|-----------------|-----------------|
| **A** | 교육 내용 vs 이수현황 | "교육 알려줘", "학습 확인해줘" | 내용: "4대교육 내용이 뭐야"<br>현황: "교육 이수율 확인해줘" |
| **B** | 정책 규정 vs HR 개인화 | "연차 알려줘", "휴가 확인해줘" | 규정: "연차 이월 규정 알려줘"<br>조회: "내 연차 며칠 남았어" |

### 8.2 되묻기 질문 표

| 경계 | ClarifyGroup | 되묻기 질문 |
|------|--------------|-------------|
| A | `EDU` | "교육 **내용**(예: 4대 교육이 뭔지)을 알고 싶으신가요, 아니면 **이수 현황**(예: 내 수료율)을 확인하고 싶으신가요?" |
| B | `POLICY` | "연차/휴가 **규정**(예: 이월 규칙)을 알고 싶으신가요, 아니면 **내 잔여 연차**를 확인하고 싶으신가요?" |

### 8.3 되묻기 후 키워드 매핑

사용자 응답(20자 이하)에서 키워드를 추출하여 의도를 결정합니다.

```python
CLARIFY_KEYWORD_MAPPING = {
    "EDU": {
        # 백엔드 조회 키워드
        "이수": "BACKEND_STATUS",
        "진도": "BACKEND_STATUS",
        "조회": "BACKEND_STATUS",
        "현황": "BACKEND_STATUS",

        # RAG 검색 키워드
        "내용": "RAG_INTERNAL",
        "설명": "RAG_INTERNAL",
        "요약": "RAG_INTERNAL",
        "뭔지": "RAG_INTERNAL",
    },

    "POLICY": {
        # 백엔드 조회 키워드
        "조회": "BACKEND_STATUS",
        "현황": "BACKEND_STATUS",
        "남은": "BACKEND_STATUS",
        "잔여": "BACKEND_STATUS",

        # RAG 검색 키워드
        "내용": "RAG_INTERNAL",
        "규정": "RAG_INTERNAL",
        "규칙": "RAG_INTERNAL",
        "어떻게": "RAG_INTERNAL",
    },
}
```

---

## 9. 역할별 라우팅 규칙

### 9.1 사용자 역할 정의

| 역할 | 영문 코드 | 설명 |
|------|-----------|------|
| 일반 직원 | `EMPLOYEE` | 기본 사용자 |
| 관리자 | `ADMIN` | HR/보안/시스템 관리자 |
| 신고관리자 | `INCIDENT_MANAGER` | 사고 신고 처리 담당자 |

### 9.2 역할×인텐트×도메인 라우팅 매트릭스

| 역할 | 도메인 | 인텐트 | 라우트 |
|------|--------|--------|--------|
| **EMPLOYEE** | - | `INCIDENT_REPORT` | `BACKEND_API` |
| **EMPLOYEE** | - | `EDU_STATUS` | `BACKEND_API` |
| **EMPLOYEE** | - | (기타) | `RAG_INTERNAL` |
| **ADMIN** | `INCIDENT` | - | `MIXED_BACKEND_RAG` |
| **ADMIN** | `EDU` | `EDU_STATUS` | `MIXED_BACKEND_RAG` |
| **ADMIN** | `EDU` | `EDUCATION_QA` | `RAG_INTERNAL` |
| **ADMIN** | `POLICY` | - | `RAG_INTERNAL` |
| **INCIDENT_MANAGER** | `INCIDENT` | - | `MIXED_BACKEND_RAG` |
| **INCIDENT_MANAGER** | `POLICY`/`EDU` | - | `RAG_INTERNAL` |

---

## 10. 신뢰도 및 임계값

### 10.1 신뢰도 기준표

| 분류 케이스 | 신뢰도 | LLMRouter 호출 |
|------------|--------|----------------|
| 치명 액션 (퀴즈 관련) | 0.95 | ❌ 스킵 |
| HR 개인화 명확 매칭 | 0.90 | ❌ 스킵 |
| 교육 현황 명확 매칭 | 0.90 | ❌ 스킵 |
| 시스템 도움말 매칭 | 0.90 | ❌ 스킵 |
| 정책/교육 내용 매칭 | 0.85 | ❌ 스킵 |
| 일반 잡담 매칭 | 0.80 | ✅ 호출 (임계값 미달) |
| 키워드 미매칭 | 0.30 | ✅ 호출 |

### 10.2 임계값 설정

```python
# RuleRouter 신뢰도 임계값
RULE_ROUTER_CONFIDENCE_THRESHOLD = 0.85

# 임계값 이상이면 LLMRouter 스킵
# 임계값 미만이면 LLMRouter로 정밀 분류
```

---

## 11. 분류 플로우 다이어그램

### 11.1 전체 플로우

```
┌─────────────────────────────────────────────────────────────────┐
│                       사용자 입력                               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    [Phase 4-1] PII 마스킹                       │
│                      (INPUT 단계)                               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│               RouterOrchestrator.route()                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─── Step 0: 대기 액션 확인 ───┐                               │
│  │  CLARIFY 대기 → ClarifyAnswerHandler                        │
│  │  CONFIRM 대기 → 확인 응답 처리                               │
│  └──────────────────────────────┘                               │
│                    ↓                                            │
│  ┌─── Step 1: RuleRouter ───────┐                               │
│  │  키워드 매칭                                                 │
│  │  경계 감지 (needs_clarify)                                   │
│  │  치명 액션 감지                                              │
│  │  신뢰도 산출                                                 │
│  └──────────────────────────────┘                               │
│                    ↓                                            │
│            needs_clarify?                                       │
│               ↓ YES                                             │
│          되묻기 응답 반환 ─────────────────────────────→ [종료] │
│               ↓ NO                                              │
│                                                                 │
│          confidence ≥ 0.85?                                     │
│               ↓ YES                                             │
│          최종 결과 반환 ───────────────────────────────→ [종료] │
│               ↓ NO                                              │
│                                                                 │
│  ┌─── Step 2: LLMRouter ────────┐                               │
│  │  Few-shot 프롬프트                                           │
│  │  JSON 응답 파싱                                              │
│  │  유효성 검증                                                 │
│  └──────────────────────────────┘                               │
│                    ↓                                            │
│       requires_confirmation?                                    │
│               ↓ YES                                             │
│          확인 프롬프트 반환 ───────────────────────────→ [종료] │
│               ↓ NO                                              │
│                                                                 │
│          RouterResult 반환 ────────────────────────────→        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     라우팅 실행                                 │
├─────────────────────────────────────────────────────────────────┤
│  RAG_INTERNAL      → RagService.search() + LLM 생성            │
│  BACKEND_API       → BackendClient.call()                      │
│  LLM_ONLY          → LLMService.generate()                     │
│  ROUTE_SYSTEM_HELP → 도움말 로직                               │
│  ROUTE_UNKNOWN     → 폴백 응답                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    [Phase 4-1] PII 마스킹                       │
│                      (OUTPUT 단계)                              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      ChatResponse 반환                          │
└─────────────────────────────────────────────────────────────────┘
```

### 11.2 RuleRouter 결정 트리

```
질문 분석
    │
    ├─► 애매한 경계 감지?
    │       ├─ 교육 경계 (A) → needs_clarify=true, group=EDU
    │       └─ 정책 경계 (B) → needs_clarify=true, group=POLICY
    │
    ├─► 퀴즈 시작 키워드?
    │       └─ YES → sub_intent=QUIZ_START, requires_confirmation=true
    │
    ├─► 퀴즈 제출 키워드?
    │       └─ YES → sub_intent=QUIZ_SUBMIT, requires_confirmation=true
    │
    ├─► 퀴즈 생성 키워드?
    │       └─ YES → sub_intent=QUIZ_GENERATION, requires_confirmation=true
    │
    ├─► HR 개인화 키워드?
    │       └─ YES → tier0=BACKEND_STATUS, confidence=0.9
    │
    ├─► 교육 현황 키워드?
    │       └─ YES → tier0=BACKEND_STATUS, sub=EDU_STATUS_CHECK, conf=0.9
    │
    ├─► 교육 내용 키워드?
    │       └─ YES → tier0=EDUCATION_QA, confidence=0.85
    │
    ├─► 정책/규정 키워드?
    │       └─ YES → tier0=POLICY_QA, confidence=0.85
    │
    ├─► 연차 규정 키워드?
    │       └─ YES → tier0=POLICY_QA, confidence=0.85
    │
    ├─► 시스템 도움말 키워드?
    │       └─ YES → tier0=SYSTEM_HELP, confidence=0.9
    │
    ├─► 일반 잡담 키워드?
    │       └─ YES → tier0=GENERAL_CHAT, confidence=0.8
    │
    └─► 매칭 없음
            └─ tier0=UNKNOWN, confidence=0.3
```

---

## 12. 응답 구조

### 12.1 RouterResult 스키마

```python
class RouterResult:
    # 기본 분류
    tier0_intent: Tier0Intent       # 주 의도 (6개 중 하나)
    domain: RouterDomain            # 도메인 (5개 중 하나)
    route_type: RouterRouteType     # 라우트 (5개 중 하나)
    sub_intent_id: str              # 세부 의도 ID (선택)

    # 신뢰도
    confidence: float               # 0.0 ~ 1.0

    # 되묻기
    needs_clarify: bool             # 되묻기 필요 여부
    clarify_question: str           # 되묻기 질문

    # 확인 게이트
    requires_confirmation: bool     # 확인 필요 여부
    confirmation_prompt: str        # 확인 프롬프트

    # 디버그
    debug: RouterDebugInfo          # 분류 과정 추적 정보
```

### 12.2 ChatResponse.meta 필드

```python
class ChatAnswerMeta:
    user_role: str              # 사용자 역할
    used_model: str             # 사용된 LLM 모델
    route: str                  # 라우팅 경로
    intent: str                 # 분류된 의도
    domain: str                 # 도메인
    masked: bool                # PII 마스킹 여부
    rag_used: bool              # RAG 사용 여부
    rag_source_count: int       # RAG 검색 결과 수
    latency_ms: int             # 전체 응답 시간
    rag_latency_ms: int         # RAG 지연시간
    llm_latency_ms: int         # LLM 지연시간
    backend_latency_ms: int     # 백엔드 지연시간
    error_type: str             # 에러 타입 (있는 경우)
```

---

## 13. 테스트 케이스 요약

### 13.1 인텐트 분류 테스트

| 테스트 ID | 입력 | 예상 인텐트 | 예상 라우트 |
|-----------|------|------------|------------|
| TC-01 | "결재 승인 관련 문의" | `POLICY_QA` | `RAG_INTERNAL` |
| TC-02 | "보안 사고 신고하려고 합니다" | `INCIDENT_REPORT` | `BACKEND_API` |
| TC-03 | "보안교육 강의 내용 알려줘" | `EDUCATION_QA` | `RAG_INTERNAL` |
| TC-04 | "안녕 ㅎㅎ" | `GENERAL_CHAT` | `LLM_ONLY` |
| TC-05 | "내 연차 며칠 남았어?" | `BACKEND_STATUS` | `BACKEND_API` |
| TC-06 | "교육 이수율 확인해줘" | `BACKEND_STATUS` | `BACKEND_API` |

### 13.2 경계 조건 테스트

| 테스트 ID | 입력 | 예상 결과 |
|-----------|------|----------|
| BC-01 | "교육 알려줘" | `needs_clarify=true`, group=`EDU` |
| BC-02 | "연차 알려줘" | `needs_clarify=true`, group=`POLICY` |
| BC-03 | "4대교육 내용이 뭐야" | `needs_clarify=false`, intent=`EDUCATION_QA` |
| BC-04 | "내 연차 잔여일수" | `needs_clarify=false`, intent=`BACKEND_STATUS` |

### 13.3 치명 액션 테스트

| 테스트 ID | 입력 | 예상 결과 |
|-----------|------|----------|
| CA-01 | "퀴즈 시작해줘" | `requires_confirmation=true`, sub=`QUIZ_START` |
| CA-02 | "답안 제출할게" | `requires_confirmation=true`, sub=`QUIZ_SUBMIT` |
| CA-03 | "퀴즈 문제 만들어줘" | `requires_confirmation=true`, sub=`QUIZ_GENERATION` |

---

## 14. 관련 소스 코드 위치

| 컴포넌트 | 파일 경로 |
|----------|----------|
| 인텐트/라우트 모델 | `app/models/intent.py` |
| Tier-0 라우터 모델 | `app/models/router_types.py` |
| IntentService (레거시) | `app/services/intent_service.py` |
| RuleRouter | `app/services/rule_router.py` |
| LLMRouter | `app/services/llm_router.py` |
| RouterOrchestrator | `app/services/router_orchestrator.py` |
| ChatService 통합 | `app/services/chat_service.py` |
| 라우터 설정 | `app/core/config.py` |
| IntentService 테스트 | `tests/test_intent_and_pii.py` |
| 라우터 테스트 | `tests/test_router_phase21.py` |

---

## 부록 A: 전체 Enum 상수 정리

### A.1 Tier0Intent (6개)

```
POLICY_QA, EDUCATION_QA, BACKEND_STATUS, GENERAL_CHAT, SYSTEM_HELP, UNKNOWN
```

### A.2 RouterDomain (5개)

```
POLICY, EDU, HR, QUIZ, GENERAL
```

### A.3 RouterRouteType (5개)

```
RAG_INTERNAL, BACKEND_API, LLM_ONLY, ROUTE_SYSTEM_HELP, ROUTE_UNKNOWN
```

### A.4 SubIntentId (7개)

```
QUIZ_START, QUIZ_SUBMIT, QUIZ_GENERATION,
EDU_STATUS_CHECK, HR_LEAVE_CHECK, HR_ATTENDANCE_CHECK, HR_WELFARE_CHECK
```

### A.5 ClarifyGroup (5개)

```
EDU, POLICY, PROFILE, INCIDENT, UNKNOWN
```

---

## 부록 B: 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| v1.0 | 2025-06-XX | Phase 10 기본 인텐트 시스템 |
| v2.0 | 2025-12-20 | Phase 21-23 Tier-0 라우터 반영, 되묻기/확인 게이트 추가 |
