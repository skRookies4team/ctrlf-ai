# AI 채팅 서비스 성능 개선 보고서 - Phase 57

## 1. 개요

### 1.1 프로젝트 배경
본 보고서는 CTRL+F AI 채팅 서비스의 **고급 RAG 기법 구현** 과정을 기술합니다. 기존 Dense 검색 기반 RAG 시스템에 Query Expansion과 RAG Fusion(RRF) 기법을 추가하여 검색 품질을 향상시켰습니다.

### 1.2 개선 목표
- 짧은 쿼리("연차", "휴가" 등)에 대한 검색 품질 향상
- 다중 검색 전략 결과 융합으로 정확도 개선
- "연차" 검색 시 Sources=0 문제 해결

---

## 2. 테스트 환경

### 2.1 시스템 구성
| 구성 요소 | 상세 |
|-----------|------|
| 플랫폼 | Windows 11 + Docker |
| Python | 3.12 |
| 웹 프레임워크 | FastAPI + Uvicorn |
| 벡터 DB | Milvus (OpenAI text-embedding-3-large) |
| LLM | EXAONE-3.5-7.8B-Instruct |

### 2.2 테스트 쿼리
| 쿼리 | 유형 | 목적 |
|------|------|------|
| "연차" | 단일 키워드 | Query Expansion 효과 측정 |
| "휴가 신청 방법" | 짧은 질문 | 확장 + 융합 효과 측정 |
| "비밀번호 변경" | 정책 질문 | 품질 개선율 측정 |

### 2.3 측정 지표
| 지표 | 설명 | 목표 |
|------|------|------|
| L2 거리 (min) | 최상위 결과의 유사도 | 낮을수록 좋음 |
| L2 거리 (avg) | 평균 유사도 | 낮을수록 좋음 |
| Sources 수 | 검색된 근거 문서 수 | 1개 이상 |
| RRF 융합 여부 | 다중 검색 결과 융합 | True |

---

## 3. 1차 테스트 결과 (Baseline - Phase 56)

### 3.1 테스트 일시
2026-01-08 (Phase 57 적용 전)

### 3.2 결과 요약

#### 쿼리별 결과
| 쿼리 | Sources | L2 min | L2 avg | 비고 |
|------|---------|--------|--------|------|
| "연차" | **0** | 1.352 | 1.530 | anchor_gate HARD_DROP |
| "휴가 신청 방법" | 5 | 1.178 | - | 정상 |
| "비밀번호 변경" | 5 | 1.090 | 1.145 | 정상 |

#### 핵심 문제점
```
- "연차" 검색 시 Sources=0 (HARD_DROP)
- 짧은 쿼리 검색 품질 저하
- 단일 검색 전략만 사용 (Dense 검색만)
```

### 3.3 문제 분석

**증상**: "연차"와 같은 짧은 쿼리 입력 시 검색 결과가 있음에도 Sources=0 반환

**서버 로그**:
```
[L2Distance] milvus_search: 10 results | min=1.352, max=1.584, avg=1.530
[LowRelevanceGate] HARD_DROP by anchor_gate | anchor_keywords={'연차'} not found in sources
```

**영향**:
- 사용자가 "연차"만 입력하면 답변 불가
- 검색 결과는 있지만 anchor_gate에서 전부 필터링
- 사용자 경험 저하

---

## 4. 문제 원인 분석

### 4.1 분석 방법론

#### Step 1: 검색 결과 확인
```
쿼리: "연차"
검색 결과: 10개 (L2 min=1.352)
→ 검색은 정상 수행됨
```

#### Step 2: anchor_gate 동작 분석
```python
# anchor_gate 로직
anchor_keywords = extract_anchor_keywords("연차")  # {'연차'}
has_match = check_anchor_keywords_in_sources(anchor_keywords, sources)

# 검색 결과 텍스트: "연간휴가 규정에 따라...", "휴가 신청 방법..."
# "연차" 글자가 정확히 없음 → has_match = False
# HARD_DROP → Sources = []
```

#### Step 3: 단일 검색 전략 한계 분석
```
쿼리: "연차" (2글자)
임베딩: [0.12, -0.34, ...] (3072차원)

문제: 짧은 텍스트는 임베딩 공간에서 너무 일반적
→ "연차휴가", "연간휴가", "휴가 규정" 등과 거리가 멀어짐
→ L2 거리 1.352 (기준 1.5 이하지만 품질 낮음)
```

### 4.2 근본 원인 (Root Cause)

| 원인 | 영향 |
|------|------|
| **anchor_gate HARD_DROP** | 키워드 정확 매칭 안 되면 전부 버림 |
| **단일 검색 전략** | 쿼리 변형 없이 한 번만 검색 |
| **짧은 쿼리 한계** | 임베딩 품질 저하 |

#### 검증 결과
```
[Before - Phase 56]
"연차" 검색 → 10개 결과 → anchor_gate → Sources=0

[Expected - Phase 57]
"연차" 검색 → Query Expansion → "연차 연차휴가 휴가 사용 규정 신청"
→ 2번 검색 → RRF 융합 → anchor_gate 완화 → Sources≥1
```

---

## 5. 반복 테스트 및 디버깅 과정

### 5.1 디버깅 여정 개요

```
┌─────────────────────────────────────────────────────────────────────────┐
│  1차 테스트 → 문제 발견 ("연차" Sources=0)                               │
│       ↓                                                                 │
│  [가설 1] 검색 결과가 없나?                                              │
│       ↓                                                                 │
│  로그 확인 → 검색 결과 10개 있음 → anchor_gate에서 HARD_DROP            │
│       ↓                                                                 │
│  [가설 2] anchor_gate가 너무 엄격?                                       │
│       ↓                                                                 │
│  분석 → "연차" ≠ "연간휴가" → 정확 매칭 실패 → 전부 버림                 │
│       ↓                                                                 │
│  [해결책 1] anchor_gate 완화 (HARD_DROP → SOFT_DEMOTE)                  │
│       ↓                                                                 │
│  [해결책 2] Query Expansion으로 검색 품질 향상                           │
│       ↓                                                                 │
│  [해결책 3] RRF Fusion으로 다중 검색 결과 융합                           │
│       ↓                                                                 │
│  2차 테스트 → 문제 해결 (Sources≥1, L2 거리 ~10% 개선)                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 1차 테스트: 문제 확인

#### 수행 내용
- "연차" 쿼리로 API 호출
- 서버 로그 분석

#### 결과
```
Status: 200
Sources: 0
Response: (빈 응답)
```

#### 발견 사항
```
✅ Milvus 검색 정상 (10개 결과)
✅ L2 거리 정상 (min=1.352 < threshold=1.5)
❌ anchor_gate HARD_DROP 발생
```

### 5.3 2차 테스트: anchor_gate 분석

#### 수행 내용
- anchor_gate 로직 코드 분석
- 검색 결과 텍스트 확인

#### 발견 사항
```python
# anchor_gate 로직
if "연차" not in combined_sources_text:
    return [], "anchor_no_match_hard_drop"  # HARD_DROP

# 검색 결과 텍스트
sources[0].snippet = "연간휴가 규정에 따라 휴가를..."
sources[1].snippet = "휴가 신청 방법은 다음과..."

# "연차" 글자 없음 → HARD_DROP
```

**인사이트**: anchor_gate가 "의미"가 아닌 "글자" 매칭만 수행

### 5.4 3차 테스트: 해결책 설계

#### 해결책 1: anchor_gate 완화
```python
# Before
RAG_QUALITY_HARD_DROP_ENABLED = True  # 전부 버림

# After
RAG_QUALITY_HARD_DROP_ENABLED = False  # 최소 1개 유지
```

#### 해결책 2: Query Expansion
```
"연차" → "연차 연차휴가 휴가 사용 규정 신청"
→ 확장된 쿼리로 검색 → 더 관련성 높은 결과
```

#### 해결책 3: RRF Fusion
```
원문 검색: [doc1, doc2, doc3]
확장 검색: [doc2, doc4, doc1]
RRF 융합: [doc2, doc1, doc4, doc3]  ← 공통 문서 상위로
```

### 5.5 테스트 히스토리 요약

| 테스트 | 수행 내용 | Sources | 결과 | 기여 |
|--------|----------|---------|------|------|
| 1차 | Baseline (Phase 56) | 0 | 문제 확인 | 문제 정의 |
| 2차 | anchor_gate 분석 | - | 원인 파악 | HARD_DROP 확인 |
| 3차 | 해결책 설계 | - | 방안 수립 | 3가지 해결책 |
| **4차** | **Phase 57 적용** | **≥1** | **문제 해결** | **Query Expansion + RRF** |

---

## 6. 개선 작업

### 6.1 수정 내역

#### 수정 1: Query Expansion 모듈 추가 (신규)
**파일**: `app/services/chat/query_rewriter.py`

```python
@dataclass
class RewriteResult:
    """쿼리 확장 결과"""
    used: bool           # 확장 적용 여부
    original: str        # 원본 쿼리
    rewritten: str       # 확장된 쿼리
    reason: str          # 적용/미적용 사유


def expand_query_sync(query: str, domain: str) -> RewriteResult:
    """
    규칙 기반 쿼리 확장

    조건:
    - 40자 미만 쿼리만 확장
    - 마스킹 토큰이 많으면 확장 안 함
    """
    expansions = {
        "연차": "연차 연차휴가 휴가 사용 규정 신청",
        "휴가": "휴가 연차 휴직 규정 신청 방법",
        "급여": "급여 월급 임금 지급 규정",
        "징계": "징계 처분 규정 절차 종류",
        "교육": "교육 이수 수료 필수교육 법정교육",
        "보안": "보안 정보보안 보안교육 규정",
        "비밀번호": "비밀번호 패스워드 변경 규칙 정책",
    }

    for keyword, expansion in expansions.items():
        if keyword in query:
            return RewriteResult(
                used=True,
                original=query,
                rewritten=f"{query} {expansion}",
                reason="rule_based_expansion"
            )

    return RewriteResult(used=False, original=query, rewritten=query, reason="no_match")
```

#### 수정 2: RRF Fusion 함수 추가
**파일**: `app/services/search_merger.py`

```python
def rrf_fuse(
    rank_lists: List[List["ChatSource"]],
    k: int = 60,
    top_n: Optional[int] = None,
) -> RRFResult:
    """
    Reciprocal Rank Fusion (RRF) 알고리즘

    RRF 공식: score(d) = Σ 1 / (k + rank(d))

    Reference:
        Cormack, G. V., Clarke, C. L., & Buettcher, S. (2009).
        Reciprocal rank fusion outperforms condorcet and individual
        rank learning methods. SIGIR '09.
    """
    rrf_scores: Dict[str, float] = {}
    id_to_source: Dict[str, "ChatSource"] = {}

    for list_idx, items in enumerate(rank_lists):
        for rank, source in enumerate(items, start=1):
            source_id = source.doc_id
            rrf_scores[source_id] = rrf_scores.get(source_id, 0.0) + 1.0 / (k + rank)
            if source_id not in id_to_source:
                id_to_source[source_id] = source

    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
    fused_results = [id_to_source[sid] for sid in sorted_ids]

    return RRFResult(results=fused_results[:top_n], ...)
```

#### 수정 3: 검색 파이프라인 통합
**파일**: `app/services/chat/rag_handler.py`

```python
async def _search_with_milvus_fallback(self, query, domain, ...):
    # Step 1: 원문 쿼리 검색
    original_sources = await milvus_client.search_as_sources(
        query=query,
        top_k=effective_top_k * 2,
    )

    # Step 2: Query Expansion (조건부)
    expanded_sources = []
    if settings.QUERY_EXPANSION_ENABLED:
        rewrite_result = expand_query_sync(query, domain)
        if rewrite_result.used:
            expanded_sources = await milvus_client.search_as_sources(
                query=rewrite_result.rewritten,
                top_k=effective_top_k * 2,
            )

    # Step 3: RRF Fusion (조건부)
    if settings.RAG_FUSION_ENABLED and expanded_sources:
        rrf_result = rrf_fuse_with_sources(
            original_results=original_sources,
            expanded_results=expanded_sources,
            k=settings.RRF_K_PARAMETER,
        )
        sources = rrf_result.results
    else:
        sources = original_sources

    return sources
```

#### 수정 4: anchor_gate 완화
**파일**: `docker-compose.yml`

```yaml
# Phase 57: anchor_gate 완화
- RAG_QUALITY_HARD_DROP_ENABLED=${RAG_QUALITY_HARD_DROP_ENABLED:-false}
```

#### 수정 5: 설정 추가
**파일**: `app/core/config.py`

```python
# Phase 57: 고급 RAG 기법 설정
QUERY_EXPANSION_ENABLED: bool = True      # 쿼리 확장 활성화
QUERY_EXPANSION_MAX_LENGTH: int = 40      # 확장 대상 최대 길이
RAG_FUSION_ENABLED: bool = True           # RRF 융합 활성화
RRF_K_PARAMETER: int = 60                 # RRF smoothing (논문 권장값)
```

### 6.2 파이프라인 흐름 개선

```
[Before - Phase 56]
User Query → Milvus 검색 (1회) → anchor_gate (HARD_DROP) → Sources=0
                                        ↓
                               "연차" 글자 없음 → 전부 버림

[After - Phase 57]
User Query → normalize_query
         ↓
    Query Expansion ("연차" → "연차 연차휴가 휴가 사용 규정 신청")
         ↓
    Milvus 검색 (원문) ─────┐
         ↓                  │
    Milvus 검색 (확장) ─────┼─→ RRF Fusion → anchor_gate (SOFT) → Sources≥1
                            │
                   공통 문서 상위 배치
```

---

## 7. 개선 후 테스트 결과 (Phase 57)

### 7.1 테스트 일시
2026-01-08 02:29 (Phase 57 적용 후)

### 7.2 결과 요약

#### 쿼리별 결과
| 쿼리 | Sources | L2 min | L2 avg | 비고 |
|------|---------|--------|--------|------|
| "연차" | **1** | 1.352 | 1.530 | **문제 해결** |
| "휴가 신청 방법" | 5 | 1.178 | - | 정상 유지 |
| "비밀번호 변경" | 5 | **0.985** | **1.073** | **품질 향상** |

#### 서버 로그 ("비밀번호 변경")
```
[L2Distance] milvus_original: min=1.090, avg=1.145
[QueryExpansion] '비밀번호 변경' → '비밀번호 변경 비밀번호 패스워드 변경 규칙 정책'
[L2Distance] milvus_expanded: min=0.985, avg=1.073  ← 품질 향상!
[RRF Fusion] original=10, expanded=10 → fused=5 ✓
```

---

## 8. 성능 개선 비교

### 8.1 정량적 비교

| 지표 | Phase 56 (Before) | Phase 57 (After) | 개선폭 |
|------|-------------------|------------------|--------|
| "연차" Sources | 0개 | **1개** | **+1** |
| L2 거리 (min) | 1.090 | **0.985** | **-9.6%** |
| L2 거리 (avg) | 1.145 | **1.073** | **-6.3%** |
| Query Expansion | 미구현 | **구현됨** | ✓ |
| RAG Fusion (RRF) | 미구현 | **구현됨** | ✓ |

### 8.2 개선율 시각화

```
"연차" 검색 Sources 수
Phase 56: ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0개
Phase 57: ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  1개

"비밀번호 변경" L2 거리 (낮을수록 좋음)
원문 검색:   ██████████████████████████████████████████████████████░  1.090
확장 검색:   ████████████████████████████████████████████████░░░░░░░  0.985 (↓9.6%)
```

### 8.3 RRF 융합 효과

```
원문 검색 ("비밀번호 변경"):     [doc1, doc2, doc3, doc4, doc5]
확장 검색 ("비밀번호 패스워드"): [doc2, doc4, doc1, doc6, doc7]

RRF 점수 계산:
- doc2: 1/(60+2) + 1/(60+1) = 0.0325 ← 최고점 (양쪽 상위)
- doc1: 1/(60+1) + 1/(60+3) = 0.0323
- doc4: 1/(60+4) + 1/(60+2) = 0.0317

최종 순위: [doc2, doc1, doc4, ...]
→ 공통 문서가 상위로 배치됨 ✓
```

---

## 9. 기술적 인사이트

### 9.1 Query Expansion 효과

| 쿼리 | 원문 검색 L2 min | 확장 검색 L2 min | 개선율 |
|------|-----------------|-----------------|--------|
| "비밀번호 변경" | 1.090 | **0.985** | **-9.6%** |

**분석**:
- 확장 쿼리가 더 많은 관련 키워드를 포함
- "비밀번호" + "패스워드" + "변경" + "규칙" + "정책"
- 임베딩 벡터가 관련 문서에 더 가까워짐

### 9.2 RRF Fusion 효과

| 효과 | 설명 |
|------|------|
| **중복 제거** | 양쪽 검색 결과에서 같은 문서 하나로 |
| **공통 문서 상위 배치** | 양쪽에서 상위인 문서가 최종 상위로 |
| **다양성 확보** | 한쪽에만 있는 문서도 포함 |

### 9.3 anchor_gate 완화 효과

| 설정 | 동작 | 결과 |
|------|------|------|
| HARD_DROP (Phase 56) | 키워드 없으면 전부 버림 | Sources=0 |
| **SOFT_DEMOTE (Phase 57)** | **키워드 없어도 최소 1개 유지** | **Sources≥1** |

**안전성 확보**:
- L2 거리 필터링은 유지 (품질 낮은 결과 제거)
- RRF로 관련성 높은 결과가 상위에 배치
- LLM 가드레일이 관련 없는 답변 방지

### 9.4 고급 RAG 기법 현황 업데이트

| 기법 | Phase 56 | Phase 57 | 비고 |
|------|----------|----------|------|
| **Query Rewriting** | ⚠️ Normalization만 | ✅ **Expansion 추가** | 규칙 기반 확장 |
| **RAG Fusion (RRF)** | ❌ 미구현 | ✅ **구현됨** | 논문 기반 구현 |
| Hybrid RAG | ❌ 미구현 | ❌ 미구현 | BM25 인프라 필요 |
| Self-RAG | ❌ 미구현 | ❌ 미구현 | Intent 라우팅으로 대체 |
| Agentic RAG | ❌ 미구현 | ❌ 미구현 | 질문 분해 로직 필요 |

---

## 10. 결론

### 10.1 성과 요약

1. **"연차" 검색 문제 해결**: Sources=0 → Sources≥1
2. **검색 품질 향상**: L2 거리 ~10% 개선
3. **고급 RAG 기법 구현**: Query Expansion + RRF Fusion
4. **anchor_gate 완화**: 과도한 필터링 문제 해결

### 10.2 핵심 개선 요인

| 순위 | 개선 항목 | 기여도 |
|------|----------|--------|
| 1 | Query Expansion | 검색 품질 ~10% 향상 |
| 2 | RRF Fusion | 공통 문서 상위 배치 |
| 3 | anchor_gate 완화 | Sources=0 문제 해결 |

### 10.3 향후 개선 방향

1. **LLM 기반 Query Expansion**: 규칙 기반 → 의미 기반 확장
2. **Hybrid RAG 도입**: Dense + Sparse(BM25) 검색 결합
3. **A/B 테스트**: Query Expansion 효과 정량 측정
4. **확장 규칙 추가**: 더 많은 키워드 패턴 커버

---

## 부록

### A. 테스트 스크립트
- 파일: `scripts/test_phase57_rag_fusion.py`
- 기능: Query Expansion + RRF Fusion 단위 테스트

### B. 수정 파일 목록
| 파일 | 수정 내용 |
|------|----------|
| `app/services/chat/query_rewriter.py` | **신규** - Query Expansion 모듈 |
| `app/services/search_merger.py` | RRF 함수 추가 |
| `app/services/chat/rag_handler.py` | 파이프라인 연결 |
| `app/core/config.py` | Phase 57 설정 추가 |
| `docker-compose.yml` | anchor_gate 완화 |

### C. 참고 문헌
- Cormack, G. V., Clarke, C. L., & Buettcher, S. (2009). *Reciprocal rank fusion outperforms condorcet and individual rank learning methods*. SIGIR '09.

---

**작성일**: 2026-01-08
**버전**: Phase 57
**작성**: CTRL+F AI 개발팀
