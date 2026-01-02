# Phase 10 개발 보고서: 역할×도메인×라우트 정책 구현

## 개요

Phase 10에서는 **역할(UserRole) × 도메인(Domain) × 라우트(RouteType)** 기반 라우팅 정책을 구현하였습니다. 사용자 역할에 따라 동일한 질문도 다른 경로로 처리되며, 역할별 가드레일을 통해 민감 정보 보호와 적절한 안내를 제공합니다.

## 구현 내용

### 1. Enum/모델 정의 (`app/models/intent.py`)

#### 새로 추가된 Enum
```python
class UserRole(str, Enum):
    EMPLOYEE = "EMPLOYEE"          # 일반 직원
    ADMIN = "ADMIN"                # 관리자
    INCIDENT_MANAGER = "INCIDENT_MANAGER"  # 신고관리자

class Domain(str, Enum):
    POLICY = "POLICY"     # 정책/규정
    INCIDENT = "INCIDENT" # 사고/위반
    EDU = "EDU"           # 교육
```

#### 새로운 RouteType (기존 레거시 호환 유지)
```python
class RouteType(str, Enum):
    # Phase 10 신규 값
    RAG_INTERNAL = "RAG_INTERNAL"
    LLM_ONLY = "LLM_ONLY"
    BACKEND_API = "BACKEND_API"
    MIXED_BACKEND_RAG = "MIXED_BACKEND_RAG"

    # 레거시 호환 (기존 코드 동작 보장)
    ROUTE_RAG_INTERNAL = "ROUTE_RAG_INTERNAL"
    ROUTE_LLM_ONLY = "ROUTE_LLM_ONLY"
    ...
```

#### IntentResult 확장
```python
class IntentResult(BaseModel):
    user_role: UserRole  # 신규 필드
    intent: IntentType
    domain: str
    route: RouteType
```

### 2. 역할×도메인 라우팅 룰 (`app/services/intent_service.py`)

| 역할 | 도메인 | 의도 | 라우트 |
|------|--------|------|--------|
| EMPLOYEE | POLICY | POLICY_QA | RAG_INTERNAL |
| EMPLOYEE | INCIDENT | INCIDENT_REPORT (신고) | BACKEND_API |
| EMPLOYEE | INCIDENT | INCIDENT_QA (문의) | RAG_INTERNAL |
| EMPLOYEE | EDU | EDU_STATUS (수료현황) | BACKEND_API |
| EMPLOYEE | EDU | EDUCATION_QA (내용) | RAG_INTERNAL |
| ADMIN | INCIDENT | * | MIXED_BACKEND_RAG |
| ADMIN | EDU | EDU_STATUS | MIXED_BACKEND_RAG |
| ADMIN | EDU | EDUCATION_QA | RAG_INTERNAL |
| INCIDENT_MANAGER | INCIDENT | * | MIXED_BACKEND_RAG |
| * | * | GENERAL_CHAT | LLM_ONLY |

### 3. 가드레일 서비스 (`app/services/guardrail_service.py`)

#### 직원(EMPLOYEE) 가드레일
- **INCIDENT_REPORT**: 개인정보 입력 금지 안내, 공식 신고 채널 유도
- **EDU_STATUS**: 본인 현황만 조회 가능, 타인 정보 조회 제한

#### 관리자(ADMIN) 가드레일
- **INCIDENT 도메인**: 실명/사번 일반화, 판단/추측 표현 금지
- **EDU 도메인**: 개인별 상세 정보 제한, 통계 위주 안내

#### 신고관리자(INCIDENT_MANAGER) 가드레일
- **INCIDENT 도메인**: 사건 참여자 실명 노출 금지, 징계 추천 금지
- **INCIDENT_REPORT**: 신고자 보호 우선, 결과 예측 금지

### 4. ChatService 통합 (`app/services/chat_service.py`)

- RouteType 분기 로직 업데이트 (RAG_INTERNAL, BACKEND_API, MIXED_BACKEND_RAG 등)
- 가드레일 서비스 통합 (system prompt prefix + answer prefix)
- `ChatAnswerMeta.user_role` 필드 추가

## 테스트 결과

```
tests/test_phase10_role_domain_routing.py - 38 tests PASSED
전체 테스트: 145 tests PASSED
```

### Phase 10 테스트 항목
1. UserRole 파싱 테스트 (5개)
2. Domain 파싱 테스트 (4개)
3. EMPLOYEE 라우팅 테스트 (6개)
4. ADMIN 라우팅 테스트 (4개)
5. INCIDENT_MANAGER 라우팅 테스트 (2개)
6. 가드레일 테스트 (6개)
7. ChatService 통합 테스트 (5개)
8. Enum 테스트 (6개)

## 파일 변경 요약

### 신규 파일
| 파일 | 설명 |
|------|------|
| `app/services/guardrail_service.py` | 역할별 가드레일 서비스 |
| `tests/test_phase10_role_domain_routing.py` | Phase 10 테스트 (38개) |

### 수정된 파일
| 파일 | 변경 내용 |
|------|-----------|
| `app/models/intent.py` | UserRole, Domain Enum 추가, IntentResult 확장 |
| `app/models/chat.py` | ChatAnswerMeta.user_role 필드 추가 |
| `app/services/intent_service.py` | 역할×도메인 라우팅 룰 구현 |
| `app/services/chat_service.py` | 새 RouteType 사용, 가드레일 통합 |
| `tests/test_intent_and_pii.py` | Phase 10 RouteType으로 업데이트 |
| `tests/test_chat_rag_integration.py` | Phase 10 RouteType으로 업데이트 |
| `tests/test_chat_http_e2e.py` | Phase 10 RouteType으로 업데이트 |

## 라우팅 예시

### 직원이 사고 신고
```
POST /api/v1/chat
{
    "user_role": "EMPLOYEE",
    "messages": [{"role": "user", "content": "보안 사고가 발생해서 신고하려고 합니다"}]
}

Response:
{
    "answer": "⚠️ **신고 시 주의사항**\n...",
    "meta": {
        "user_role": "EMPLOYEE",
        "route": "BACKEND_API",
        "intent": "INCIDENT_REPORT"
    }
}
```

### 관리자가 사고 현황 조회
```
POST /api/v1/chat
{
    "user_role": "ADMIN",
    "messages": [{"role": "user", "content": "이번 달 보안사고 통계 알려줘"}]
}

Response:
{
    "meta": {
        "user_role": "ADMIN",
        "route": "MIXED_BACKEND_RAG",
        "intent": "INCIDENT_QA"
    }
}
```

## 하위 호환성

레거시 RouteType 값(ROUTE_RAG_INTERNAL, ROUTE_LLM_ONLY 등)은 ChatService의 분기 로직에서 계속 처리됩니다. 기존 클라이언트 코드 수정 없이 동작합니다.

## 향후 과제

1. **BACKEND_API 실제 연동**: 현재 BACKEND_API 라우트는 LLM-only로 fallback 처리
2. **MIXED_BACKEND_RAG 구현**: 백엔드 데이터 + RAG 조합 로직 구현
3. **ML 기반 Intent Classifier**: 현재 키워드 기반 → ML/LLM 기반으로 교체 예정
4. **가드레일 강화**: 실시간 PII 탐지와 연계한 동적 가드레일
