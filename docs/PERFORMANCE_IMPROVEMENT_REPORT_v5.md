# AI 채팅 서비스 성능 개선 보고서 v5

## 1. 개요

### 1.1 문서 목적
본 보고서는 Phase 47~48 개선 작업을 기술합니다.
- **Phase 47**: GPT 리뷰 피드백 반영 (소프트 가드레일, 도메인 정규화)
- **Phase 48**: Low-relevance Gate 구현 (저관련 검색 결과 강등, dataset_id 필터)

이전 Phase에 대한 상세 내용은 아래를 참조하세요:
- [v1 보고서](./PERFORMANCE_IMPROVEMENT_REPORT.md): Phase 43 (1차~5차, "하" 키워드 버그 수정)
- [v2 보고서](./PERFORMANCE_IMPROVEMENT_REPORT_v2.md): Phase 44 (6차~7차, 가드레일 완화)
- [v3 보고서](./PERFORMANCE_IMPROVEMENT_REPORT_v3.md): Phase 45 (8차, 소프트 가드레일 & 언어 강제)
- [v4 보고서](./PERFORMANCE_IMPROVEMENT_REPORT_v4.md): Phase 46 (소프트 가드레일 강화, 지표 분리 정의)

### 1.2 버전 히스토리
| 버전 | 일시 | 주요 변경 |
|------|------|----------|
| v1 | 2025-12-23 | Phase 43: 1차~5차 테스트, "하" 키워드 버그 수정 |
| v2 | 2025-12-23 | Phase 44: 6차~7차 테스트, 가드레일 완화 |
| v3 | 2025-12-23 | Phase 45: 8차 테스트, 소프트 가드레일 & 언어 강제 |
| v4 | 2025-12-24 | Phase 46: 소프트 가드레일 강화, 지표 분리 정의 |
| v5 | 2025-12-26 | Phase 47: GPT 피드백 반영 (3개 필수 수정) |
| **v5.1** | **2025-12-28** | **Phase 48: Low-relevance Gate + dataset_id 필터** |

### 1.3 Phase 47 피드백 요약

GPT 리뷰에서 v4 보고서에 대해 다음 피드백을 받았습니다:

| 우선순위 | 지적 사항 | 해결 상태 |
|---------|----------|----------|
| **필수** | `'~입니다'` 금지 표현에 포함 → 한국어 기본 서술 종결이므로 제거 필요 | ✅ 해결 |
| **필수** | `soft_guardrail_instruction`이 `build_rag_messages()`에만 적용됨 | ✅ 해결 |
| **필수** | `DOMAIN_CONTACT_INFO`에 시스템 도메인과 교육 주제 카테고리 혼재 | ✅ 해결 |
| **권장** | Similarity 로깅 빈 배열 방어 로직 필요 | ✅ 이미 구현됨 |

---

## 2. Phase 47 구현 상세

### 2.1 필수 수정 1: '~입니다' 금지 표현 제거

**문제**: `'~입니다'`는 한국어 기본 서술 종결어미인데, 이를 금지하면 LLM이 정상적인 문장 형태로 답변하기 어려워짐

**해결**: '회사 기준 확정/근거 주장'만 금지하도록 변경

#### 수정 파일: `app/services/answer_guard_service.py`

```python
def get_soft_guardrail_system_instruction(self) -> str:
    """소프트 가드레일용 시스템 프롬프트 추가 지침.

    Phase 47: '~입니다' 종결어미 금지 제거 (한국어 기본 서술 종결어미)
              → '회사 기준 확정/근거 주장'만 금지하도록 변경
    """
    return (
        "\n\n[중요 지침 - 회사 기준 확정 표현 금지]\n"
        "현재 참고할 사내 문서 근거가 없습니다.\n"
        "따라서 답변 시 다음 규칙을 반드시 따르세요:\n\n"
        "【금지 표현 - 회사 기준 확정/근거 주장】\n"
        "• '회사 규정상', '사규에 따르면', '정책에 따라', '회사 방침으로'\n"
        "• '의무적으로', '반드시', '무조건'\n"
        "• 제N조, 제N항, 제N호 등 구체적 조항 번호 단정 인용\n\n"
        "【허용 표현 - 일반 지식/조건부 표현】\n"
        "• '일반적으로는 ~로 운영되는 경우가 많습니다'\n"
        "• '회사마다 다를 수 있습니다', '~일 수 있습니다'\n"
        "• '통상적으로 ~합니다', '대부분의 경우 ~합니다'\n"
        "• 일반적인 서술형 종결('~입니다', '~합니다')은 사용 가능\n\n"  # <-- 변경점
        ...
    )
```

**핵심 변경**:
- 금지: `'~입니다', '~해야 합니다'` → `'회사 규정상', '사규에 따르면'`
- 허용에 명시: `'~입니다', '~합니다' 사용 가능`

---

### 2.2 필수 수정 2: soft_guardrail_instruction 모든 경로 적용

**문제**: `soft_guardrail_instruction`이 `build_rag_messages()`에만 전달되고, `build_mixed_messages()` 및 `build_backend_api_messages()`에는 누락됨

**해결**: 모든 메시지 빌더 함수에 동일하게 적용

#### 수정 파일 및 내용

| 파일 | 수정 내용 |
|------|----------|
| `app/services/chat_service.py` | `_build_mixed_llm_messages`, `_build_backend_api_llm_messages`에 파라미터 추가 |
| `app/services/chat/message_builder.py` | `build_mixed_messages`, `build_backend_api_messages`에 파라미터 및 로직 추가 |

```python
# chat_service.py (호출부)
if route in mixed_routes:
    llm_messages = self._build_mixed_llm_messages(
        ...
        soft_guardrail_instruction=soft_guardrail_instruction,  # Phase 47
    )
elif route in backend_api_routes:
    llm_messages = self._build_backend_api_llm_messages(
        ...
        soft_guardrail_instruction=soft_guardrail_instruction,  # Phase 47
    )

# message_builder.py (각 빌더 함수)
def build_mixed_messages(
    self,
    ...
    soft_guardrail_instruction: Optional[str] = None,  # Phase 47
) -> List[Dict[str, str]]:
    ...
    # Phase 47: 소프트 가드레일 시스템 지침 추가 (모든 경로 적용)
    if soft_guardrail_instruction:
        system_content = system_content + soft_guardrail_instruction
```

**적용 범위 증명**:
- ✅ `build_rag_messages()` (기존)
- ✅ `build_mixed_messages()` (Phase 47 추가)
- ✅ `build_backend_api_messages()` (Phase 47 추가)

---

### 2.3 필수 수정 3: DOMAIN_CONTACT_INFO 도메인/카테고리 정규화

**문제**: 시스템 도메인(POLICY, EDUCATION, INCIDENT)과 교육 주제 카테고리(PIP, SHP, BHP 등)가 하나의 딕셔너리에 혼재되어 매핑이 흔들릴 수 있음

**해결**: 정규화 함수를 통해 일관되게 처리

#### 수정 내용

**1. 데이터 구조 분리**:
```python
# 시스템 도메인 (라우팅용)
DOMAIN_CONTACT_INFO = {
    "POLICY": "• 인사팀 / 총무팀 (사내 규정 관련)",
    "EDU": "• 교육팀 / HR팀 (교육 관련)",
    "EDUCATION": "• 교육팀 / HR팀 (교육 관련)",  # EDU alias
    "INCIDENT": "• 보안팀 / 감사팀 (사건/사고 관련)",
    "GENERAL": "• 담당 부서에 문의해 주세요.",
    "DEFAULT": "• 담당 부서에 문의해 주세요.",
}

# 교육 주제 카테고리별 담당부서 (dataset/topic용)
TOPIC_CONTACT_INFO = {
    "PIP": "• 개인정보보호팀 (개인정보 관련)",
    "SHP": "• 인사팀 / 고충처리위원회 (성희롱 예방)",
    "BHP": "• 인사팀 / 고충처리위원회 (직장내 괴롭힘)",
    "DEP": "• 인사팀 (장애인 인식개선)",
    "JOB": "• 교육팀 / 해당 부서 (직무교육)",
}
```

**2. 정규화 함수 추가**:
```python
class AnswerGuardService:
    # 교육 주제 카테고리 → 시스템 도메인 매핑
    _TOPIC_TO_DOMAIN_MAP: Dict[str, str] = {
        "PIP": "EDUCATION",
        "SHP": "EDUCATION",
        "BHP": "EDUCATION",
        "DEP": "EDUCATION",
        "JOB": "EDUCATION",
    }

    def normalize_domain_key(self, domain: Optional[str]) -> str:
        """도메인 키를 정규화하여 담당부서 안내에 사용합니다.

        정규화 규칙:
        1. EDUCATION/EDU → EDUCATION
        2. PIP/SHP/BHP/DEP/JOB → EDUCATION (교육 주제 카테고리)
        3. POLICY/INCIDENT/GENERAL → 그대로 유지
        4. None 또는 알 수 없는 값 → DEFAULT
        """
        ...

    def get_contact_info(self, domain: Optional[str], topic: Optional[str] = None) -> str:
        """도메인/토픽에 맞는 담당부서 안내를 반환합니다.

        - 토픽이 있으면 토픽 기준 (더 구체적인 안내)
        - 없으면 도메인 기준
        """
        ...
```

**3. check_soft_guardrail에 topic 파라미터 추가**:
```python
def check_soft_guardrail(
    self,
    intent: Tier0Intent,
    sources: List[ChatSource],
    domain: Optional[str] = None,
    topic: Optional[str] = None,  # Phase 47 추가
) -> Tuple[bool, Optional[str]]:
    ...
    # Phase 47: 정규화된 담당부서 안내 구성
    contact_info = self.get_contact_info(domain=domain, topic=topic)
```

---

### 2.4 권장 수정: Similarity 로깅 빈 배열 방어

**확인 결과**: 이미 `log_similarity_distribution()` 함수에 방어 로직이 구현되어 있음

```python
# app/services/chat/rag_handler.py:91-98
scores = [s.score for s in sources if s.score is not None]
if not scores:
    logger.info(
        f"[Similarity] {search_stage}: {len(sources)} results (no scores) | "
        f"domain={domain}, query='{query[:30]}...'"
    )
    return  # 빈 배열일 때 안전하게 리턴
```

---

## 3. 수정 파일 목록 (Phase 47)

| 파일 | 수정 내용 |
|------|----------|
| `app/services/answer_guard_service.py` | 금지 표현 변경, 정규화 함수 추가, TOPIC_CONTACT_INFO 분리 |
| `app/services/chat_service.py` | `_build_mixed_llm_messages`, `_build_backend_api_llm_messages`에 soft_guardrail 파라미터 추가 |
| `app/services/chat/message_builder.py` | `build_mixed_messages`, `build_backend_api_messages`에 soft_guardrail 로직 추가 |

---

## 4. 기술적 인사이트

### 4.1 서술형 종결어미 vs 확정 표현

```
[Anti-Pattern] Phase 46
'~입니다'를 금지 → LLM이 불완전한 문장을 생성하거나 어색한 표현 사용

[Best Practice] Phase 47
'~입니다', '~합니다' 등 서술형 종결어미 허용
'회사 규정상', '사규에 따르면' 등 확정적 근거 주장만 금지
```

### 4.2 모든 경로에 일관된 가드레일 적용

```
[Anti-Pattern] Phase 46
soft_guardrail_instruction이 RAG 경로에만 적용
→ MIXED_BACKEND_RAG, BACKEND_API 경로에서는 적용 안됨

[Best Practice] Phase 47
시스템 프롬프트를 생성하는 모든 경로에 동일하게 적용:
- build_rag_messages() ✓
- build_mixed_messages() ✓
- build_backend_api_messages() ✓
```

### 4.3 도메인/토픽 2단계 정규화

```
[입력값]                    [정규화]              [담당부서 안내]
EDUCATION, EDU          →  EDUCATION          →  교육팀 / HR팀
PIP, SHP, BHP, DEP, JOB →  EDUCATION (도메인)  →  교육팀 / HR팀
                           PIP (토픽)          →  개인정보보호팀 (더 구체적)
POLICY                  →  POLICY             →  인사팀 / 총무팀
INCIDENT                →  INCIDENT           →  보안팀 / 감사팀
```

---

## 5. 지표 체계 현황

### 5.1 Phase 47은 '안전/표현 통제' 단계

**중요**: Phase 47은 **코드 품질/안정성 개선** 단계이며, **RAG 품질 향상(소스 포함율↑)**은 Phase 48 이후에서 다룹니다.

| 지표 카테고리 | Phase 46까지 | Phase 47 |
|-------------|-------------|----------|
| **Availability** | 차단률 0%, LANGUAGE_ERROR 0건 | 유지 |
| **RAG** | 소스 포함율 63.1% (7차 기준) | **측정 대기** |
| **정답** | 미측정 | **측정 대기** |

### 5.2 Phase 48 권장 작업

1. **RAGFlow 정상 연결** → 소스 포함율 재측정 (9차 테스트)
2. **source=0 원인 분해**:
   - A: KB에 문서/내용 없음 (커버리지 문제) → 문서 보강
   - B: 문서는 있는데 검색 실패 (인덱싱/청킹 문제) → 검색 설정 개선
3. **B부터 개선**: 청킹/메타데이터/쿼리 튜닝 → 소스 포함율 재측정
4. **골든 답안 기반 평가**: 정답 지표 자동 평가 구축

---

## 6. Phase 48: Low-relevance Gate

### 6.1 배경 및 목적

prompt.txt에서 제시된 RAG 품질 문제:
> "문서에 실제로 없는 주제(연차/근태)를 물었을 때도, RAG가 억지로 TopK를 반환해서 '근거가 있는 것처럼' 보이게 만드는 문제"

**핵심 목표**: 저품질 검색 결과를 sources=[]로 강등 → soft guardrail 정상 발동

### 6.2 구현 내용

#### 6.2.1 Low-relevance Gate (rag_handler.py)

두 단계 게이트로 저관련 결과 필터링:

```
┌─────────────────────────────────────────────────────────┐
│                   RAG 검색 결과                          │
└──────────────────────┬──────────────────────────────────┘
                       ▼
              ┌────────────────┐
              │  Gate A:       │    max_score < 0.60?
              │  Score Gate    │ ──────────────────────→ sources=[]
              └───────┬────────┘                         (강등)
                      │ 통과
                      ▼
              ┌────────────────┐
              │  Gate B:       │    앵커 키워드가
              │  Anchor Gate   │    sources에 없음?
              └───────┬────────┘ ─────────────────────→ sources=[]
                      │ 통과                             (강등)
                      ▼
              ┌────────────────┐
              │   최종 결과     │
              └────────────────┘
```

**구현 함수**:
- `get_anchor_stopwords()`: 불용어 세트 로드 (config에서)
- `extract_anchor_keywords(query)`: 쿼리에서 핵심 키워드 추출
- `check_anchor_keywords_in_sources()`: 키워드가 sources 텍스트에 있는지 확인
- `apply_low_relevance_gate()`: 두 게이트 적용

**예시**:
```python
# 쿼리: "연차 규정 알려줘"
# 불용어: ["규정", "알려줘", "관련", "문서", ...]
# 앵커 키워드: {"연차"}

# 만약 sources에 "연차"가 없으면 → sources=[] 강등
# → soft guardrail 발동 → "근거 없음" 안내
```

#### 6.2.2 Config 설정 (config.py)

```python
# Phase 48: Low-relevance Gate 설정
RAG_MIN_MAX_SCORE: float = 0.60  # max_score 임계값
RAG_ANCHOR_STOPWORDS: str = (    # 불용어 목록 (쉼표 구분)
    "관련,규정,정책,문서,요약,알려줘,뭐야,해줘,해주세요,있어,없어,어떻게,"
    "무엇,뭔가,좀,을,를,이,가,은,는,의,에,에서,로,으로,와,과,하고,그리고,"
    "또는,및,대한,대해,대해서,것,수,등,내용,사항,부분,전체,모든,각,해당"
)
RAG_DATASET_FILTER_ENABLED: bool = True  # dataset_id 필터 활성화
```

#### 6.2.3 domain → dataset_id 필터 (milvus_client.py)

Milvus 검색 시 domain에 따라 dataset_id 필터를 강제 적용:

```python
DOMAIN_DATASET_MAPPING = {
    "POLICY": "사내규정",
    "EDUCATION": "정보보안교육",
}

def get_dataset_filter_expr(domain: str) -> Optional[str]:
    # POLICY → 'dataset_id == "사내규정"'
    # EDUCATION → 'dataset_id == "정보보안교육"'
```

**효과**: 도메인별로 검색 범위를 제한하여 관련 없는 문서가 섞이는 것을 방지

### 6.3 테스트 결과

```
=== Test 4: Low-relevance Gate ===
[PASS] High score + keyword match -> PASSED: 2 sources
[PASS] Low score -> DEMOTED: reason=max_score_below_threshold
[PASS] Keyword mismatch -> DEMOTED: reason=no_anchor_term_match
[PASS] Empty sources -> stays empty: 0

=== Test 5: Dataset Filter Expression ===
[PASS] POLICY -> filter=dataset_id == "사내규정"
[PASS] EDUCATION -> filter=dataset_id == "정보보안교육"
[PASS] GENERAL -> filter=None
```

### 6.4 Live API 테스트 및 임계값 분석

실제 API 호출을 통해 Low-relevance Gate 동작과 임계값 적정성을 검증했습니다.

#### 6.4.1 Live 테스트 결과

| 쿼리 | Domain | Sources | Soft Guardrail | 판정 |
|------|--------|---------|----------------|------|
| 연차 규정 알려줘 | POLICY | **0개** | 발동 | KB에 없음 → 정상 강등 |
| 근태 관련 문서 보여줘 | POLICY | **0개** | 발동 | KB에 없음 → 정상 강등 |
| 징계 규정 알려줘 | POLICY | **5개** | - | KB에 있음 → 정상 통과 |
| 개인정보보호 교육 내용 | EDUCATION | **5개** | - | KB에 있음 → 정상 통과 |

#### 6.4.2 Score 분포 분석

| 쿼리 | Top Score | 임계값 0.60 | 결과 |
|------|-----------|-------------|------|
| 징계 규정 | **0.665** | 통과 | 5개 sources 반환 |
| 개인정보보호 교육 | **0.786** | 통과 | 5개 sources 반환 |
| 복무 규정 | - | - | 0개 (검색 결과 없음) |
| 보안 교육 | - | - | 0개 (검색 결과 없음) |
| 연차/근태 | - | - | 0개 (KB에 없음) |

#### 6.4.3 임계값 튜닝 분석

```
현재 임계값: 0.60
  ├─ 징계 규정 (0.665) → 통과 ✅
  ├─ 개인정보보호 (0.786) → 통과 ✅
  └─ KB에 없는 쿼리 → 강등 ✅

만약 0.65로 올리면:
  └─ 징계 규정 (0.665) → 겨우 통과 (위험)

만약 0.55로 내리면:
  └─ 저품질 결과가 통과할 위험 ↑
```

**결론: 0.60 유지 권장**
- 현재 유효한 결과(징계, 개인정보보호)는 통과
- KB에 없는 쿼리는 정상 강등
- 운영 데이터 더 축적 후 재평가 권장

### 6.5 수정 파일 목록

| 파일 | 수정 내용 |
|------|----------|
| `app/core/config.py` | RAG_MIN_MAX_SCORE, RAG_ANCHOR_STOPWORDS, RAG_DATASET_FILTER_ENABLED 추가 |
| `app/services/chat/rag_handler.py` | Low-relevance Gate 함수 4개 추가, perform_search_with_fallback에 게이트 적용 |
| `app/clients/milvus_client.py` | DOMAIN_DATASET_MAPPING, get_dataset_filter_expr() 추가, search_as_sources에 필터 적용 |
| `scripts/test_phase48_changes.py` | Phase 48 테스트 스크립트 신규 작성 |

---

## 7. 결론

### 7.1 Phase 47 성과 요약

| 항목 | 구현 내용 | 효과 |
|------|----------|------|
| '~입니다' 종결어미 허용 | 금지 표현에서 제거, 허용 표현에 명시 | LLM이 자연스러운 한국어 문장 생성 |
| 소프트 가드레일 전 경로 적용 | MIXED, BACKEND_API 경로에도 적용 | 모든 응답에 일관된 가드레일 |
| 도메인/토픽 정규화 | 정규화 함수 + 2단계 매핑 | 안정적인 담당부서 안내 |
| Similarity 로깅 방어 | 이미 구현됨 확인 | 런타임 안전성 보장 |

### 7.2 Phase 48 성과 요약

| 항목 | 구현 내용 | 효과 |
|------|----------|------|
| Score Hard Gate | max_score < 0.60 → sources=[] | 저품질 검색 결과 자동 강등 |
| Anchor Keyword Gate | 핵심 키워드가 sources에 없으면 → sources=[] | "없는 주제" 질문 시 근거 없음 처리 |
| dataset_id 필터 | domain → dataset_id 매핑으로 검색 범위 제한 | 도메인별 문서 분리 검색 |
| Config 설정화 | 임계값/불용어를 config에서 관리 | 운영 중 튜닝 용이 |

### 7.3 Phase 흐름 정리

```
Phase 43~46: 기능 구현 + 버그 수정
Phase 47:    코드 안정성 + 엣지 케이스 처리 + 구조 개선
             (GPT 리뷰 피드백 반영)
Phase 48:    RAG 품질 개선 - 저관련 검색 결과 강등
             (Low-relevance Gate + dataset 필터)
```

### 7.4 다음 단계 (Phase 49 권장)

1. **실제 운영 로그 분석**: Low-relevance Gate 강등 비율 모니터링
2. **임계값 튜닝**: RAG_MIN_MAX_SCORE 값을 운영 데이터 기반으로 조정
3. **하이브리드 검색**: sparse(BM25) + dense 하이브리드 검색 도입 검토
4. **리랭킹**: 검색 결과 정렬 품질 향상을 위한 reranker 도입 검토

---

**작성일**: 2025-12-28
**버전**: v5.1
**작성자**: Claude Code
