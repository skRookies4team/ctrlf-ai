# AI 채팅 서비스 성능 개선 보고서 v4

## 1. 개요

### 1.1 문서 목적
본 보고서는 Phase 46 개선 작업을 기술합니다. 이전 Phase에 대한 상세 내용은 아래를 참조하세요:
- [v1 보고서](./PERFORMANCE_IMPROVEMENT_REPORT.md): Phase 43 (1차~5차, "하" 키워드 버그 수정)
- [v2 보고서](./PERFORMANCE_IMPROVEMENT_REPORT_v2.md): Phase 44 (6차~7차, 가드레일 완화)
- [v3 보고서](./PERFORMANCE_IMPROVEMENT_REPORT_v3.md): Phase 45 (8차, 소프트 가드레일 & 언어 강제)

### 1.2 버전 히스토리
| 버전 | 일시 | 주요 변경 |
|------|------|----------|
| v1 | 2025-12-23 | Phase 43: 1차~5차 테스트, "하" 키워드 버그 수정 |
| v2 | 2025-12-23 | Phase 44: 6차~7차 테스트, 가드레일 완화 |
| v3 | 2025-12-23 | Phase 45: 8차 테스트, 소프트 가드레일 & 언어 강제 |
| v4 | 2025-12-24 | Phase 46: 소프트 가드레일 강화, 지표 분리 정의 |

### 1.3 Phase 45 성과 요약 및 피드백

#### 8차 테스트 결과 분석
| 지표 | 7차 (Phase 44) | 8차 (Phase 45) |
|------|---------------|----------------|
| 정상 답변율 | 97.7% | 100% |
| LANGUAGE_ERROR | 3건 | 0건 |
| 소스 포함율 | **측정 불가** | **측정 불가** |

> **중요**: 8차 테스트는 RAGFlow 미연결 상태에서 수행되어 **130건 전부 sources=0** 상태였습니다.
> 따라서 "100% 정상 답변율"은 **품질 지표가 아닌 '차단 없이 응답 생성' 지표**입니다.

#### Phase 45 피드백에서 도출된 개선 과제

| 우선순위 | 과제 | 해결 방향 |
|---------|------|----------|
| **필수** | DOMAIN_CONTACT_INFO 키 불일치 | EDU/EDUCATION 둘 다 지원 |
| **필수** | '확정 표현 금지' 규칙 미흡 | 시스템 프롬프트에 상세 지침 추가 |
| **권장** | 지표 정의 혼란 | Availability/RAG/정답 지표 분리 |
| **권장** | 8차 결과 오해 가능성 | RAGFlow 미연결 명확히 표시 |

---

## 2. Phase 46 지표 체계 정의

### 2.1 지표 분리 원칙

기존의 "정상 답변율"을 3개 축으로 분리하여 **실제 품질 측정**이 가능하도록 합니다:

```
[Availability 지표] 시스템이 답변을 생성할 수 있는가?
├── 차단률: 가드레일에 의해 차단된 비율
├── 에러율: 시스템 오류 비율
└── 언어 오류율: LANGUAGE_ERROR 비율

[RAG 지표] 검색이 정상적으로 동작하는가?
├── 소스 포함율: sources > 0인 응답 비율
├── returned_k: 평균 검색 결과 수
├── similarity 분포: 점수 구간별 분포
└── 도메인별 source=0 비율

[정답 지표] 답변이 정확한가?
├── 핵심 포함 여부: 골든 답안 대비 핵심 키워드 포함율
└── 근거 기반 여부: RAG 소스 인용 정확도
```

### 2.2 Phase 46 이후 측정 체계

| 지표 카테고리 | 세부 지표 | 측정 방법 | Phase 45까지 상태 |
|-------------|----------|----------|------------------|
| **Availability** | 차단률 | 가드레일 차단 건수/전체 | 0% (해결됨) |
| **Availability** | 에러율 | 시스템 에러 건수/전체 | 측정 중 |
| **Availability** | LANGUAGE_ERROR | 언어 오류 건수 | 0건 (해결됨) |
| **RAG** | 소스 포함율 | sources>0 비율 | **63.1% (7차)** |
| **RAG** | 도메인별 source=0 | 도메인별 실패율 | 사규 46.7% |
| **정답** | 핵심 포함율 | 골든 답안 대비 | **미측정** |

---

## 3. Phase 46 구현 상세

### 3.1 DOMAIN_CONTACT_INFO 키 정렬

**문제**: API에서 "EDUCATION"으로 도메인이 전달될 수 있으나, DOMAIN_CONTACT_INFO에는 "EDU"만 있어 DEFAULT로 폴백

**해결**: EDUCATION 키 추가 (EDU의 별칭)

#### 수정 파일: `app/services/answer_guard_service.py`

```python
# Phase 45/46: 도메인별 담당 부서 안내
# Phase 46: EDUCATION/EDU 둘 다 지원 (API에서 EDUCATION, 내부 enum은 EDU)
DOMAIN_CONTACT_INFO = {
    "POLICY": "• 인사팀 / 총무팀 (사내 규정 관련)",
    "EDU": "• 교육팀 / HR팀 (교육 관련)",
    "EDUCATION": "• 교육팀 / HR팀 (교육 관련)",  # Phase 46: EDU alias
    "INCIDENT": "• 보안팀 / 감사팀 (사건/사고 관련)",
    "PIP": "• 개인정보보호팀 (개인정보 관련)",
    "SHP": "• 인사팀 / 고충처리위원회 (성희롱 예방)",
    "BHP": "• 인사팀 / 고충처리위원회 (직장내 괴롭힘)",
    "DEP": "• 인사팀 (장애인 인식개선)",
    "JOB": "• 교육팀 / 해당 부서 (직무교육)",
    "DEFAULT": "• 담당 부서에 문의해 주세요.",
}
```

### 3.2 소프트 가드레일 '확정 표현 금지' 규칙 강화

**문제**: Phase 45의 소프트 가드레일이 경고 prefix만 추가하고, LLM에게 유보적 표현을 쓰라는 지시가 없음

**해결**: 시스템 프롬프트에 상세한 '확정 표현 금지' 지침 추가

#### 수정 파일: `app/services/answer_guard_service.py`

```python
def get_soft_guardrail_system_instruction(self) -> str:
    """소프트 가드레일용 시스템 프롬프트 추가 지침.

    Phase 45: sources=0일 때 LLM이 "확정" 표현을 쓰지 않도록 지시.
    Phase 46: '확정 표현 금지' 규칙 강화 + 답변 형태 제한
    """
    return (
        "\n\n[중요 지침 - 확정 표현 금지]\n"
        "현재 참고할 사내 문서 근거가 없습니다.\n"
        "따라서 답변 시 다음 규칙을 반드시 따르세요:\n\n"
        "【금지 표현】\n"
        "• '~입니다', '~해야 합니다', '반드시', '규정상', '의무적으로'\n"
        "• '회사 규정에 따르면', '사규에 의하면', '정책에 따라'\n"
        "• 제N조, 제N항 등 구체적 조항 번호 언급\n\n"
        "【허용 표현】\n"
        "• '일반적으로 ~하는 경향이 있습니다'\n"
        "• '~일 수 있습니다', '~으로 알려져 있습니다'\n"
        "• '대부분의 경우 ~합니다', '통상적으로 ~합니다'\n\n"
        "【답변 형식】\n"
        "답변은 반드시 다음 구조를 따르세요:\n"
        "1. 일반적인 안내 (확정 표현 없이)\n"
        "2. 확인 방법 안내 (어떤 문서/담당부서/키워드로 찾을 수 있는지)\n"
        "3. '정확한 정보는 담당 부서에 확인해 주세요' 문구로 마무리\n\n"
        "반드시 한국어로만 답변하세요.\n"
    )
```

### 3.3 시스템 프롬프트에 소프트 가드레일 지침 통합

**문제**: `get_soft_guardrail_system_instruction()` 메서드가 있지만 실제 LLM 프롬프트에 포함되지 않음

**해결**: MessageBuilder에 soft_guardrail_instruction 파라미터 추가

#### 수정 파일: `app/services/chat/message_builder.py`

```python
def build_rag_messages(
    self,
    user_query: str,
    sources: List[ChatSource],
    req: ChatRequest,
    rag_attempted: bool = False,
    user_role: Optional["UserRole"] = None,
    domain: Optional[str] = None,
    intent: Optional["IntentType"] = None,
    soft_guardrail_instruction: Optional[str] = None,  # Phase 46
) -> List[Dict[str, str]]:
    ...
    # Phase 46: 소프트 가드레일 시스템 지침 추가 (확정 표현 금지)
    if soft_guardrail_instruction:
        system_content = system_content + soft_guardrail_instruction
    ...
```

#### 수정 파일: `app/services/chat_service.py`

```python
# Phase 45/46: 소프트 가드레일 활성화 시 시스템 지침 추가
soft_guardrail_instruction: Optional[str] = None
if needs_soft_guardrail:
    soft_guardrail_instruction = self._answer_guard.get_soft_guardrail_system_instruction()

...

llm_messages = self._build_llm_messages(
    ...
    soft_guardrail_instruction=soft_guardrail_instruction,  # Phase 46
)
```

### 3.4 동작 흐름 (Phase 46 완성)

```
1. RAG 검색 수행
2. sources=0 체크
   ├── POLICY_QA/EDUCATION_QA → 소프트 가드레일 활성화
   │   ├── (a) 시스템 프롬프트에 '확정 표현 금지' 지침 추가
   │   ├── (b) LLM이 유보적 표현으로 답변 생성
   │   └── (c) 응답에 경고 prefix 추가
   └── GENERAL_CHAT/SYSTEM_HELP → 자연스러운 답변
3. 최종 응답 반환
```

---

## 4. 테스트 결과

### 4.1 단위 테스트

```
$ python -m pytest tests/ -v --tb=short -k "guard or soft"
52 passed, 933 deselected in 2.75s

$ python -m pytest tests/unit/test_chat_http_e2e.py tests/unit/test_phase14_rag_gap_candidate.py tests/unit/test_phase22_chat_router_integration.py -v
44 passed in 4.68s
```

- Phase 44 정책 완화에 맞게 테스트 케이스 업데이트
- 소프트 가드레일 mock 추가 (FakeAnswerGuardService)
- 모든 테스트 통과 확인

### 4.2 수정된 테스트 케이스

| 테스트 | 이전 기대값 | 수정된 기대값 | 이유 |
|-------|-----------|-------------|------|
| `test_policy_intent_without_sources_blocked` | is_answerable=False | is_answerable=True | Phase 44 정책 완화 |
| `test_debug_info_updated` | "requires RAG evidence" | "allowing LLM" | Phase 44 정책 완화 |
| `test_hallucinated_citation_blocked` | is_valid=False | is_valid=True | Phase 44 Citation 검증 완화 |
| `test_citation_without_sources_blocked` | is_valid=False | is_valid=True | Phase 44 Citation 검증 완화 |
| `test_full_guard_flow_blocked_no_rag` | NO_RAG_EVIDENCE | error_type=None | Phase 44 차단→허용 |

---

## 5. Phase 43 → 46 종합 성과

### 5.1 Availability 지표 개선

| 지표 | 1차 (Baseline) | 5차 (Phase 43) | 7차 (Phase 44) | 8차 (Phase 45) | Phase 46 |
|------|---------------|----------------|----------------|----------------|----------|
| 차단률 | ~63% | 63.8% | 2.3% | 0% | **0%** |
| LANGUAGE_ERROR | N/A | N/A | 3건 | 0건 | **0건** |
| 에러율 | N/A | N/A | N/A | N/A | 측정 중 |

### 5.2 RAG 지표 현황

| 지표 | 7차 (마지막 측정) | 8차 | Phase 46 |
|------|-----------------|-----|----------|
| 소스 포함율 | 63.1% | N/A (RAGFlow 미연결) | **측정 필요** |
| 도메인별 source=0 | 사규 46.7%, 장애교육 15% | N/A | **분석 필요** |
| Similarity 로깅 | 미구현 | 구현 | **분석 가능** |

### 5.3 정답 지표 현황

| 지표 | 현재 상태 | 필요 작업 |
|------|----------|----------|
| 핵심 포함율 | **미측정** | 골든 답안 기반 자동 평가 필요 |
| 근거 기반 여부 | **미측정** | RAG 소스 인용 정확도 평가 필요 |

---

## 6. 기술적 인사이트

### 6.1 소프트 가드레일 2단계 보호

```
[1단계: 시스템 프롬프트 지침]
LLM에게 유보적 표현만 사용하도록 지시
→ 답변 생성 시점에서 오답 리스크 감소

[2단계: 응답 prefix 추가]
사용자에게 "일반 지식 기반 답변"임을 명시
→ 사용자가 스스로 검증 필요성 인지
```

### 6.2 도메인 키 정규화 전략

```python
# Bad: 하드코딩된 단일 키
DOMAIN_CONTACT_INFO = {"EDU": "..."}  # EDUCATION → DEFAULT 폴백

# Good: 별칭 지원
DOMAIN_CONTACT_INFO = {
    "EDU": "• 교육팀 / HR팀",
    "EDUCATION": "• 교육팀 / HR팀",  # EDU의 별칭
}
```

### 6.3 테스트 정책 일관성

```
[Anti-Pattern]
코드 동작이 변경되었는데 테스트가 이전 동작을 기대
→ 테스트 실패 + 잘못된 회귀 경고

[Best Practice]
정책 변경 시 테스트도 함께 업데이트
→ 테스트가 현재 의도된 동작을 검증
```

---

## 7. 결론

### 7.1 Phase 46 성과 요약

| 항목 | 구현 내용 | 효과 |
|------|----------|------|
| DOMAIN_CONTACT_INFO 키 정렬 | EDUCATION 별칭 추가 | 도메인 불일치 방지 |
| '확정 표현 금지' 규칙 강화 | 상세 지침 시스템 프롬프트에 추가 | 오답 리스크 구조적 감소 |
| 시스템 프롬프트 통합 | soft_guardrail_instruction 파라미터 | LLM이 유보적 표현 사용 |
| 테스트 업데이트 | Phase 44 정책에 맞게 수정 | 테스트 정합성 확보 |

### 7.2 남은 과제

| 과제 | 현황 | 해결 방향 | 우선순위 |
|------|------|----------|---------|
| 소스 포함율 측정 | RAGFlow 연결 필요 | 정상 연결 후 재측정 | **1순위** |
| source=0 원인 분해 | Similarity 로깅 구현됨 | A(커버리지)/B(검색) 분류 | **1순위** |
| 골든 답안 기반 평가 | 미구현 | 정답 지표 자동 평가 구축 | 2순위 |
| 도메인별 문서 보강 | 사규 46.7% source=0 | 문서 업로드/인덱싱 개선 | 2순위 |

### 7.3 Phase 47 권장 작업

1. **RAGFlow 정상 연결** → 소스 포함율 재측정
2. **source=0 원인 분해**:
   - A: KB에 문서/내용이 없음 (커버리지 문제)
   - B: 문서는 있는데 검색이 못 찾음 (인덱싱/청킹/검색 설정 문제)
3. **B로 분류된 것부터 우선 개선**:
   - 사규/규정 문서: "조항 단위 청킹 + 메타데이터(제N조/항/호)" 적용
4. **A는 문서 보강**으로 해결

---

## 부록

### A. 수정 파일 목록 (Phase 46)

| 파일 | 수정 내용 |
|------|----------|
| `app/services/answer_guard_service.py` | EDUCATION 별칭, 확정 표현 금지 규칙 강화 |
| `app/services/chat_service.py` | soft_guardrail_instruction 파라미터 추가 |
| `app/services/chat/message_builder.py` | soft_guardrail_instruction 시스템 프롬프트 통합 |
| `tests/unit/test_phase39_answer_guard.py` | Phase 44 정책에 맞게 테스트 업데이트 |
| `tests/unit/test_phase14_rag_gap_candidate.py` | Phase 44 정책에 맞게 테스트 업데이트 |
| `tests/unit/test_phase22_chat_router_integration.py` | 소프트 가드레일 mock 추가 |
| `tests/unit/test_chat_http_e2e.py` | FakeAnswerGuardService에 메서드 추가 |

### B. 지표 측정 체크리스트

#### Availability 지표 (Phase 46 완료)
- [x] 차단률: 0%
- [x] LANGUAGE_ERROR: 0건
- [ ] 에러율: 측정 중

#### RAG 지표 (Phase 47 필요)
- [ ] 소스 포함율: RAGFlow 연결 후 측정
- [ ] returned_k: Similarity 로그에서 추출
- [ ] similarity 분포: Similarity 로그에서 추출
- [ ] 도메인별 source=0 비율: 분석 필요

#### 정답 지표 (향후 과제)
- [ ] 핵심 포함율: 골든 답안 기반 평가 시스템 필요
- [ ] 근거 기반 여부: RAG 소스 인용 정확도 평가 필요

---

**작성일**: 2025-12-24
**버전**: v4
**작성자**: 모인지
