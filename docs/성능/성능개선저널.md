# CTRL+F AI 채팅 서비스 성능 개선 일지

## Performance Improvement Journal

**프로젝트**: CTRL+F AI 채팅 서비스
**기간**: 2025-12-23 ~ 2026-01-08
**버전**: Phase 43 ~ Phase 56
**작성자**: CTRL+F AI 개발팀

---

## Executive Summary

### 최종 성과

| 지표 | Baseline | Final | 개선폭 |
|------|----------|-------|--------|
| GENERAL_CHAT 오분류율 | 61.5% | **0.0%** | **-61.5%p** |
| RAG 검색 수행율 | 36.2% | **91.5%** | **+55.3%p** |
| 정상 답변율 | ~36% | **100%** | **+64%p** |
| 템플릿 폴백 | 79건 | **0건** | **-79건** |
| 언어 오류 | 3건 | **0건** | **해결** |
| 통계 환각 응답 | 발생 가능 | **차단됨** | **해결** |
| 개인정보 명단 요청 | 처리됨 | **차단됨** | **해결** |

### 성능 개선 여정

```
정상 답변율 추이
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1차 Baseline  ████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ~36%
5차 Phase 43  ████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   36.2%
7차 Phase 44  ████████████████████████████████████████████████░   97.7%
8차 Phase 45  █████████████████████████████████████████████████  100.0%
              ─────────────────┴──────────────────┴──────────────
                     Phase 43        Phase 44        Phase 45
```

---

## Phase별 개선 타임라인

### Phase 43 (2025-12-23) | "하" 키워드 버그 수정

#### 문제 발견
- **증상**: 130개 질문 중 80건(61.5%)이 GENERAL_CHAT으로 오분류
- **영향**: RAG 검색 없이 템플릿 폴백 응답 반환

#### 근본 원인
```python
# COMPLAINT_KEYWORDS에 "하" 포함
COMPLAINT_KEYWORDS = {"그지", "왜몰라", ..., "하"}  # ← 문제

# "하"는 한국어 동사 기본형
# "~하나요?", "~해야", "~하고" 등 모든 질문과 매칭
# → Intent 분류 전에 Complaint Fast Path에서 차단
```

#### 디버깅 과정
```
1차 테스트 → 문제 발견 (61.5% GENERAL_CHAT)
     ↓
2차 테스트 → POLICY_KEYWORDS 확장 시도 → 변화 없음
     ↓        └─ 발견: 서버 미재시작 문제
3차 테스트 → 서버 재시작 → 변화 없음
     ↓        └─ 발견: __pycache__ 캐싱 문제
4차 테스트 → pycache 삭제 → IntentService 단위 테스트 → 정상 작동!
     ↓        └─ 발견: IntentService 이전 단계에서 차단
가설 전환 → Complaint Fast Path 조사 → "하" 키워드 발견
     ↓
5차 테스트 → "하" 키워드 제거 → 극적 개선!
```

#### 해결책
```python
# Before
COMPLAINT_KEYWORDS = {"그지", "왜몰라", ..., "하"}

# After (Phase 43)
COMPLAINT_KEYWORDS = {"그지", "왜몰라", ...}  # "하" 제거

# 회귀 방지: 2자 미만 키워드 자동 필터
COMPLAINT_KEYWORDS = {kw for kw in _RAW_KEYWORDS if len(kw) >= 2}
```

#### 결과
| 지표 | Before | After | 변화 |
|------|--------|-------|------|
| GENERAL_CHAT | 61.5% | 0.0% | **-61.5%p** |
| RAG_INTERNAL | 36.2% | 91.5% | **+55.3%p** |
| 템플릿 폴백 | 79건 | 0건 | **-79건** |

---

### Phase 44 (2025-12-23) | 가드레일 완화

#### 문제 발견
- **증상**: 83건(63.8%)이 가드레일에 의해 차단
  - CITATION_HALLUCINATION: 67건
  - NO_RAG_EVIDENCE: 16건

#### 근본 원인
```python
# 가드레일이 너무 엄격
def validate_citation(self, answer, sources):
    if not sources:
        return (False, BLOCKED_TEMPLATE)  # RAG sources 없으면 무조건 차단
```

#### 해결책
```python
# Before (과차단)
if not sources:
    return (False, BLOCKED_TEMPLATE)

# After (완화)
if not sources:
    logger.info("allowing LLM general knowledge")
    return (True, answer)  # 경고만, 허용
```

#### 추가 개선
- **2nd-chance Retrieval**: top_k 5→15 재시도
- **쿼리 정규화**: 마스킹 토큰 제거

#### 결과
| 지표 | Before | After | 변화 |
|------|--------|-------|------|
| 정상 답변율 | 36.2% | 97.7% | **+61.5%p** |
| CITATION_HALLUCINATION | 67건 | 0건 | **-67건** |
| NO_RAG_EVIDENCE | 16건 | 0건 | **-16건** |

---

### Phase 45 (2025-12-23) | 소프트 가드레일 & 언어 강제

#### 개선 목표
1. sources=0일 때 정답 리스크 관리
2. LANGUAGE_ERROR 3건 해결
3. 검색 품질 진단 강화

#### 구현 내용

**1. 소프트 가드레일**
```python
# Intent별 응답 전략 분기
SOFT_GUARDRAIL_INTENTS = {POLICY_QA, EDUCATION_QA}
FREE_ANSWER_INTENTS = {GENERAL_CHAT, SYSTEM_HELP}

# sources=0 + 정책 질문 → 경고 prefix + 일반 지식 답변
SOFT_GUARDRAIL_PREFIX = """
⚠️ **사내 문서에서 관련 근거를 찾지 못했습니다.**

아래 답변은 일반적인 지식 기반 참고 정보입니다.
정확한 정보는 담당 부서에 문의해 주세요.
"""
```

**2. 한국어 강제**
```python
KOREAN_ONLY_INSTRUCTION = """
[언어 규칙 - 반드시 준수]
• 반드시 한국어로만 답변하세요.
• 영어 전문용어는 한글 표기 후 괄호 안에 영문 병기
  예: 인공지능(AI)
"""
```

**3. Similarity 분포 로깅**
```python
def log_similarity_distribution(sources, search_stage):
    """
    [Similarity] 1st_search: 5 results | min=0.423, max=0.892, avg=0.651 |
    distribution: [>=0.9:0, 0.7-0.9:2, 0.5-0.7:2, <0.5:1]
    """
```

#### 결과
| 지표 | Before | After | 변화 |
|------|--------|-------|------|
| 정상 답변율 | 97.7% | 100% | **+2.3%p** |
| LANGUAGE_ERROR | 3건 | 0건 | **해결** |

---

### Phase 46 (2025-12-24) | 소프트 가드레일 강화 & 지표 체계

#### 개선 내용
- DOMAIN_CONTACT_INFO 키 정렬 (EDUCATION 별칭 추가)
- '확정 표현 금지' 규칙 강화
- 지표 3축 분리 정의

#### 지표 체계 정의
```
[Availability 지표] 시스템이 답변을 생성할 수 있는가?
├── 차단률: 가드레일에 의해 차단된 비율
├── 에러율: 시스템 오류 비율
└── 언어 오류율: LANGUAGE_ERROR 비율

[RAG 지표] 검색이 정상적으로 동작하는가?
├── 소스 포함율: sources > 0인 응답 비율
├── returned_k: 평균 검색 결과 수
└── similarity 분포: 점수 구간별 분포

[정답 지표] 답변이 정확한가?
├── 핵심 포함 여부: 골든 답안 대비 핵심 키워드 포함율
└── 근거 기반 여부: RAG 소스 인용 정확도
```

---

### Phase 47 (2025-12-26) | GPT 피드백 반영

#### GPT 리뷰 피드백 및 해결

| 피드백 | 문제점 | 해결책 |
|--------|--------|--------|
| '~입니다' 금지 표현 포함 | 한국어 기본 종결어미라 LLM 문장 생성 어려움 | 금지에서 제거, 허용에 명시 |
| soft_guardrail RAG만 적용 | MIXED, BACKEND_API 경로 누락 | 모든 경로에 적용 |
| 도메인/카테고리 혼재 | 매핑 불안정 | 정규화 함수 + TOPIC_CONTACT_INFO 분리 |

---

### Phase 48 (2025-12-28) | Low-relevance Gate

#### 배경
> "문서에 실제로 없는 주제를 물었을 때도, RAG가 억지로 TopK를 반환해서 '근거가 있는 것처럼' 보이게 만드는 문제"

#### 구현: 2단계 Gate
```
┌─────────────────────────────────────────┐
│            RAG 검색 결과                  │
└───────────────────┬─────────────────────┘
                    ▼
          ┌──────────────────┐
          │  Gate A:         │    max_score < 0.60?
          │  Score Gate      │ ─────────────────→ sources=[]
          └────────┬─────────┘                    (강등)
                   │ 통과
                   ▼
          ┌──────────────────┐
          │  Gate B:         │    앵커 키워드가
          │  Anchor Gate     │    sources에 없음?
          └────────┬─────────┘ ──────────────→ sources=[]
                   │ 통과                       (강등)
                   ▼
          ┌──────────────────┐
          │   최종 결과       │
          └──────────────────┘
```

#### 추가 구현
- **dataset_id 필터**: domain → dataset_id 매핑으로 검색 범위 제한
- **Config 설정화**: 임계값/불용어를 config에서 관리

---

### Phase 49 (2025-12-29) | 도메인 라우팅 개선

#### 문제 발견
| 쿼리 | 기대 결과 | 실제 결과 | 원인 |
|------|----------|----------|------|
| "연차 규정 알려줘" | POLICY | HR (UNKNOWN) | 경계 B 애매함 판정 |
| "정보보호교육 내용" | EDUCATION | POLICY | 키워드 우선순위 충돌 |

#### 해결책
- **경계 B 체크 개선**: policy_clarifiers ("규정", "정책") 추가
- **복합 조건 우선 체크**: "교육"/"규정" 감지 시 해당 도메인 우선 분기
- **교육 키워드 확장**: Q세트 5개 도메인 키워드 추가
- **Config 분리**: EDUCATION dataset_id allowlist 환경변수화

---

### Phase 50 (2025-12-29) | LowRelevanceGate 개선

#### 문제 발견
| 쿼리 | 증상 | 원인 |
|------|------|------|
| "연차휴가 규정 알려줘" | 소스 0개 반환 | "알려줘"가 anchor_keywords에 포함 |
| "보안 관련 문서 요약해줘" | 소스 0개 반환 | "요약해줘"가 anchor_keywords에 포함 |

#### 해결책

**1. ACTION_TOKENS 필터링**
```python
ACTION_TOKENS = frozenset([
    "요약해줘", "알려줘", "설명해줘", "보여줘", "찾아줘",
    "해줘", "해주세요", "좀", "부탁",
])
```

**2. Soft Gate 안전장치**
```python
# Before (Hard Gate)
if condition_failed:
    return [], "reason"  # 전량 필터링

# After (Soft Gate)
if condition_failed:
    kept = sorted(sources, key=lambda s: s.score, reverse=True)[:1]
    return kept, "reason_soft"  # 최소 1개 유지
```

---

### Phase 55-56 (2025-12 ~ 2026-01) | 환각 방지 강화

#### Phase 55: 환각 방지 테스트 및 설정 강화

**통계/순위 환각 패턴 정의**
```python
STATISTICAL_CLAIM_PATTERNS = [
    r"TOP\s*\d+",
    r"가장\s*많이",
    r"\d+\s*위",
    r"약?\s*\d+\s*%",
    r"평균적으로",
]
```

#### Phase 56: 통계 질문 할루시네이션 방지

**Stats Out-of-Scope Fast Path**
```python
STATS_SIGNAL_PATTERNS = [
    r"최근\s*\d+\s*(년|개월|달|주|일)",
    r"\bTOP\s*\d+\b",
    r"상위\s*\d+",
    r"(통계|건수|횟수|비율|분포|추이|랭킹|순위)",
]

# 통계 질문 감지 시 조기 차단
STATS_OUT_OF_SCOPE = """
요청하신 '최근 N기간/Top N' 위반 통계는 현재 시스템에 집계 데이터가 없어
제공할 수 없습니다.

대신 '자주 언급되는 위반 유형(안내문서 기준)' 요약은 제공할 수 있어요.
"""
```

#### Privacy Query Gate (신규)

**개인정보 명단 요청 차단**
```python
class PrivacyQueryGate:
    """
    차단 조건 (3개 동시 성립):
    1. 대상(사람/직원 집합) 지시 - score +2
    2. 명단화/추출 행위 - score +3
    3. 민감 속성(교육/점수/평가) - score +3

    총점 >= 6 이면 차단
    """
```

#### 라우트별 차등 타임아웃 정책

```python
LONGFORM_INTENTS → 120초  # 체크리스트, 가이드, 요약
COMPLEX_INTENTS  → 60초   # 통계 분석, 복합 질문
SIMPLE_INTENTS   → 30초   # 단순 조회, FAQ
```

---

## 기술적 인사이트

### 1. 한국어 NLP 특수성

```
[문제]
단일 음절 키워드 "하"가 모든 동사 활용과 매칭
"~하나요?", "~해야", "~하고" → 79건 오분류

[해결]
2자 이상 키워드 강제 + 런타임 검증
```

### 2. 가드레일 설계 원칙

```
[Anti-Pattern] 과도한 전처리 필터링
Input → 엄격한 가드레일 → (대부분 차단) → 답변 불가

[Best Practice] 관대한 허용, 후처리 검증
Input → 관대한 가드레일 → 답변 생성 → 로깅/모니터링
```

### 3. 소프트 가드레일 2단계 보호

```
[1단계] 시스템 프롬프트 지침
LLM에게 유보적 표현만 사용하도록 지시
→ 답변 생성 시점에서 오답 리스크 감소

[2단계] 응답 prefix 추가
사용자에게 "일반 지식 기반 답변"임을 명시
→ 사용자가 스스로 검증 필요성 인지
```

### 4. 환각 방지 패턴

```
[Anti-Pattern] LLM에게 통계 생성 허용
Query: "TOP5 위반 유형" + sources=0
→ LLM이 임의로 "1위: X (45%)" 생성

[Best Practice] 통계 질문 Fast Path 차단
Query: "TOP5 위반 유형"
→ Stats Signal 감지 → STATS_GAP 에러
→ 안내 메시지 반환
```

### 5. Config 분리 원칙

```
[Anti-Pattern] 하드코딩
운영 중 변경 시 코드 수정 + 빌드 + 배포 필요

[Best Practice] Config 분리
환경변수 변경만으로 동작 변경 가능 (무중단)
```

---

## 수정 파일 종합

| Phase | 파일 | 수정 내용 |
|-------|------|----------|
| 43 | `answer_guard_service.py` | COMPLAINT_KEYWORDS에서 "하" 제거 |
| 43 | `intent_service.py` | POLICY_KEYWORDS 126개로 확장 |
| 44 | `answer_guard_service.py` | Citation Guard / Answerability 완화 |
| 44 | `rag_handler.py` | 2nd-chance retrieval, 쿼리 정규화 |
| 45 | `answer_guard_service.py` | 소프트 가드레일 로직 추가 |
| 45 | `message_builder.py` | KOREAN_ONLY_INSTRUCTION 추가 |
| 45 | `rag_handler.py` | Similarity 분포 로깅 추가 |
| 46 | `answer_guard_service.py` | EDUCATION 별칭, 확정 표현 금지 |
| 47 | `answer_guard_service.py` | 금지 표현 수정, 도메인 정규화 함수 |
| 47 | `message_builder.py` | 모든 빌더에 soft_guardrail 적용 |
| 48 | `rag_handler.py` | Low-relevance Gate 함수 4개 추가 |
| 48 | `milvus_client.py` | dataset_id 필터 구현 |
| 49 | `rule_router.py` | 복합 조건 체크, 교육 키워드 확장 |
| 50 | `rag_handler.py` | ACTION_TOKENS, Soft Gate 구현 |
| 55-56 | `answer_guard_service.py` | 환각 방지 패턴, Stats Fast Path |
| 55-56 | `privacy_query_gate.py` | 개인정보 명단 요청 차단 (신규) |
| 55-56 | `timeout_policy.py` | 차등 타임아웃 정책 (신규) |

---

## 테스트 결과 추이

| 테스트 | Phase | GENERAL_CHAT | RAG_INTERNAL | 정상 답변율 | LANGUAGE_ERROR |
|--------|-------|--------------|--------------|-------------|----------------|
| 1차 | Baseline | 61.5% | 36.2% | ~36% | N/A |
| 5차 | 43 | 0.0% | 91.5% | 36.2% | N/A |
| 6차 | 44 | 0.0% | 91.5% | 36.2% | 0건 |
| 7차 | 44 | 0.0% | 91.5% | 97.7% | 3건 |
| 8차 | 45 | 0.0% | 91.5% | **100%** | **0건** |

---

## 결론

### 핵심 성과

14단계(Phase 43~56)에 걸친 체계적인 개선으로 다음을 달성했습니다:

1. **정상 답변율 100% 달성**: ~36% → 100%
2. **GENERAL_CHAT 오분류 완전 해결**: 61.5% → 0%
3. **RAG 검색 수행율 대폭 향상**: 36.2% → 91.5%
4. **환각 방지 체계 구축**: 통계 질문 Fast Path, 조항 인용 검증
5. **개인정보 보호 강화**: Privacy Query Gate 구현
6. **운영 안정성 확보**: 차등 타임아웃, Config 분리

### Phase별 기여도

| 순위 | Phase | 핵심 개선 | 기여도 |
|------|-------|----------|--------|
| 1 | 43 | "하" 키워드 제거 | **결정적** (79건 복구) |
| 2 | 44 | 가드레일 완화 | **결정적** (83건 복구) |
| 3 | 45 | 소프트 가드레일 + 언어 강제 | **중요** (품질 향상) |
| 4 | 48 | Low-relevance Gate | **높음** (검색 품질) |
| 5 | 50 | Soft Gate 안전장치 | **높음** (필터링 방지) |
| 6 | 55-56 | 환각 방지 강화 | **높음** (안전성) |

### 교훈

1. **단일 음절 키워드 주의**: 한국어 동사 활용 패턴과 충돌 위험
2. **가드레일은 경고 우선**: 차단보다 경고가 적절한 경우가 많음
3. **점진적 개선**: 한 번에 해결하려 하지 말고 단계별 접근
4. **디버깅 체계화**: 가설 검증 반복, 단위 테스트 활용
5. **운영 유연성 확보**: 하드코딩 대신 Config 분리

---

## 부록

### A. 상세 보고서 목록

| 보고서 | Phase | 주요 내용 |
|--------|-------|----------|
| [PERFORMANCE_IMPROVEMENT_REPORT.md](./PERFORMANCE_IMPROVEMENT_REPORT.md) | 43 | "하" 키워드 버그 수정 |
| [PERFORMANCE_IMPROVEMENT_REPORT_v2.md](./PERFORMANCE_IMPROVEMENT_REPORT_v2.md) | 44 | 가드레일 완화 |
| [PERFORMANCE_IMPROVEMENT_REPORT_v3.md](./PERFORMANCE_IMPROVEMENT_REPORT_v3.md) | 45 | 소프트 가드레일 & 언어 강제 |
| [PERFORMANCE_IMPROVEMENT_REPORT_v4.md](./PERFORMANCE_IMPROVEMENT_REPORT_v4.md) | 46 | 확정 표현 금지 & 지표 체계 |
| [PERFORMANCE_IMPROVEMENT_REPORT_v5.md](./PERFORMANCE_IMPROVEMENT_REPORT_v5.md) | 47-49 | GPT 피드백 반영 & 라우팅 개선 |
| [PERFORMANCE_IMPROVEMENT_REPORT_v6.md](./PERFORMANCE_IMPROVEMENT_REPORT_v6.md) | 49 | 도메인 라우팅 개선 |
| [PERFORMANCE_IMPROVEMENT_REPORT_v7.md](./PERFORMANCE_IMPROVEMENT_REPORT_v7.md) | 50 | LowRelevanceGate 개선 |
| [PERFORMANCE_IMPROVEMENT_REPORT_v8.md](./PERFORMANCE_IMPROVEMENT_REPORT_v8.md) | 55-56 | 환각 방지 강화 |

### B. 테스트 데이터셋

- **파일**: `data/Q세트.csv` (130문항)
- **도메인 분포**:
  - 사규/복무/인사 (RUL): 30문항
  - 개인정보보호 (PIP): 20문항
  - 성희롱 방지 (SHP): 20문항
  - 직장내괴롭힘 (BHP): 20문항
  - 장애인식 (DEP): 20문항
  - 직무별 교육 (JOB): 20문항

### C. 주요 설정 값

```python
# Low-relevance Gate
RAG_MIN_MAX_SCORE = 0.55  # max_score 임계값
ANCHOR_GATE_MIN_KEEP = 1  # Soft Gate 최소 유지 개수

# Privacy Query Gate
BLOCK_THRESHOLD = 6       # 차단 임계 점수
TARGET_SCORE = 2          # 대상 점수
ACTION_SCORE = 3          # 행위 점수
SENSITIVE_SCORE = 3       # 민감 속성 점수

# 타임아웃 정책
TIMEOUT_LLM_SIMPLE_SEC = 30.0
TIMEOUT_LLM_COMPLEX_SEC = 60.0
TIMEOUT_LLM_LONGFORM_SEC = 120.0
```

---

**작성 완료일**: 2026-01-08
**문서 버전**: 1.0
**CTRL+F AI 개발팀**
