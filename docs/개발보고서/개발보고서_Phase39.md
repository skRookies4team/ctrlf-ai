# Phase 39: Answer Guard Service (답변 품질 가드레일)

## 개요

사내 문서 기반 답변 품질을 망치는 구조적 원인을 제거하기 위한 종합적인 가드레일 시스템입니다.

## 해결한 문제점

| 문제 | 원인 | 해결책 |
|------|------|--------|
| 내부 규정 질문에서 문서를 못 찾았는데도 일반론 생성 | RAG 결과 없을 때 LLM이 추측 답변 | [A] Answerability Gate |
| LLM_ONLY 응답이 가짜 조항 인용 | 근거 없는 "제10조 제2항" 등 생성 | [B] Citation Hallucination Guard |
| 퇴직금 질문에 교육 현황 템플릿 섞임 | 라우팅/상태 누수 | [C] Template Routing Fix |
| 징계심의 답변에 중국어 섞임 | 언어 가드레일 부재 | [D] Korean-only Output Enforcement |
| 불만 입력에 UX 최악 | 사과/원인/다음 행동 없음 | [E] Complaint Fast Path |

## 변경된 파일 목록

### 새로 생성된 파일

| 파일 | 설명 |
|------|------|
| `app/services/answer_guard_service.py` | 답변 품질 가드레일 서비스 (핵심) |
| `tests/test_phase39_answer_guard.py` | Phase 39 테스트 (39개) |

### 수정된 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/services/chat_service.py` | AnswerGuardService 통합 (5개 체크포인트 추가) |
| `app/core/config.py` | `ANSWER_GUARD_DEBUG` 설정 추가 |

## 핵심 기능 설명

### [A] Answerability Gate (답변 가능 여부 게이트)

내부 규정/사규/정책 질문에서 RAG 근거가 없으면 답변 생성을 금지합니다.

```python
# chat_service.py에서 RAG 검색 후 호출
is_answerable, template = answer_guard.check_answerability(
    intent=Tier0Intent.POLICY_QA,
    sources=rag_sources,  # 빈 리스트면 차단
    route_type=RouterRouteType.RAG_INTERNAL,
)
if not is_answerable:
    return template  # 고정 템플릿으로 종료
```

**고정 템플릿 (한국어):**
```
승인/인덱싱된 사내 문서에서 관련 내용을 찾지 못했어요.

**가능한 원인:**
• 문서 미업로드
• 문서 미승인
• 인덱싱 제외
• 검색 설정 문제

**조치:** 문서 업로드 → 승인 → 인덱싱 후 다시 질문해 주세요.
```

### [B] Citation Hallucination Guard (가짜 조항 인용 차단)

LLM 답변에 "제N조/조항/항" 패턴이 있는데 RAG 소스에 근거가 없으면 답변을 폐기합니다.

```python
# LLM 응답 후 검증
citation_valid, validated_answer = answer_guard.validate_citation(
    answer="제99조에 따르면...",  # RAG에 없는 조항
    sources=rag_sources,
)
if not citation_valid:
    return validated_answer  # 차단 템플릿으로 교체
```

**검출 패턴:**
- `제N조`, `제N항`, `제N호`
- `N조 N항`, `조항`, `별표`, `부칙`
- `시행령`, `시행규칙`

### [C] Template Routing Fix (템플릿 매핑 버그 제거)

`request_id` 기반 스코프 관리로 다른 요청의 템플릿이 섞이는 것을 방지합니다.

```python
# 요청 시작 시 컨텍스트 생성
ctx = answer_guard.create_request_context(
    intent=Tier0Intent.POLICY_QA,
    route_type=RouterRouteType.RAG_INTERNAL,
)

# 응답 시 검증
is_valid = ctx.validate_response_context(response_request_id)
```

### [D] Korean-only Output Enforcement (언어 가드레일)

중국어 혼입을 탐지하고, 재생성을 시도하거나 에러 템플릿을 반환합니다.

```python
# LLM 응답 후 검증
korean_valid, result = await answer_guard.enforce_korean_output(
    answer="年假规定에 대해...",  # 중국어 혼입
    llm_regenerate_fn=regenerate_fn,
    original_query=query,
)
if not korean_valid:
    return result  # "언어 오류" 템플릿
```

**처리 플로우:**
1. 중국어 문자 3개 이상 감지
2. "한국어로만 다시 작성" 프롬프트로 재생성
3. 재생성도 실패하면 "언어 오류가 감지되어 답변을 중단합니다" 템플릿

### [E] Complaint Fast Path (불만/욕설 빠른 경로)

불만 키워드 감지 시 RAG/툴 호출 없이 즉시 응답합니다.

```python
# intent 분류 전에 먼저 실행 (전처리)
complaint_response = answer_guard.check_complaint_fast_path(
    user_query="왜몰라이씨",
    last_error_reason="NO_RAG_EVIDENCE",
)
if complaint_response:
    return complaint_response  # 즉시 응답
```

**불만 키워드 예시:**
`그지`, `왜몰라`, `뭐하`, `답답`, `짜증`, `개같`, `멍청`, `병신` 등

**응답 형식:**
```
방금 답변이 도움 안 됐죠. 미안해요.

지금은 관련 문서 근거를 못 찾아서 정확히 답할 수 없었어요.

문서를 인덱싱하면 그 기준으로만 답하게 만들게요. 다시 질문해 주세요.
```

### [F] Debug Logging (디버그 가시성)

`ANSWER_GUARD_DEBUG=true` 설정 시 라우팅/검색/검증 정보를 로그로 출력합니다.

```bash
# .env에서 활성화
ANSWER_GUARD_DEBUG=true
```

**로그 출력 예시:**
```
[ANSWER_GUARD_DEBUG] request_id=abc-123
  route: intent=POLICY_QA, route_type=RAG_INTERNAL, reason=keyword match
  retrieval: topK=5, results=3
  answerable: True (sources=3, is_policy=True)
  guards: citation_valid=True, language_valid=True
```

## 환경변수 (.env)

```env
# Phase 39: Answer Guard 설정
ANSWER_GUARD_DEBUG=false  # 디버그 로그 활성화 (true/false)
```

## 테스트 결과

```
$ pytest tests/test_phase39_answer_guard.py -v

============================= 39 passed in 2.84s ==============================

테스트 케이스:
- TestComplaintFastPath (6개): 불만 키워드 감지, 정상 질문 통과
- TestAnswerabilityGate (5개): RAG 근거 기반 답변 가능 여부 판정
- TestCitationHallucinationGuard (6개): 가짜 조항 인용 검증
- TestKoreanOutputEnforcement (6개): 중국어 혼입 탐지 및 재생성
- TestRequestContext (4개): request_id 스코프 관리
- TestDebugLogging (3개): 디버그 정보 생성/출력
- TestTemplates (4개): 고정 템플릿 내용 검증
- TestSingleton (2개): 싱글톤 패턴
- TestIntegration (3개): 전체 가드 플로우 통합
```

## 체크포인트 위치 (chat_service.py)

```
사용자 질문
    ↓
[1] PII Masking (INPUT)
    ↓
[2] ★ Complaint Fast Path 체크 ← Phase 39 [E]
    ↓
[3] Router Orchestrator
    ↓
[4] Intent Classification
    ↓
[5] RAG Search / Backend Data
    ↓
[6] ★ Answerability Gate 체크 ← Phase 39 [A]
    ↓
[7] Build LLM Prompt
    ↓
[8] Generate LLM Response
    ↓
[9] ★ Citation Hallucination Guard ← Phase 39 [B]
    ↓
[10] ★ Korean Output Enforcement ← Phase 39 [D]
    ↓
[11] PII Masking (OUTPUT)
    ↓
[12] Apply Guardrails
    ↓
최종 응답
```

## 완료 조건 (AC) 체크리스트

- [x] 내부 규정 질문 + RAG 결과 없음 → "근거 없음" 템플릿 반환
- [x] LLM 답변에 RAG에 없는 조항 패턴 → 답변 폐기
- [x] 중국어 혼입 시 재생성 1회 시도 → 실패하면 에러 템플릿
- [x] 불만 키워드 시 RAG/툴 호출 없이 즉시 응답
- [x] 디버그 모드에서 route/retrieval/answerable 정보 출력
- [x] 테스트 39개 통과
