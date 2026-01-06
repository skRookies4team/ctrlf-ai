# AI 채팅 서비스 성능 개선 보고서 - Phase 50

## 1. 개요

### 1.1 프로젝트 배경
본 보고서는 CTRL+F AI 채팅 서비스의 **LowRelevanceGate 개선** 작업을 기술합니다. Phase 48에서 도입된 Low-relevance Gate가 특정 쿼리에서 모든 RAG 소스를 필터링하여 "관련 문서를 찾지 못했습니다" 응답이 발생하는 문제가 있었습니다. 이를 해결하기 위해 Gate 로직을 개선하고 안전장치를 추가하였습니다.

### 1.2 개선 목표
| 목표 | 상세 |
|------|------|
| anchor_keywords 정확도 향상 | 행동 표현("요약해줘", "알려줘") 제거로 핵심 명사만 추출 |
| anchor_gate 매칭률 향상 | 매칭 대상을 title+snippet+article_label+article_path로 확장 |
| 과도한 필터링 방지 | 최소 1개 소스 보장 (Soft Gate) |
| 임계값 최적화 | score_gate 임계값 0.60→0.55 하향 조정 |
| 버그 수정 | ai_log URL 이중 슬래시 문제 해결 |

### 1.3 Phase 히스토리
| Phase | 주요 내용 | 상태 |
|-------|----------|------|
| Phase 43 | COMPLAINT_KEYWORDS "하" 버그 수정 | 완료 |
| Phase 44-46 | 가드레일 완화/강화, 소프트 가드레일 | 완료 |
| Phase 47 | GPT 피드백 반영 (금지 표현 수정) | 완료 |
| Phase 48 | Low-relevance Gate + dataset_id 필터 | 완료 |
| Phase 49 | 도메인 라우팅 개선 + config 분리 | 완료 |
| **Phase 50** | **LowRelevanceGate 개선 + Soft Gate** | **완료** |

---

## 2. 문제 분석

### 2.1 발견된 문제

Phase 48 이후 운영 테스트에서 다음과 같은 문제가 발견되었습니다:

| 쿼리 | 증상 | 원인 |
|------|------|------|
| "연차휴가 규정 알려줘" | 소스 0개 반환 | "알려줘"가 anchor_keywords에 포함되어 매칭 실패 |
| "보안 관련 문서 요약해줘" | 소스 0개 반환 | "요약해줘"가 anchor_keywords에 포함 |
| "정책 관련 문서 요약해줘" | 소스 0개 반환 | anchor_keywords가 모두 stopwords로 제거됨 |

### 2.2 근본 원인 분석

#### 원인 1: anchor_keywords에 행동 표현 포함

```python
# 기존 extract_anchor_keywords 로직
def extract_anchor_keywords(query: str) -> set[str]:
    tokens = query.split()
    stopwords = get_anchor_stopwords()
    keywords = {t for t in tokens if t not in stopwords and len(t) >= 2}
    return keywords

# "연차휴가 규정 알려줘" → {"연차휴가", "규정", "알려줘"}
# "알려줘"가 키워드로 포함됨 → 소스에서 "알려줘" 매칭 시도 → 실패
```

**문제**: "알려줘", "요약해줘" 등 행동 표현이 anchor_keywords에 포함되어 불필요한 매칭 조건 생성

#### 원인 2: anchor_gate 매칭 대상 제한

```python
# 기존 check_anchor_keywords_in_sources 로직
def check_anchor_keywords_in_sources(keywords: set[str], sources: list[ChatSource]) -> bool:
    for source in sources:
        combined = f"{source.title} {source.snippet}".lower()
        # article_label, article_path는 체크하지 않음
        for kw in keywords:
            if kw in combined:
                return True
    return False
```

**문제**: `article_label`(예: "제5조 (보안 관련 조항)")이나 `article_path`(예: "제3장 정보보호 > 제10조")에 키워드가 있어도 매칭 실패

#### 원인 3: Hard Gate로 인한 전량 필터링

```python
# 기존 apply_low_relevance_gate 로직
def apply_low_relevance_gate(...) -> tuple[list[ChatSource], Optional[str]]:
    # score_gate 실패 시
    if max_score < threshold:
        return [], "max_score_below_threshold"  # 전량 필터링

    # anchor_gate 실패 시
    if not anchor_matched:
        return [], "no_anchor_term_match"  # 전량 필터링
```

**문제**: Gate 조건 미충족 시 모든 소스가 필터링되어 사용자에게 "문서를 찾지 못했습니다" 응답

#### 원인 4: score_gate 임계값 과도

```python
# 기존 설정
RAG_MIN_MAX_SCORE: float = 0.60
```

**문제**: 0.55~0.60 사이의 점수를 가진 관련성 있는 문서도 필터링됨

#### 원인 5: ai_log URL 이중 슬래시

```python
# 기존 코드
self._backend_log_endpoint = f"{settings.backend_base_url}/api/ai-logs"

# backend_base_url = "http://backend:8080/" 일 때
# 결과: "http://backend:8080//api/ai-logs" (이중 슬래시)
```

**문제**: trailing slash가 있는 URL에서 이중 슬래시 발생

---

## 3. 개선 작업

### 3.1 Phase 50-1: anchor_keywords 행동 표현 제거

#### 3.1.1 ACTION_TOKENS 정의

**파일**: `app/services/chat/rag_handler.py`

```python
# Phase 50: 행동 표현 토큰 (anchor_keywords에서 제거)
ACTION_TOKENS = frozenset([
    # 요약 관련
    "요약해줘", "요약해주세요", "요약해", "요약좀", "요약",
    "정리해줘", "정리해주세요", "정리해", "정리좀", "정리",
    # 설명 관련
    "알려줘", "알려주세요", "알려줄래", "알려줘요",
    "설명해줘", "설명해주세요", "설명해", "설명좀",
    # 기타 행동
    "보여줘", "보여주세요", "찾아줘", "찾아주세요",
    "해줘", "해주세요", "해줄래", "좀", "부탁", "뭐야", "뭔가",
])
```

#### 3.1.2 ACTION_SUFFIX_PATTERN 정의

```python
# Phase 50: 행동 접미사 패턴 (토큰 끝에서 제거)
ACTION_SUFFIX_PATTERN = re.compile(
    r'(해줘|해주세요|해줄래|해줄게|할래|하세요|해봐|해라|'
    r'알려줘|알려주세요|알려줄래|알려주라|'
    r'설명해|설명해줘|설명해주세요|'
    r'정리해|정리해줘|정리해주세요|'
    r'보여줘|보여주세요|찾아줘|찾아주세요|'
    r'줘|주세요|줄래|주라)$'
)
```

#### 3.1.3 extract_anchor_keywords 개선

```python
def extract_anchor_keywords(query: str) -> set[str]:
    """
    쿼리에서 anchor 키워드를 추출합니다.

    Phase 50 개선:
    - ACTION_TOKENS 필터링 (행동 표현 제거)
    - ACTION_SUFFIX_PATTERN으로 접미사 제거
    """
    if not query:
        return set()

    stopwords = get_anchor_stopwords()
    tokens = query.split()
    keywords = set()

    for token in tokens:
        token_lower = token.lower()

        # Phase 50: 행동 표현 토큰 제거
        if token_lower in ACTION_TOKENS:
            continue

        # Phase 50: 행동 접미사 제거 (예: "정책설명해줘" → "정책")
        cleaned = ACTION_SUFFIX_PATTERN.sub("", token_lower)

        # 불용어 및 짧은 토큰 제거
        if cleaned and cleaned not in stopwords and len(cleaned) >= 2:
            keywords.add(cleaned)

    return keywords
```

**효과**:
```
Before: "연차휴가 규정 알려줘" → {"연차휴가", "규정", "알려줘"}
After:  "연차휴가 규정 알려줘" → {"연차휴가"}  # "규정"은 stopwords, "알려줘"는 ACTION_TOKENS
```

---

### 3.2 Phase 50-2: anchor_gate 매칭 대상 확장

**파일**: `app/services/chat/rag_handler.py`

```python
def check_anchor_keywords_in_sources(
    anchor_keywords: set[str],
    sources: list[ChatSource],
) -> bool:
    """
    anchor 키워드가 소스들에 존재하는지 확인합니다.

    Phase 50 확장: article_label, article_path도 매칭 대상에 포함
    """
    if not anchor_keywords:
        return True  # 빈 키워드는 통과

    for source in sources:
        # Phase 50: 매칭 대상 확장
        combined_text = " ".join(filter(None, [
            source.title or "",
            source.snippet or "",
            getattr(source, "article_label", "") or "",  # Phase 50 확장
            getattr(source, "article_path", "") or "",   # Phase 50 확장
        ])).lower()

        for keyword in anchor_keywords:
            if keyword.lower() in combined_text:
                return True

    return False
```

**효과**:
```
소스: { title: "일반 규정", snippet: "목적 조항", article_label: "제5조 (보안 관련)" }
키워드: {"보안"}

Before: False (title+snippet에 "보안" 없음)
After:  True  (article_label에 "보안" 있음)
```

---

### 3.3 Phase 50-3: Soft Gate 안전장치 추가

#### 3.3.1 ANCHOR_GATE_MIN_KEEP 상수

**파일**: `app/services/chat/rag_handler.py`

```python
# Phase 50: anchor_gate 안전장치 - 최소 유지 개수
ANCHOR_GATE_MIN_KEEP = 1
```

#### 3.3.2 apply_low_relevance_gate Soft Gate 구현

```python
def apply_low_relevance_gate(
    sources: list[ChatSource],
    query: str,
    domain: Optional[str] = None,
) -> tuple[list[ChatSource], Optional[str]]:
    """
    Low-relevance Gate를 적용합니다.

    Phase 50 개선:
    - Hard Gate → Soft Gate 전환
    - 최소 ANCHOR_GATE_MIN_KEEP개 소스 보장
    """
    if not sources:
        return sources, None

    settings = get_settings()
    threshold = settings.RAG_MIN_MAX_SCORE
    min_keep = ANCHOR_GATE_MIN_KEEP

    max_score = max(s.score for s in sources)
    avg_score = sum(s.score for s in sources) / len(sources)
    anchor_keywords = extract_anchor_keywords(query)

    # 1. Score Gate
    if max_score < threshold:
        # Phase 50: Soft Gate - 최소 min_keep개 유지
        kept_sources = sorted(sources, key=lambda s: s.score, reverse=True)[:min_keep]
        logger.info(
            f"[LowRelevanceGate] SOFT_DEMOTE by score_gate | "
            f"max_score={max_score:.3f} < threshold={threshold} | "
            f"kept_count={len(kept_sources)} (min_keep={min_keep})"
        )
        return kept_sources, "max_score_below_threshold_soft"

    # 2. Anchor Gate
    anchor_matched = check_anchor_keywords_in_sources(anchor_keywords, sources)
    if not anchor_matched and anchor_keywords:
        # Phase 50: Soft Gate - 최소 min_keep개 유지
        kept_sources = sorted(sources, key=lambda s: s.score, reverse=True)[:min_keep]
        logger.info(
            f"[LowRelevanceGate] SOFT_DEMOTE by anchor_gate | "
            f"anchor_keywords={anchor_keywords} not found | "
            f"kept_count={len(kept_sources)} (min_keep={min_keep})"
        )
        return kept_sources, "no_anchor_term_match_soft"

    # 모든 Gate 통과
    return sources, None
```

**효과**:
```
Before (Hard Gate):
  score_gate 실패 → return [], "max_score_below_threshold"

After (Soft Gate):
  score_gate 실패 → return [top_1_source], "max_score_below_threshold_soft"
```

---

### 3.4 Phase 50-4: score_gate 임계값 하향 조정

**파일**: `app/core/config.py`

```python
# Before
RAG_MIN_MAX_SCORE: float = 0.60

# After (Phase 50)
RAG_MIN_MAX_SCORE: float = 0.55
```

**근거**: Phase 48 임계값 분석 결과, 0.55~0.60 사이에 관련성 있는 문서들이 다수 존재

---

### 3.5 Phase 50-5: ai_log URL 이중 슬래시 수정

**파일**: `app/services/ai_log_service.py`

```python
def __init__(self, pii_service: Optional[PiiService] = None) -> None:
    self._pii_service = pii_service or PiiService()

    # Phase 50: trailing slash 제거로 //api/ai-logs 중복 방지
    if settings.backend_base_url:
        base_url = settings.backend_base_url.rstrip("/")
        self._backend_log_endpoint = f"{base_url}/api/ai-logs"
    else:
        self._backend_log_endpoint = None
```

**효과**:
```
Before: "http://backend:8080/" + "/api/ai-logs" = "http://backend:8080//api/ai-logs"
After:  "http://backend:8080".rstrip("/") + "/api/ai-logs" = "http://backend:8080/api/ai-logs"
```

---

## 4. 테스트 결과

### 4.1 테스트 환경
- **Unit 테스트**: `tests/unit/test_phase50_low_relevance_gate.py` (24개)
- **통합 테스트**: `scripts/test_phase48_changes.py` (Phase 50 반영, 5개)

### 4.2 Unit 테스트 결과

```
============================= test session starts =============================
platform win32 -- Python 3.12.7, pytest-9.0.2

tests/unit/test_phase50_low_relevance_gate.py::TestExtractAnchorKeywords::test_removes_action_tokens_simple PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestExtractAnchorKeywords::test_removes_action_tokens_yoyak PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestExtractAnchorKeywords::test_removes_action_suffix PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestExtractAnchorKeywords::test_preserves_core_nouns PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestExtractAnchorKeywords::test_query_with_only_action_tokens PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestExtractAnchorKeywords::test_compound_action_expressions PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestCheckAnchorKeywordsInSources::test_matches_in_snippet PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestCheckAnchorKeywordsInSources::test_matches_in_title PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestCheckAnchorKeywordsInSources::test_matches_in_article_label PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestCheckAnchorKeywordsInSources::test_matches_in_article_path PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestCheckAnchorKeywordsInSources::test_no_match_returns_false PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestCheckAnchorKeywordsInSources::test_empty_keywords_returns_true PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestApplyLowRelevanceGate::test_passes_high_score_with_anchor_match PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestApplyLowRelevanceGate::test_soft_demote_low_score_keeps_min PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestApplyLowRelevanceGate::test_soft_demote_no_anchor_match_keeps_min PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestApplyLowRelevanceGate::test_empty_sources_returns_empty PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestRegressionQueries::test_regression_yeoncha_query PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestRegressionQueries::test_regression_boan_yoyak_query PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestRegressionQueries::test_regression_jeongchaek_yoyak_query PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestConfigValues::test_min_max_score_threshold PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestConfigValues::test_anchor_gate_min_keep PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestConfigValues::test_action_tokens_contain_key_expressions PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestAiLogUrlFix::test_url_no_double_slash PASSED
tests/unit/test_phase50_low_relevance_gate.py::TestAiLogUrlFix::test_url_without_trailing_slash PASSED

============================= 24 passed in 2.82s ==============================
```

### 4.3 통합 테스트 결과

```
============================================================
Phase 48 Low-relevance Gate Test
============================================================

=== Test 1: Config Settings ===
  [PASS] RAG_MIN_MAX_SCORE exists: True
    -> value: 0.55
  [PASS] RAG_ANCHOR_STOPWORDS exists: True
  [PASS] RAG_DATASET_FILTER_ENABLED exists: True

=== Test 2: Anchor Keyword Extraction ===
  [PASS] Stopwords loaded: 38 words
  [PASS] '연차 규정 알려줘' -> {'연차'} (expected: {'연차'})
  [PASS] '근태 관련 문서' -> {'근태'} (expected: {'근태'})
  [PASS] '법무 정책 7번' -> {'법무', '7번'} (expected: {'법무', '7번'})
  [PASS] '총무 관련 문서 요약해줘' -> {'총무'} (expected: {'총무'})

=== Test 3: Anchor Keyword in Sources Check ===
  [PASS] keywords={'복무'} in sources: True
  [PASS] keywords={'연차'} not in sources: True
  [PASS] keywords={'개인정보'} in sources: True
  [PASS] empty keywords always passes: True

=== Test 4: Low-relevance Gate ===
  [PASS] High score + keyword match -> PASSED: 2 sources
  [PASS] Low score -> SOFT DEMOTED: reason=max_score_below_threshold_soft, kept=1
  [PASS] Keyword mismatch -> SOFT DEMOTED: reason=no_anchor_term_match_soft, kept=1
  [PASS] Empty sources -> stays empty: 0

=== Test 5: Dataset Filter Expression ===
  [PASS] POLICY in mapping: True
  [PASS] domain=POLICY -> filter=dataset_id == "사내규정"

============================================================
Test Summary
============================================================
  [PASS] test_config_settings
  [PASS] test_anchor_keyword_extraction
  [PASS] test_anchor_keyword_check
  [PASS] test_low_relevance_gate
  [PASS] test_dataset_filter_expr

Total: 5/5 tests passed
[OK] All Phase 48 tests passed!
```

### 4.4 회귀 테스트 결과

| 쿼리 | Before (Phase 48) | After (Phase 50) |
|------|-------------------|------------------|
| "연차휴가 규정 알려줘" | 소스 0개, 실패 | 소스 1개+, **통과** |
| "보안 관련 문서 요약해줘" | 소스 0개, 실패 | 소스 1개+, **통과** |
| "정책 관련 문서 요약해줘" | 소스 0개, 실패 | 소스 1개+, **통과** |

---

## 5. 기술적 인사이트

### 5.1 Hard Gate vs Soft Gate 패턴

```
[Anti-Pattern] Hard Gate
조건 미충족 → 전량 필터링 → 사용자에게 "찾지 못했습니다" 응답
→ 사용자 경험 저하, 실제로는 관련 문서가 있을 수 있음

[Best Practice] Soft Gate
조건 미충족 → 최소 N개 유지 → 사용자에게 최선의 결과 제공
→ 정보 손실 최소화, 사용자 경험 향상
```

**적용**:
```python
# Hard Gate (Before)
if condition_failed:
    return [], "reason"

# Soft Gate (After)
if condition_failed:
    kept = sorted(sources, key=lambda s: s.score, reverse=True)[:min_keep]
    return kept, "reason_soft"
```

### 5.2 행동 표현 분리 패턴

```
[Anti-Pattern] 모든 토큰을 키워드로 사용
"연차휴가 규정 알려줘" → {"연차휴가", "규정", "알려줘"}
→ "알려줘"를 소스에서 매칭하려 함 → 실패

[Best Practice] 행동 표현과 핵심 명사 분리
"연차휴가 규정 알려줘" → {"연차휴가"} (행동 표현 제거)
→ 핵심 명사만으로 매칭 → 정확도 향상
```

**구현**:
- `ACTION_TOKENS`: 완전 일치로 제거할 행동 표현
- `ACTION_SUFFIX_PATTERN`: 접미사로 제거할 행동 표현 (예: "정책설명해줘" → "정책")

### 5.3 매칭 대상 확장 패턴

```
[Anti-Pattern] 제한된 필드만 매칭
title + snippet만 검색 → 메타데이터에 있는 키워드 놓침

[Best Practice] 모든 관련 필드 매칭
title + snippet + article_label + article_path
→ 문서 구조 정보까지 활용 → 매칭률 향상
```

### 5.4 URL 정규화 패턴

```python
# [Anti-Pattern] 직접 문자열 결합
endpoint = f"{base_url}/api/path"  # trailing slash 있으면 //api/path

# [Best Practice] 정규화 후 결합
endpoint = f"{base_url.rstrip('/')}/api/path"  # 항상 /api/path
```

---

## 6. 결론

### 6.1 성과 요약

| 항목 | 구현 내용 | 효과 |
|------|----------|------|
| ACTION_TOKENS 필터링 | 27개 행동 표현 정의 | anchor_keywords 정확도 향상 |
| ACTION_SUFFIX_PATTERN | 접미사 제거 정규식 | 복합 표현 처리 |
| 매칭 대상 확장 | article_label, article_path 추가 | 매칭률 향상 |
| Soft Gate | 최소 1개 소스 보장 | 전량 필터링 방지 |
| 임계값 하향 | 0.60 → 0.55 | 관련 문서 포함률 증가 |
| URL 정규화 | rstrip("/") 적용 | 이중 슬래시 버그 수정 |

### 6.2 핵심 개선 요인

| 순위 | 개선 항목 | 기여도 |
|------|----------|--------|
| 1 | Soft Gate 안전장치 | **결정적** (전량 필터링 방지) |
| 2 | ACTION_TOKENS 필터링 | **높음** (anchor_keywords 정확도) |
| 3 | 매칭 대상 확장 | **중간** (매칭률 향상) |
| 4 | 임계값 하향 | **보조** (경계선 문서 포함) |

### 6.3 개선 전후 비교

| 지표 | Before (Phase 48) | After (Phase 50) |
|------|-------------------|------------------|
| anchor_keywords 정확도 | 낮음 (행동 표현 포함) | 높음 (핵심 명사만) |
| anchor_gate 매칭률 | 제한적 (2개 필드) | 확장 (4개 필드) |
| 최소 응답 소스 | 0개 (Hard Gate) | 1개 (Soft Gate) |
| score_gate 임계값 | 0.60 | 0.55 |
| 회귀 쿼리 통과율 | 0/3 | **3/3** |

### 6.4 향후 개선 방향

1. **형태소 분석기 도입**: 행동 표현 감지 정확도 향상
2. **동적 min_keep 조정**: 쿼리 유형별 최소 유지 개수 조정
3. **anchor_keywords 가중치**: 핵심 명사에 가중치 부여
4. **A/B 테스트**: Soft Gate vs Hard Gate 사용자 만족도 비교

---

## 부록

### A. 수정 파일 목록

| 파일 | 수정 내용 |
|------|----------|
| `app/services/chat/rag_handler.py` | ACTION_TOKENS, ACTION_SUFFIX_PATTERN, Soft Gate 구현 |
| `app/core/config.py` | RAG_MIN_MAX_SCORE 0.55, RAG_ANCHOR_STOPWORDS 정리 |
| `app/services/ai_log_service.py` | URL rstrip("/") 적용 |
| `scripts/test_phase48_changes.py` | Phase 50 Soft Gate 반영 |
| `tests/unit/test_phase50_low_relevance_gate.py` | 24개 테스트 신규 작성 |

### B. 테스트 파일

| 파일 | 테스트 수 | 내용 |
|------|----------|------|
| `test_phase50_low_relevance_gate.py` | 24 | anchor_keywords, anchor_gate, Soft Gate, URL 수정 |
| `test_phase48_changes.py` | 5 | 통합 테스트 (Phase 50 반영) |

### C. 관련 상수 및 설정

```python
# app/services/chat/rag_handler.py
ANCHOR_GATE_MIN_KEEP = 1

ACTION_TOKENS = frozenset([
    "요약해줘", "요약해주세요", "요약해", "요약좀", "요약",
    "정리해줘", "정리해주세요", "정리해", "정리좀", "정리",
    "알려줘", "알려주세요", "알려줄래", "알려줘요",
    "설명해줘", "설명해주세요", "설명해", "설명좀",
    "보여줘", "보여주세요", "찾아줘", "찾아주세요",
    "해줘", "해주세요", "해줄래", "좀", "부탁", "뭐야", "뭔가",
])

# app/core/config.py
RAG_MIN_MAX_SCORE: float = 0.55
```

### D. Gate 동작 흐름도

```
┌─────────────────────────────────────────────────────────┐
│                    RAG Sources 입력                      │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                    Score Gate 체크                       │
│              max_score >= 0.55 ?                        │
└─────────────────────────────────────────────────────────┘
                     │              │
                    YES            NO
                     │              │
                     ▼              ▼
              ┌──────────┐   ┌──────────────────────┐
              │ 다음 Gate │   │ Soft Demote          │
              │ 진행     │   │ Top 1 유지           │
              └──────────┘   │ reason: score_soft   │
                     │       └──────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────────┐
│                   Anchor Gate 체크                       │
│        anchor_keywords in sources ?                     │
└─────────────────────────────────────────────────────────┘
                     │              │
                    YES            NO
                     │              │
                     ▼              ▼
              ┌──────────┐   ┌──────────────────────┐
              │ 전체 통과 │   │ Soft Demote          │
              │ 반환     │   │ Top 1 유지           │
              └──────────┘   │ reason: anchor_soft  │
                            └──────────────────────┘
```

---

**작성일**: 2025-12-29
**작성자**: CTRL+F AI 개발팀
**커밋**: `871caf4` (feat: Phase 50 LowRelevanceGate 개선 및 ai_log URL 수정)
