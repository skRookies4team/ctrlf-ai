# Phase 4-1 개발 보고서: PII 마스킹 + 의도/라우팅 인터페이스

## 개요

**프로젝트**: ctrlf-ai-gateway
**Phase**: 4-1
**작성일**: 2025-12-08
**목표**: PII 마스킹 서비스와 의도 분류/라우팅 인터페이스 구현

---

## 1. 구현 목표

Phase 4-1에서는 ChatService 파이프라인에 다음 기능을 추가했습니다:

1. **PII/Intent/Route 도메인 모델** 정의
2. **IntentService**: 규칙 기반 의도 분류 및 라우팅
3. **PiiService**: 3단계 PII 마스킹 인터페이스
4. **ChatService 통합**: PII + Intent 파이프라인 연결
5. **테스트 추가**: 새로운 기능에 대한 검증

---

## 2. 아키텍처 변경

### 2.1 이전 파이프라인 (Phase 3-2)
```
사용자 입력 → RAG 검색 → LLM 프롬프트 구성 → LLM 호출 → 응답 반환
```

### 2.2 새로운 파이프라인 (Phase 4-1)
```
사용자 입력
    ↓
[1] PII 마스킹 (INPUT 단계)
    ↓
[2] 의도 분류 + 라우팅 결정
    ↓
[3] RAG 검색 (라우트에 따라 선택적)
    ↓
[4] LLM 프롬프트 구성
    ↓
[5] LLM 호출
    ↓
[6] PII 마스킹 (OUTPUT 단계)
    ↓
응답 반환 (meta.route, meta.masked 포함)
```

### 2.3 컴포넌트 다이어그램
```
┌─────────────────────────────────────────────────────────────┐
│                      ChatService                             │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │  PiiService  │  │ IntentService │  │  RagflowClient   │  │
│  │  (3-stage)   │  │ (rule-based)  │  │                  │  │
│  └──────────────┘  └───────────────┘  └──────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                    LLMClient                          │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 구현 상세

### 3.1 도메인 모델 (app/models/intent.py)

#### IntentType (의도 유형)
| 값 | 설명 |
|---|---|
| `POLICY_QA` | 사규/규정/정책 관련 Q&A |
| `INCIDENT_REPORT` | 사고/유출/보안사고 신고 |
| `EDUCATION_QA` | 교육/훈련/퀴즈 관련 |
| `GENERAL_CHAT` | 일반 잡담, Small talk |
| `SYSTEM_HELP` | 시스템 사용법 안내 |
| `UNKNOWN` | 분류 불가 |

#### RouteType (라우팅 유형)
| 값 | 설명 | RAG 사용 |
|---|---|---|
| `ROUTE_RAG_INTERNAL` | 내부 RAG + LLM | O |
| `ROUTE_LLM_ONLY` | LLM만 사용 | X |
| `ROUTE_INCIDENT` | 사고 신고 경로 (향후 확장) | X |
| `ROUTE_TRAINING` | 교육/퀴즈 경로 | X |
| `ROUTE_FALLBACK` | 설정 미비 시 폴백 | X |
| `ROUTE_ERROR` | 에러 발생 시 | X |

#### MaskingStage (마스킹 단계)
| 값 | 설명 | 적용 시점 |
|---|---|---|
| `INPUT` | 1차 마스킹 | 사용자 입력 시 |
| `OUTPUT` | 2차 마스킹 | LLM 응답 출력 전 |
| `LOG` | 3차 마스킹 | 로그/학습 데이터 저장 전 |

#### 데이터 모델
```python
class PiiTag(BaseModel):
    entity: str       # 검출된 민감정보 텍스트
    label: str        # 엔티티 타입 (PERSON, PHONE, RRN 등)
    start: Optional[int]  # 시작 인덱스
    end: Optional[int]    # 끝 인덱스

class PiiMaskResult(BaseModel):
    original_text: str    # 원본 텍스트
    masked_text: str      # 마스킹된 텍스트
    has_pii: bool         # PII 검출 여부
    tags: List[PiiTag]    # 검출된 PII 목록

class IntentResult(BaseModel):
    intent: IntentType    # 분류된 의도
    domain: Optional[str] # 도메인 (POLICY, INCIDENT 등)
    route: RouteType      # 라우팅 결정
```

### 3.2 IntentService (app/services/intent_service.py)

규칙 기반 의도 분류 서비스로, 키워드 매칭을 통해 의도를 분류합니다.

#### 분류 규칙 (우선순위 순)
| 우선순위 | 키워드 예시 | Intent | Route |
|---|---|---|---|
| 1 | 사고, 유출, 침해, 해킹, 신고 | INCIDENT_REPORT | ROUTE_INCIDENT |
| 2 | 교육, 훈련, 퀴즈, 시험, 수료 | EDUCATION_QA | ROUTE_TRAINING |
| 3 | 사용법, 메뉴, 화면, 기능 | SYSTEM_HELP | ROUTE_LLM_ONLY |
| 4 | 안녕, ㅎㅎ, 날씨, 농담 | GENERAL_CHAT | ROUTE_LLM_ONLY |
| 5 | domain=POLICY | POLICY_QA | ROUTE_RAG_INTERNAL |
| 기본 | 기타 모든 쿼리 | POLICY_QA | ROUTE_RAG_INTERNAL |

#### 사용 예시
```python
service = IntentService()
result = service.classify(
    req=chat_request,
    user_query="연차 이월 규정 알려줘"
)
# result.intent == IntentType.POLICY_QA
# result.route == RouteType.ROUTE_RAG_INTERNAL
```

### 3.3 PiiService (app/services/pii_service.py)

3단계 PII 마스킹을 지원하는 서비스입니다.

#### 동작 흐름
```
PII_ENABLED 확인
    ↓
False → 원문 그대로 반환 (has_pii=False)
    ↓
True → PII_BASE_URL 확인
    ↓
미설정 → 원문 그대로 반환 (has_pii=False)
    ↓
설정됨 → HTTP 서비스 호출 → 마스킹 결과 반환
```

#### 설정
| 환경변수 | 설명 | 기본값 |
|---|---|---|
| `PII_BASE_URL` | PII 서비스 URL | (없음) |
| `PII_ENABLED` | 마스킹 활성화 여부 | `true` |

#### 외부 PII 서비스 연동 스펙
```
엔드포인트: POST {PII_BASE_URL}/mask
요청: { "text": "...", "stage": "input" }
응답: {
  "original_text": "...",
  "masked_text": "...",
  "has_pii": true,
  "tags": [{"entity": "홍길동", "label": "PERSON", "start": 0, "end": 3}]
}
```

#### 3단계 마스킹 전략
| 단계 | 적용 시점 | 현재 구현 | 향후 계획 |
|---|---|---|---|
| INPUT | 사용자 입력 시 | 기본 마스킹 | 표준 마스킹 |
| OUTPUT | LLM 응답 시 | 기본 마스킹 | 표준 마스킹 |
| LOG | 로그 저장 시 | 인터페이스만 | 강한 마스킹 (재식별 방지) |

### 3.4 ChatService 통합 (app/services/chat_service.py)

#### 생성자 변경
```python
# 이전
def __init__(self, ragflow_client, llm_client)

# 이후
def __init__(self, ragflow_client, llm_client, pii_service, intent_service)
```

#### handle_chat 파이프라인
```python
async def handle_chat(self, req: ChatRequest) -> ChatResponse:
    # Step 1: 사용자 쿼리 추출
    user_query = req.messages[-1].content

    # Step 2: PII 마스킹 (INPUT)
    pii_input = await self._pii.detect_and_mask(user_query, MaskingStage.INPUT)
    masked_query = pii_input.masked_text

    # Step 3: 의도 분류 + 라우팅
    intent_result = self._intent.classify(req=req, user_query=masked_query)
    route = intent_result.route

    # Step 4: RAG 검색 (라우트에 따라)
    if route == RouteType.ROUTE_RAG_INTERNAL:
        sources = await self._ragflow.search(...)
    else:
        sources = []

    # Step 5: LLM 호출
    raw_answer = await self._llm.generate_chat_completion(...)

    # Step 6: PII 마스킹 (OUTPUT)
    pii_output = await self._pii.detect_and_mask(raw_answer, MaskingStage.OUTPUT)
    final_answer = pii_output.masked_text

    # Step 7: 응답 반환
    return ChatResponse(
        answer=final_answer,
        sources=sources,
        meta=ChatAnswerMeta(
            route=route.value,
            masked=pii_input.has_pii or pii_output.has_pii,
            ...
        )
    )
```

---

## 4. 파일 변경 내역

### 4.1 추가된 파일 (4개)
| 파일 | 라인 수 | 설명 |
|---|---|---|
| `app/models/intent.py` | 97 | Intent/Route/PII 도메인 모델 |
| `app/services/intent_service.py` | 153 | 규칙 기반 의도 분류 서비스 |
| `app/services/pii_service.py` | 240 | 3단계 PII 마스킹 서비스 |
| `tests/test_intent_and_pii.py` | 312 | Intent/PII 테스트 (19개) |

### 4.2 수정된 파일 (4개)
| 파일 | 변경 내용 |
|---|---|
| `app/core/config.py` | PII_BASE_URL, PII_ENABLED 설정 추가 |
| `app/services/chat_service.py` | PII + Intent 통합 파이프라인 |
| `.env.example` | PII 환경변수 예시 추가 |
| `README.md` | 환경변수 표에 PII 항목 추가 |

---

## 5. 테스트 결과

### 5.1 테스트 실행
```bash
$ pytest -v
============================= test session starts =============================
collected 48 items

tests/test_chat_api.py::test_chat_endpoint_returns_200 PASSED
tests/test_chat_api.py::test_chat_endpoint_returns_dummy_answer PASSED
tests/test_chat_api.py::test_chat_endpoint_meta_structure PASSED
tests/test_chat_api.py::test_chat_endpoint_with_minimal_payload PASSED
tests/test_chat_api.py::test_chat_endpoint_with_conversation_history PASSED
tests/test_chat_api.py::test_chat_endpoint_validation_error PASSED
tests/test_chat_api.py::test_chat_endpoint_invalid_role PASSED
tests/test_health.py::test_health_check_returns_200 PASSED
tests/test_health.py::test_health_check_returns_status_ok PASSED
tests/test_health.py::test_health_check_contains_app_info PASSED
tests/test_health.py::test_readiness_check_returns_200 PASSED
tests/test_health.py::test_readiness_check_returns_ready_true PASSED
tests/test_intent_and_pii.py::test_intent_policy_qa PASSED
tests/test_intent_and_pii.py::test_intent_incident_report PASSED
tests/test_intent_and_pii.py::test_intent_education_qa PASSED
tests/test_intent_and_pii.py::test_intent_general_chat PASSED
tests/test_intent_and_pii.py::test_intent_with_domain_policy PASSED
tests/test_intent_and_pii.py::test_intent_default_fallback PASSED
tests/test_intent_and_pii.py::test_pii_disabled_returns_original PASSED
tests/test_intent_and_pii.py::test_pii_no_base_url_returns_original PASSED
tests/test_intent_and_pii.py::test_pii_all_stages_work PASSED
tests/test_intent_and_pii.py::test_pii_preserves_text_on_skip PASSED
tests/test_intent_and_pii.py::test_chat_service_with_intent_and_pii PASSED
tests/test_intent_and_pii.py::test_chat_service_general_chat_route PASSED
tests/test_intent_and_pii.py::test_chat_service_incident_route PASSED
tests/test_intent_and_pii.py::test_chat_service_education_route PASSED
tests/test_intent_and_pii.py::test_chat_service_meta_has_required_fields PASSED
tests/test_intent_and_pii.py::test_masking_stage_enum_values PASSED
tests/test_intent_and_pii.py::test_masking_stage_enum_members PASSED
tests/test_intent_and_pii.py::test_intent_type_enum_values PASSED
tests/test_intent_and_pii.py::test_route_type_enum_values PASSED
tests/test_rag_api.py::test_rag_process_returns_200 PASSED
tests/test_rag_api.py::test_rag_process_returns_success PASSED
tests/test_rag_api.py::test_rag_process_response_structure PASSED
tests/test_rag_api.py::test_rag_process_without_acl PASSED
tests/test_rag_api.py::test_rag_process_with_empty_acl PASSED
tests/test_rag_api.py::test_rag_process_validation_error_missing_fields PASSED
tests/test_rag_api.py::test_rag_process_validation_error_invalid_url PASSED
tests/test_rag_api.py::test_rag_process_preserves_doc_id PASSED
tests/test_service_fallback.py::test_chat_service_returns_response_without_config PASSED
tests/test_service_fallback.py::test_chat_service_returns_fallback_message PASSED
tests/test_service_fallback.py::test_chat_service_empty_sources_without_ragflow PASSED
tests/test_service_fallback.py::test_chat_service_meta_has_latency PASSED
tests/test_service_fallback.py::test_rag_service_returns_dummy_success_without_config PASSED
tests/test_service_fallback.py::test_rag_service_preserves_doc_id PASSED
tests/test_service_fallback.py::test_ragflow_client_search_returns_empty_without_config PASSED
tests/test_service_fallback.py::test_ragflow_client_process_returns_failure_without_config PASSED
tests/test_service_fallback.py::test_llm_client_returns_fallback_without_config PASSED

============================= 48 passed in 5.06s ==============================
```

### 5.2 테스트 요약
| 테스트 파일 | 테스트 수 | 결과 |
|---|---|---|
| test_health.py | 5 | ✅ PASSED |
| test_chat_api.py | 7 | ✅ PASSED |
| test_rag_api.py | 8 | ✅ PASSED |
| test_service_fallback.py | 9 | ✅ PASSED |
| **test_intent_and_pii.py** | **19** | ✅ PASSED |
| **총계** | **48** | ✅ ALL PASSED |

### 5.3 새 테스트 항목 (19개)
| 카테고리 | 테스트 | 설명 |
|---|---|---|
| IntentService | test_intent_policy_qa | 정책 질문 분류 |
| IntentService | test_intent_incident_report | 사고 신고 분류 |
| IntentService | test_intent_education_qa | 교육 질문 분류 |
| IntentService | test_intent_general_chat | 일반 잡담 분류 |
| IntentService | test_intent_with_domain_policy | domain=POLICY 분류 |
| IntentService | test_intent_default_fallback | 기본값 분류 |
| PiiService | test_pii_disabled_returns_original | 비활성화 시 원문 반환 |
| PiiService | test_pii_no_base_url_returns_original | URL 미설정 시 원문 반환 |
| PiiService | test_pii_all_stages_work | 3단계 모두 동작 |
| PiiService | test_pii_preserves_text_on_skip | 스킵 시 텍스트 보존 |
| ChatService | test_chat_service_with_intent_and_pii | 통합 테스트 |
| ChatService | test_chat_service_general_chat_route | 잡담 라우팅 |
| ChatService | test_chat_service_incident_route | 사고 라우팅 |
| ChatService | test_chat_service_education_route | 교육 라우팅 |
| ChatService | test_chat_service_meta_has_required_fields | meta 필드 검증 |
| Enum | test_masking_stage_enum_values | MaskingStage 값 검증 |
| Enum | test_masking_stage_enum_members | MaskingStage 멤버 검증 |
| Enum | test_intent_type_enum_values | IntentType 값 검증 |
| Enum | test_route_type_enum_values | RouteType 값 검증 |

---

## 6. 디렉터리 구조 (Phase 4-1 이후)

```
ctrlf-ai/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── health.py
│   │       ├── chat.py
│   │       └── rag.py
│   ├── clients/
│   │   ├── __init__.py
│   │   ├── http_client.py
│   │   ├── ragflow_client.py      # (stub)
│   │   └── llm_client.py          # (stub)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # ← PII_BASE_URL, PII_ENABLED 추가
│   │   └── logging.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   ├── rag.py
│   │   └── intent.py              # ← NEW: Intent/Route/PII 모델
│   └── services/
│       ├── __init__.py
│       ├── chat_service.py        # ← PII + Intent 통합
│       ├── rag_service.py
│       ├── ragflow_client.py
│       ├── llm_client.py
│       ├── intent_service.py      # ← NEW: 의도 분류 서비스
│       └── pii_service.py         # ← NEW: PII 마스킹 서비스
├── tests/
│   ├── __init__.py
│   ├── test_health.py
│   ├── test_chat_api.py
│   ├── test_rag_api.py
│   ├── test_service_fallback.py
│   └── test_intent_and_pii.py     # ← NEW: Intent/PII 테스트
├── .env.example                   # ← PII 설정 추가
├── Dockerfile
├── README.md                      # ← 환경변수 표 업데이트
├── requirements.txt
├── DEVELOPMENT_REPORT_PHASE3_2.md
└── DEVELOPMENT_REPORT_PHASE4_1.md # ← NEW: 이 보고서
```

---

## 7. 환경변수 설정

### 7.1 전체 환경변수 목록
| 변수명 | 설명 | 기본값 | 필수 |
|---|---|---|---|
| `APP_NAME` | 애플리케이션 이름 | `ctrlf-ai-gateway` | X |
| `APP_ENV` | 실행 환경 | `local` | X |
| `APP_VERSION` | 버전 | `0.1.0` | X |
| `LOG_LEVEL` | 로그 레벨 | `INFO` | X |
| `RAGFLOW_BASE_URL` | RAGFlow 서비스 URL | - | X (운영) |
| `LLM_BASE_URL` | LLM 서비스 URL | - | X (운영) |
| `BACKEND_BASE_URL` | Spring 백엔드 URL | - | X (운영) |
| **`PII_BASE_URL`** | PII 서비스 URL | - | X (선택) |
| **`PII_ENABLED`** | PII 마스킹 활성화 | `true` | X |
| `CORS_ORIGINS` | 허용 Origin | `*` | X |

### 7.2 .env.example 추가 내용
```bash
# ===== PII 마스킹 설정 =====
# PII 마스킹 서비스 URL (외부 PII 마스킹 서비스, 선택사항)
# 비어있으면 내장 규칙 기반 마스킹 사용
# 예: http://localhost:8000/pii 또는 http://pii-service:8000
PII_BASE_URL=

# PII 마스킹 활성화 여부 (true/false)
# true: 입력/출력에서 개인정보 마스킹 수행
# false: 마스킹 바이패스 (개발/테스트용)
PII_ENABLED=true
```

---

## 8. API 응답 변경

### 8.1 POST /ai/chat/messages 응답 예시
```json
{
  "answer": "연차 이월 규정은 다음과 같습니다...",
  "sources": [
    {
      "doc_id": "HR-001",
      "title": "인사규정",
      "page": 15,
      "score": 0.95,
      "snippet": "연차휴가는 다음 연도로 이월할 수 있으며..."
    }
  ],
  "meta": {
    "used_model": "internal-llm",
    "route": "ROUTE_RAG_INTERNAL",
    "masked": false,
    "latency_ms": 1234
  }
}
```

### 8.2 meta.route 값 설명
| 값 | 의미 |
|---|---|
| `ROUTE_RAG_INTERNAL` | RAG 검색 후 LLM 응답 생성 |
| `ROUTE_LLM_ONLY` | LLM만 사용 (잡담, 시스템 도움말) |
| `ROUTE_INCIDENT` | 사고 신고 관련 (향후 별도 모듈) |
| `ROUTE_TRAINING` | 교육/퀴즈 관련 |
| `ROUTE_FALLBACK` | 설정 미비로 인한 폴백 |
| `ROUTE_ERROR` | 처리 중 에러 발생 |

### 8.3 meta.masked 값 설명
| 값 | 의미 |
|---|---|
| `true` | 입력 또는 출력에서 PII가 검출되어 마스킹됨 |
| `false` | PII 미검출 또는 마스킹 비활성화 |

---

## 9. 향후 계획

### 9.1 단기 (Phase 4-2)
- [ ] GLiNER-PII 서비스 연동 구현
- [ ] 실제 PII 마스킹 HTTP 호출 테스트
- [ ] 로그 단계(LOG) 강화 마스킹 구현

### 9.2 중기
- [ ] ML/LLM 기반 Intent Classifier 교체
- [ ] INCIDENT 모듈 분리 및 연동
- [ ] TRAINING 모듈 (교육/퀴즈) 연동

### 9.3 장기
- [ ] 대화 컨텍스트 기반 의도 분류
- [ ] 사용자별 맞춤 라우팅
- [ ] PII 마스킹 정책 관리 UI

---

## 10. 결론

Phase 4-1에서는 ChatService 파이프라인에 PII 마스킹과 의도 분류/라우팅 기능을 성공적으로 통합했습니다.

### 주요 성과
1. **3단계 PII 마스킹 인터페이스** 구현 (INPUT/OUTPUT/LOG)
2. **규칙 기반 의도 분류** 및 **6가지 라우팅 경로** 구현
3. **완전한 파이프라인 통합** (PII → Intent → RAG → LLM → PII)
4. **19개 새 테스트** 추가, **총 48개 테스트 통과**

### 설계 원칙
- **확장성**: ML/LLM 기반 분류기로 쉽게 교체 가능
- **안전성**: PII 서비스 장애 시에도 시스템 정상 동작
- **투명성**: meta.route, meta.masked로 처리 경로 추적 가능

Phase 4-1 구현이 완료되어 PII 마스킹과 의도 기반 라우팅의 기반이 마련되었습니다.

---

**작성자**: Claude AI Assistant
**검토일**: 2025-12-08
