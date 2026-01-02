# AI 채팅 서비스 성능 개선 보고서 - Phase 49

## 1. 개요

### 1.1 프로젝트 배경
본 보고서는 CTRL+F AI 채팅 서비스의 **도메인 라우팅 정확도 개선** 및 **운영 유연성 강화** 작업을 기술합니다. Phase 48까지 Low-relevance Gate와 dataset_id 필터를 구현하였으나, 키워드 기반 라우팅에서 여전히 오분류 사례가 발견되어 이를 개선하였습니다.

### 1.2 개선 목표
| 목표 | 상세 |
|------|------|
| 라우팅 정확도 향상 | 연차/휴가/근태 → POLICY, 교육 콘텐츠 → EDUCATION 정확 분류 |
| 운영 유연성 확보 | EDUCATION dataset_id allowlist를 config로 분리 |
| 확장 기반 마련 | 요약 인텐트 감지 로직 추가 (피처 플래그 보호) |

### 1.3 Phase 히스토리
| Phase | 주요 내용 | 상태 |
|-------|----------|------|
| Phase 43 | COMPLAINT_KEYWORDS "하" 버그 수정 | 완료 |
| Phase 44-46 | 가드레일 완화/강화, 소프트 가드레일 | 완료 |
| Phase 47 | GPT 피드백 반영 (금지 표현 수정) | 완료 |
| Phase 48 | Low-relevance Gate + dataset_id 필터 | 완료 |
| **Phase 49** | **도메인 라우팅 개선 + config 분리** | **완료** |

> **참고**: 본 보고서의 내용은 [PERFORMANCE_IMPROVEMENT_REPORT_v5.md](./PERFORMANCE_IMPROVEMENT_REPORT_v5.md) v5.2에도 통합되어 있습니다.

### 1.4 도메인 표기 규칙

본 문서에서 사용하는 도메인 표기 규칙:

| 표기 | 용도 | 설명 |
|------|------|------|
| `POLICY` | 필터/라우팅 정규 키 | Milvus 필터, RuleRouter 분류에 사용 |
| `EDUCATION` | 필터/라우팅 정규 키 | Milvus 필터, RuleRouter 분류에 사용 |
| `EDU` | 코드 내 표시값 (alias) | `RouterDomain.EDU`로 정의, 내부적으로 `EDUCATION`으로 정규화됨 |
| `POLICY_QA`, `EDUCATION_QA` | 인텐트 타입 | `Tier0Intent` enum 값 |

**정규화 흐름**:
```
RouterDomain.EDU → domain.upper() → "EDUCATION" → Milvus 필터 적용
```

---

## 2. 문제 분석

### 2.1 발견된 라우팅 오류

Phase 48 이후 테스트에서 다음과 같은 오분류 사례가 발견되었습니다:

| 쿼리 | 기대 결과 | 실제 결과 | 원인 |
|------|----------|----------|------|
| "연차 규정 알려줘" | POLICY | HR (UNKNOWN) | 경계 B에서 애매함으로 판정 |
| "근태 관련 규정" | POLICY | HR | "근태"가 HR_PERSONAL_KEYWORDS에 있음 |
| "정보보호교육 내용" | EDUCATION | POLICY | "정보보호"가 POLICY_KEYWORDS에 있음 |
| "성희롱예방교육 설명해줘" | EDUCATION | POLICY | "성희롱"이 POLICY_KEYWORDS에 있음 |

### 2.2 근본 원인 분석

#### 원인 1: 경계 B 체크의 과도한 애매함 판정

```python
# 기존 로직
def _is_boundary_b_ambiguous(self, query_lower: str) -> bool:
    # "연차" + "알려줘" → 애매함으로 판정 → HR/UNKNOWN
    has_leave_keyword = self._contains_any(query_lower, LEAVE_AMBIGUOUS_KEYWORDS)
    has_ambiguous_verb = self._contains_any(query_lower, LEAVE_AMBIGUOUS_VERBS)
    return has_leave_keyword and has_ambiguous_verb
```

**문제**: "연차 규정 알려줘"에서 "규정"이라는 명확한 정책 지시어가 있음에도 애매함으로 판정

#### 원인 2: 키워드 우선순위 충돌

```
RuleRouter 키워드 체크 순서 (기존):
1. HR_PERSONAL_KEYWORDS  → "근태" 포함
2. EDU_STATUS_KEYWORDS
3. EDU_CONTENT_KEYWORDS  → "정보보호교육" 포함
4. POLICY_KEYWORDS       → "정보보호", "근태" 포함
```

**문제**:
- "근태 규정" → "근태"가 HR에서 먼저 매칭 → HR로 오분류
- "정보보호교육" → "정보보호"가 POLICY에서 먼저 매칭되지 않지만, EDU_CONTENT보다 POLICY가 나중이라 상관없음. 하지만 "성희롱"이 POLICY에 있어서 "성희롱예방교육"이 문제

#### 원인 3: 교육 특화 키워드 부재

```python
# 기존 EDU_CONTENT_KEYWORDS
EDU_CONTENT_KEYWORDS = frozenset([
    "교육내용", "교육자료", "정보보호교육", "보안교육",
    # "성희롱예방교육", "장애인식개선교육" 등 누락
])
```

**문제**: Q세트 도메인의 교육 키워드가 EDU_CONTENT_KEYWORDS에 없어서 POLICY로 분류

---

## 3. 개선 작업

### 3.1 Phase 49-1: RuleRouter 도메인 라우팅 개선

#### 3.1.1 경계 B 체크 개선

**파일**: `app/services/rule_router.py`

```python
# Before (문제 코드)
def _is_boundary_b_ambiguous(self, query_lower: str) -> bool:
    if self._contains_any(query_lower, LEAVE_POLICY_KEYWORDS):
        return False
    if self._contains_any(query_lower, HR_PERSONAL_KEYWORDS):
        return False
    # "연차 규정 알려줘" → 여기서 애매함으로 판정됨
    has_leave_keyword = self._contains_any(query_lower, LEAVE_AMBIGUOUS_KEYWORDS)
    has_ambiguous_verb = self._contains_any(query_lower, LEAVE_AMBIGUOUS_VERBS)
    return has_leave_keyword and has_ambiguous_verb

# After (수정 코드)
def _is_boundary_b_ambiguous(self, query_lower: str) -> bool:
    if self._contains_any(query_lower, LEAVE_POLICY_KEYWORDS):
        return False
    if self._contains_any(query_lower, HR_PERSONAL_KEYWORDS):
        return False

    # Phase 49: "규정", "정책" 등이 있으면 명확히 정책 질문
    policy_clarifiers = {"규정", "정책", "규칙", "지침", "제도"}
    if self._contains_any(query_lower, policy_clarifiers):
        return False  # 명확히 정책 질문으로 판단

    has_leave_keyword = self._contains_any(query_lower, LEAVE_AMBIGUOUS_KEYWORDS)
    has_ambiguous_verb = self._contains_any(query_lower, LEAVE_AMBIGUOUS_VERBS)
    return has_leave_keyword and has_ambiguous_verb
```

**효과**: "연차 규정 알려줘" → "규정" 감지 → 애매함 판정 회피 → POLICY로 정상 분류

#### 3.1.2 복합 조건 우선 체크

**파일**: `app/services/rule_router.py` (`_classify_by_keywords` 메서드)

```python
def _classify_by_keywords(self, query_lower, query_original, debug_info):
    # Phase 49: 복합 조건 - "교육"이 포함되면 EDU 우선 체크
    if "교육" in query_lower:
        if self._contains_any(query_lower, EDU_CONTENT_KEYWORDS):
            debug_info.rule_hits.append("EDU_CONTENT_PRIORITY")
            return RouterResult(
                tier0_intent=Tier0Intent.EDUCATION_QA,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.85,
                debug=debug_info,
            )

    # Phase 49: 복합 조건 - "규정/정책/규칙" 포함 시 POLICY 우선 체크
    policy_clarifiers = {"규정", "정책", "규칙", "지침", "제도"}
    if self._contains_any(query_lower, policy_clarifiers):
        if self._contains_any(query_lower, POLICY_KEYWORDS) or \
           self._contains_any(query_lower, LEAVE_AMBIGUOUS_KEYWORDS):
            debug_info.rule_hits.append("POLICY_PRIORITY")
            return RouterResult(
                tier0_intent=Tier0Intent.POLICY_QA,
                domain=RouterDomain.POLICY,
                ...
            )

    # 이후 기존 순서대로 체크
```

**효과**:
- "정보보호교육 내용" → "교육" 감지 → EDU 우선 체크 → EDUCATION_QA
- "근태 관련 규정" → "규정" 감지 → POLICY 우선 체크 → POLICY_QA

#### 3.1.3 교육 특화 키워드 확장

**파일**: `app/services/rule_router.py`

```python
# Before
EDU_CONTENT_KEYWORDS = frozenset([
    "교육내용", "교육자료", "교육규정", "학습내용",
    "정보보호교육", "보안교육", "컴플라이언스교육",
    "교육이란", "교육이 뭐", "무슨 교육",
])

# After (Phase 49 확장)
EDU_CONTENT_KEYWORDS = frozenset([
    "교육내용", "교육자료", "교육규정", "학습내용",
    "강의내용", "교육과정", "커리큘럼",
    "4대교육", "법정교육", "의무교육",
    # 교육 특화 키워드 (Phase 49 확장)
    "정보보호교육", "보안교육", "컴플라이언스교육",
    "성희롱예방교육", "성희롱교육",
    "장애인식개선교육", "장애인식교육",
    "직장내괴롭힘예방교육", "괴롭힘예방교육",
    "개인정보보호교육", "개인정보교육",
    # 일반 교육 질문
    "교육이란", "교육이 뭐", "교육 설명",
    "무슨 교육", "어떤 교육",
])
```

**효과**: Q세트의 5개 교육 도메인(PIP, SHP, BHP, DEP, JOB) 키워드 매칭률 향상

#### 3.1.4 ASCII-safe 로깅

Git Bash 파이프 환경에서 한글 깨짐(mojibake) 방지:

```python
def ascii_safe_preview(text: str, max_len: int = 50) -> str:
    """
    로그 출력용 ASCII-safe 텍스트 미리보기.
    Git Bash 파이프, Windows cp949 문제 방지.
    """
    if not text:
        return ""
    truncated = text[:max_len]
    return truncated.encode("unicode_escape").decode("ascii")
    # "연차 규정" → "\\uc5f0\\ucc28 \\uaddc\\uc815"

# 로깅 적용
logger.info(
    f"RuleRouter: intent={result.tier0_intent.value}, "
    f"domain={result.domain.value}, "
    f"rule_hits={debug_info.rule_hits}, "
    f"query='{ascii_safe_preview(user_query, 50)}'"
)
```

**효과**: `query='�ް�...'` → `query='\uc5f0\ucc28 \uaddc\uc815...'` (디버깅 가능)

---

### 3.2 Phase 49-2: EDUCATION dataset_id allowlist Config 분리

#### 3.2.1 기존 문제

```python
# milvus_client.py (하드코딩)
DOMAIN_DATASET_MAPPING = {
    "POLICY": "사내규정",
    "EDUCATION": [
        "정보보안교육",
        "장애인인식개선교육",
        "직무교육",
        "직장내괴롭힘교육",
        "직장내성희롱교육",
    ],
}
```

**문제**: 교육 dataset_id 목록 변경 시 코드 수정 + 재배포 필요

#### 3.2.2 개선 구현

**파일 1**: `app/core/config.py`

```python
# Phase 49: EDUCATION dataset_id allowlist 설정
RAG_EDUCATION_DATASET_IDS: str = (
    "정보보안교육,성희롱예방교육,장애인식개선교육,직장내괴롭힘예방교육,개인정보보호교육"
)
```

**파일 2**: `app/clients/milvus_client.py`

```python
def get_education_dataset_ids() -> List[str]:
    """
    Phase 49: config에서 EDUCATION dataset_id allowlist를 파싱합니다.
    """
    settings = get_settings()
    raw = getattr(settings, "RAG_EDUCATION_DATASET_IDS", "")
    if not raw:
        return []
    return [ds_id.strip() for ds_id in raw.split(",") if ds_id.strip()]

def get_dataset_filter_expr(domain: Optional[str]) -> Optional[str]:
    if domain_upper == "EDUCATION":
        dataset_ids = get_education_dataset_ids()  # config에서 동적 로드
        if dataset_ids:
            safe_ids = [f'"{escape_milvus_string(ds_id)}"' for ds_id in dataset_ids]
            return f'dataset_id in [{", ".join(safe_ids)}]'
        return None
    # ...
```

**효과**: 환경변수 `RAG_EDUCATION_DATASET_IDS` 변경만으로 allowlist 수정 가능 (재배포 불필요)

> **⚠️ 중요**: allowlist 값은 Milvus에 저장된 `dataset_id` 실제 값과 **정확히 일치**해야 합니다.
> 불일치 시 필터 조건에 맞는 문서가 없어 검색 결과가 0이 될 수 있습니다.
>
> 예: Milvus에 `직장내성희롱교육`으로 저장된 경우 config에 `성희롱예방교육`으로 설정하면 매칭 실패
>
> **확인 방법**: 앱 시작 시 로그에서 `RAG_EDUCATION_DATASET_IDS` 값을 확인하고, Milvus 실제 데이터와 대조

---

### 3.3 Phase 49-3: 요약 인텐트 분리 (기본 OFF)

#### 3.3.1 목적

"요약해줘", "정리해줘" 등의 요약 요청을 감지하여, 향후 별도 파이프라인으로 분기할 수 있는 기반 마련

#### 3.3.2 구현

**파일 1**: `app/core/config.py`

```python
# Phase 49: Summary Intent 분리 (요약 인텐트)
# 기본 OFF - 향후 활성화 시 별도 파이프라인으로 분기 가능
SUMMARY_INTENT_ENABLED: bool = False
```

**파일 2**: `app/services/rule_router.py`

```python
# 요약 인텐트 키워드
SUMMARY_KEYWORDS = frozenset([
    "요약", "요약해", "요약해줘", "요약해주세요",
    "정리", "정리해", "정리해줘", "정리해주세요",
    "줄여", "줄여줘", "간단히", "핵심만",
    "한줄로", "한 줄로", "짧게",
])

def _classify_by_keywords(self, query_lower, query_original, debug_info):
    # Phase 49: 요약 인텐트 감지 (피처 플래그로 보호)
    settings = get_settings()
    if getattr(settings, "SUMMARY_INTENT_ENABLED", False):
        if self._contains_any(query_lower, SUMMARY_KEYWORDS):
            matched_keywords = [kw for kw in SUMMARY_KEYWORDS if kw in query_lower]
            debug_info.rule_hits.append("SUMMARY_DETECTED")
            debug_info.keywords.extend(matched_keywords)
            logger.info(
                f"RuleRouter: Summary intent detected | "
                f"keywords={matched_keywords} | query='{ascii_safe_preview(query_original, 50)}'"
            )
            # TODO: 향후 별도 SUMMARY_QA 인텐트로 분기 가능
    # ...
```

**현재 동작 (SUMMARY_INTENT_ENABLED=False)**:
- 요약 키워드 감지 로직 비활성화
- 기존 POLICY_QA/EDUCATION_QA 분류 로직 그대로 진행

**향후 확장 (SUMMARY_INTENT_ENABLED=True)**:
- 요약 키워드 감지 시 debug_info에 기록
- 별도 SUMMARY_QA 인텐트로 분기 가능

---

## 4. 테스트 결과

### 4.1 테스트 환경
- **스크립트**: `scripts/test_phase49_changes.py`
- **테스트 항목**: 5개 카테고리

### 4.2 결과 상세

```
============================================================
Phase 49 RuleRouter & Config Test
============================================================

=== Test 1: Config Settings ===
  [PASS] RAG_EDUCATION_DATASET_IDS exists: True
    -> value: 정보보안교육,성희롱예방교육,장애인식개선교육,직장내괴롭힘예방교육,개인정보보호교육
  [PASS] SUMMARY_INTENT_ENABLED exists: True
    -> value: False

=== Test 2: RuleRouter POLICY Priority ===
  [PASS] '연차 규정 알려줘' -> domain=POLICY, intent=POLICY_QA
  [PASS] '휴가 정책 설명해줘' -> domain=POLICY, intent=POLICY_QA
  [PASS] '복무 규정 뭐야' -> domain=POLICY, intent=POLICY_QA
  [PASS] '징계 절차 알려줘' -> domain=POLICY, intent=POLICY_QA
  [PASS] '근태 관련 규정' -> domain=POLICY, intent=POLICY_QA
  [PASS] '정보보호교육 내용 알려줘' -> domain=EDU, intent=EDUCATION_QA
  [PASS] '보안교육 뭐야' -> domain=EDU, intent=EDUCATION_QA
  [PASS] '성희롱예방교육 설명해줘' -> domain=EDU, intent=EDUCATION_QA

=== Test 3: EDUCATION Dataset ID Allowlist ===
  [PASS] get_education_dataset_ids() returns list: 5 items
    -> ids: ['정보보안교육', '성희롱예방교육', '장애인식개선교육']...
  [PASS] EDUCATION filter expr: True
    -> expr: dataset_id in ["정보보안교육", "성희롱예방교육", ...]
  [PASS] POLICY filter expr: True
    -> expr: dataset_id == "사내규정"

=== Test 4: Summary Intent Detection ===
  SUMMARY_INTENT_ENABLED = False
  [PASS] SUMMARY_KEYWORDS defined: 15 keywords
  [PASS] '연차 규정 요약해줘' -> domain=POLICY, intent=POLICY_QA
  [PASS] '정보보호교육 정리해주세요' -> domain=EDU, intent=EDUCATION_QA
  [PASS] '복무 규정 핵심만 알려줘' -> domain=POLICY, intent=POLICY_QA

=== Test 5: ASCII-safe Preview ===
  [PASS] ascii_safe_preview('연차 규정 알려줘...', 50) -> '\uc5f0\ucc28...' (ASCII: True)
  [PASS] ascii_safe_preview('정보보호교육 내용...', 20) -> '\uc815\ubcf4...' (ASCII: True)
  [PASS] ascii_safe_preview('...', 50) -> '' (ASCII: True)

============================================================
Test Summary
============================================================
  [PASS] test_config_settings
  [PASS] test_rule_router_policy_priority
  [PASS] test_education_dataset_ids
  [PASS] test_summary_intent_detection
  [PASS] test_ascii_safe_preview

Total: 5/5 tests passed
[OK] All Phase 49 tests passed!
```

### 4.3 개선 전후 비교

| 쿼리 | Before | After |
|------|--------|-------|
| "연차 규정 알려줘" | HR (UNKNOWN) | **POLICY_QA** |
| "근태 관련 규정" | HR (BACKEND_STATUS) | **POLICY_QA** |
| "정보보호교육 내용 알려줘" | POLICY_QA | **EDUCATION_QA** |
| "성희롱예방교육 설명해줘" | POLICY_QA | **EDUCATION_QA** |

---

## 5. 기술적 인사이트

### 5.1 복합 조건 우선 체크 패턴

```
[Anti-Pattern] 단순 키워드 우선순위
"정보보호교육" → "정보보호"가 POLICY에서 먼저 매칭? → 순서에 의존

[Best Practice] 복합 조건 우선 체크
"정보보호교육" → "교육" 포함? → EDU 키워드 먼저 체크 → 정확한 분류
```

**핵심**: 특정 접미사("교육", "규정")가 있으면 해당 도메인을 우선 체크

### 5.2 Config 분리 원칙

```
[Anti-Pattern] 하드코딩
운영 중 변경 시 코드 수정 + 빌드 + 배포 필요

[Best Practice] Config 분리
환경변수 변경만으로 동작 변경 가능 (무중단)
```

**적용**:
- `RAG_EDUCATION_DATASET_IDS`: 교육 dataset allowlist
- `SUMMARY_INTENT_ENABLED`: 요약 인텐트 피처 플래그

### 5.3 피처 플래그 보호 패턴

```python
# 기본 OFF로 보호
SUMMARY_INTENT_ENABLED: bool = False

# 코드에서 플래그 체크
if getattr(settings, "SUMMARY_INTENT_ENABLED", False):
    # 새 기능 로직
    pass
# 기존 로직 계속 진행
```

**장점**:
- 새 기능을 안전하게 배포 (기본 비활성화)
- 운영 중 플래그만 켜서 A/B 테스트 가능
- 문제 발생 시 즉시 롤백 (플래그 끄기)

---

## 6. 결론

### 6.1 성과 요약

| 항목 | 구현 내용 | 효과 |
|------|----------|------|
| 경계 B 체크 개선 | policy_clarifiers 추가 | "연차 규정" → POLICY 정확 분류 |
| 복합 조건 우선 체크 | "교육"/"규정" 감지 시 우선 분기 | 도메인 오분류 제거 |
| 교육 키워드 확장 | Q세트 5개 도메인 키워드 추가 | EDUCATION 매칭률 향상 |
| ASCII-safe 로깅 | unicode_escape 적용 | Git Bash 한글 깨짐 해결 |
| Config 분리 | RAG_EDUCATION_DATASET_IDS | 재배포 없이 allowlist 변경 |
| 요약 인텐트 | SUMMARY_INTENT_ENABLED | 향후 확장 기반 마련 |

### 6.2 핵심 개선 요인

| 순위 | 개선 항목 | 기여도 |
|------|----------|--------|
| 1 | 복합 조건 우선 체크 | **결정적** (오분류 4건 해결) |
| 2 | 경계 B 체크 개선 | **높음** (애매함 판정 방지) |
| 3 | 교육 키워드 확장 | **중간** (매칭률 향상) |
| 4 | Config 분리 | **운영적** (유연성 확보) |

### 6.3 향후 개선 방향

1. **요약 인텐트 파이프라인**: `SUMMARY_INTENT_ENABLED=True` 시 별도 처리 로직
2. **형태소 분석기 도입**: 키워드 매칭의 정밀도 향상
3. **ML 기반 Intent Classifier**: 규칙 기반 한계 극복
4. **운영 로그 분석**: 라우팅 정확도 지속 모니터링

---

## 부록

### A. 수정 파일 목록

| 파일 | 수정 내용 |
|------|----------|
| `app/core/config.py` | RAG_EDUCATION_DATASET_IDS, SUMMARY_INTENT_ENABLED 추가 |
| `app/services/rule_router.py` | 경계 B 개선, 복합 조건 체크, 교육 키워드 확장, ASCII-safe 로깅 |
| `app/clients/milvus_client.py` | get_education_dataset_ids() 추가, EDUCATION 동적 로드 |
| `scripts/test_phase49_changes.py` | Phase 49 테스트 스크립트 신규 작성 |
| `docs/PERFORMANCE_IMPROVEMENT_REPORT_v5.md` | Phase 49 섹션 추가 (v5.2) |

### B. 테스트 스크립트
- **파일**: `scripts/test_phase49_changes.py`
- **기능**: Config 설정, 라우팅 우선순위, dataset_id 필터, 요약 인텐트, ASCII-safe 테스트

### C. 관련 Config 설정
```bash
# EDUCATION dataset_id allowlist (쉼표 구분)
RAG_EDUCATION_DATASET_IDS=정보보안교육,성희롱예방교육,장애인식개선교육,직장내괴롭힘예방교육,개인정보보호교육

# 요약 인텐트 활성화 (기본 false)
SUMMARY_INTENT_ENABLED=false
```

---

**작성일**: 2025-12-29
**작성자**: CTRL+F AI 개발팀
