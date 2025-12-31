# Phase 15: RAG Gap 보완 제안 API 구현

## 개요

Phase 15에서는 **RAG Gap 보완 제안 API**를 구현했습니다. 백엔드(ctrlf-back)에서 수집한 RAG Gap 질문들을 AI Gateway에 보내면, LLM이 "어떤 사규/교육 항목을 추가/보완하면 좋을지" 분석하여 제안을 반환합니다.

이 API는 관리자/운영자용 분석 도구로, 최종 사용자 답변에는 관여하지 않습니다.

## 구현 내용

### 1. 새 엔드포인트

```
POST /ai/gap/policy-edu/suggestions
```

### 2. 요청/응답 스키마

**요청 (GapSuggestionRequest)**:
```json
{
  "timeRange": {
    "from": "2025-12-01T00:00:00",
    "to": "2025-12-10T23:59:59"
  },
  "domain": "POLICY",
  "groupingKey": "intent",
  "questions": [
    {
      "questionId": "log-123",
      "text": "재택근무할 때 VPN 안 쓰면 어떻게 되나요?",
      "userRole": "EMPLOYEE",
      "intent": "POLICY_QA",
      "domain": "POLICY",
      "askedCount": 5
    }
  ]
}
```

**응답 (GapSuggestionResponse)**:
```json
{
  "summary": "재택근무 시 보안 규정에 대한 문서가 부족합니다.",
  "suggestions": [
    {
      "id": "SUG-001",
      "title": "재택근무 시 정보보호 수칙 상세 예시 추가",
      "description": "VPN 사용 의무, 공용 Wi-Fi 금지 등을 포함한 조문을 신설하는 것이 좋습니다.",
      "relatedQuestionIds": ["log-123"],
      "priority": "HIGH"
    }
  ]
}
```

### 3. 파일 구조

```
app/
├── models/
│   └── gap_suggestion.py     # DTO 모델 정의
├── services/
│   └── gap_suggestion_service.py  # LLM 호출 및 파싱 로직
├── api/
│   └── v1/
│       └── gap_suggestions.py     # FastAPI 엔드포인트
└── main.py                        # 라우터 등록
```

### 4. 핵심 로직

#### GapSuggestionService

```python
class GapSuggestionService:
    async def generate_suggestions(
        self,
        request: GapSuggestionRequest,
    ) -> GapSuggestionResponse:
        # 1. 질문이 없으면 빈 응답
        if not request.questions:
            return EMPTY_QUESTIONS_RESPONSE

        # 2. LLM 메시지 구성
        messages = self._build_llm_messages(request)

        # 3. LLM 호출
        llm_response = await self._llm.generate_chat_completion(...)

        # 4. 응답 파싱
        return self._parse_llm_response(llm_response)
```

#### LLM 프롬프트

```
System:
당신은 기업 내부 정보보호/인사 사규를 설계하는 기획자를 돕는 AI입니다.
입력으로는 최근 일정 기간 동안 RAG 검색에서 관련 문서를 찾지 못한 질문 목록이 주어집니다.

당신의 역할:
1) 기존 사규/교육에서 어떤 부분이 부족한지 요약
2) 새로 추가/보완하면 좋을 항목 후보 생성
3) 각 항목에 대해: 제목, 설명, 관련 질문 ID, 우선순위 제공

User:
도메인: POLICY
분석 대상 질문 수: 3개

질문 목록:
1. [ID: log-123] 재택근무할 때 VPN 안 쓰면 어떻게 되나요?
   - 역할: EMPLOYEE, 의도: POLICY_QA, 도메인: POLICY (질문 횟수: 5회)
...
```

### 5. 에러 처리

| 상황 | 처리 방식 |
|------|----------|
| 질문 0개 | 빈 응답 + 안내 메시지 |
| LLM 호출 실패 | Fallback 응답 (도메인별 기본 제안) |
| JSON 파싱 실패 | Fallback 응답 |
| 우선순위 정규화 | HIGH/MEDIUM/LOW로 변환 |

### 6. Fallback 응답 예시

LLM 호출 실패 시:
```json
{
  "summary": "총 3개의 RAG Gap 질문이 발견되었습니다. 자동 분석에 문제가 있어 기본 제안을 드립니다.",
  "suggestions": [
    {
      "id": "SUG-FALLBACK-POLICY",
      "title": "POLICY 영역 문서 보완 필요",
      "description": "POLICY 도메인에서 3개의 질문에 대한 문서가 부족합니다.",
      "relatedQuestionIds": ["log-1", "log-2", "log-3"],
      "priority": "MEDIUM"
    }
  ]
}
```

## 테스트 결과

### Phase 15 테스트 (23개 통과)

```
TestGapSuggestionModels: 7개 - 모델 생성/직렬화
TestGapSuggestionService: 7개 - 서비스 로직
TestGapSuggestionsAPI: 6개 - API 통합
TestGapSuggestionsErrorCases: 3개 - 에러 처리
```

### 전체 테스트

```
============================= 284 passed in 3.68s =============================
```

## 변경된 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/models/gap_suggestion.py` | 신규 - DTO 모델 |
| `app/services/gap_suggestion_service.py` | 신규 - 서비스 로직 |
| `app/api/v1/gap_suggestions.py` | 신규 - API 엔드포인트 |
| `app/api/v1/__init__.py` | gap_suggestions 추가 |
| `app/main.py` | 라우터 등록 |
| `tests/test_phase15_gap_suggestions.py` | 신규 - 23개 테스트 |

## 사용 예시

### curl 요청

```bash
curl -X POST http://localhost:8000/ai/gap/policy-edu/suggestions \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "POLICY",
    "questions": [
      {
        "questionId": "log-123",
        "text": "재택근무할 때 VPN 안 쓰면 어떻게 되나요?",
        "userRole": "EMPLOYEE",
        "intent": "POLICY_QA",
        "domain": "POLICY",
        "askedCount": 5
      }
    ]
  }'
```

## TODO: 향후 개선

1. **인증/권한**: IP 제한 또는 헤더 토큰 기반 인증 추가
2. **캐싱**: 동일 질문 세트에 대한 결과 캐싱
3. **비동기 처리**: 대량 질문 처리 시 백그라운드 작업으로 전환
4. **대시보드 연동**: ctrlf-back 관리자 대시보드와 연동

## 결론

Phase 15에서 RAG Gap 보완 제안 API를 구현하여, 관리자가 "어떤 사규/교육 문서가 부족한지" AI 분석을 받을 수 있게 되었습니다. 이를 통해 사규/교육 콘텐츠의 지속적인 개선이 가능해집니다.
