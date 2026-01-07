# AI 채팅 서비스 성능 개선 보고서 - Phase 55-56

## 1. 개요

### 1.1 프로젝트 배경
본 보고서는 CTRL+F AI 채팅 서비스의 **환각 방지 강화 및 운영 안정화** 작업을 기술합니다. Phase 50에서 LowRelevanceGate 개선 이후, 운영 환경에서 발견된 환각(Hallucination) 문제와 개인정보 보호 요구사항을 해결하기 위해 다양한 가드레일을 추가하였습니다.

### 1.2 개선 목표
| 목표 | 상세 |
|------|------|
| 통계 질문 환각 방지 | 통계/순위 질문에 대한 허위 데이터 생성 차단 |
| 개인정보 명단 요청 차단 | 타인 인사정보 요청을 RAG/LLM 호출 전에 차단 |
| 타임아웃 정책 최적화 | 라우트/질문 난이도별 차등 타임아웃 적용 |
| 도메인 구조 확장 | 6개 도메인으로 확장 (기존 3개 → 6개) |
| 개인화 기능 확장 | Q12(연차 이력), Q15(복지 포인트) 추가 |
| 운영 환경 개선 | 하드코딩된 IP 주소를 환경변수로 변경 |

### 1.3 Phase 히스토리
| Phase | 주요 내용 | 상태 |
|-------|----------|------|
| Phase 50 | LowRelevanceGate 개선 + Soft Gate | 완료 |
| **Phase 55** | **환각 방지 테스트 및 설정 강화** | **완료** |
| **Phase 56** | **통계 질문 할루시네이션 방지** | **완료** |

---

## 2. 문제 분석

### 2.1 발견된 문제

Phase 50 이후 운영 환경에서 다음과 같은 문제가 발견되었습니다:

| 쿼리 유형 | 증상 | 원인 |
|-----------|------|------|
| "최근 3년간 보안 사고 TOP5" | 허위 통계 생성 | RAG sources 없이 LLM이 통계 데이터 생성 |
| "교육 미이수자 명단 뽑아줘" | 개인정보 노출 위험 | 개인정보성 요청이 필터링 없이 처리 |
| 복잡한 질문 | 타임아웃 발생 | 모든 요청에 동일한 타임아웃 적용 |
| "우리 부서 퀴즈 점수 낮은 사람" | 타인 인사정보 요청 | PII 차단 로직 부재 |

### 2.2 근본 원인 분석

#### 원인 1: 통계/순위 질문에 대한 환각

```python
# 문제: RAG sources가 없어도 LLM이 통계 데이터를 생성
# "최근 3년간 TOP5 위반 유형"이라는 질문에 허위 데이터 생성
LLM 응답: "1위: 개인정보 유출 (45%), 2위: 무단 접근 (23%)..."
# → 실제 데이터 없이 생성된 환각
```

**문제**: 통계/순위 표현이 포함된 질문에 RAG 근거 없이 LLM이 임의로 수치를 생성

#### 원인 2: 개인정보성 명단 요청 무방비

```python
# 문제: 타인의 인사정보를 요청하는 쿼리가 필터링 없이 처리
user_query = "교육 미이수자 명단 뽑아줘"
# → RAG 검색 → LLM 응답 → 개인정보 노출 위험
```

**문제**: 개인정보 보호 게이트가 없어 타인 인사정보 요청이 그대로 처리됨

#### 원인 3: 단일 타임아웃 정책

```python
# 기존: 모든 요청에 30초 타임아웃
LLM_TIMEOUT = 30  # 단순 질문도 30초, 복잡 질문도 30초

# 문제:
# - 장문 생성 (체크리스트, 요약) → 30초 부족 → 타임아웃
# - 단순 조회 → 30초 대기 → 불필요한 지연
```

**문제**: 질문 난이도와 무관하게 동일한 타임아웃 적용

---

## 3. 개선 작업

### 3.1 Phase 55: 환각 방지 테스트 및 설정 강화

#### 3.1.1 HALLUCINATION_GUARD 설정 추가

**파일**: `app/core/config.py`

```python
# Phase 55: 환각 방지 설정
HALLUCINATION_GUARD_ENABLED: bool = True
HALLUCINATION_GUARD_STRICT: bool = False  # 엄격 모드 (개발용)
```

#### 3.1.2 통계/순위 환각 패턴 정의

**파일**: `app/services/answer_guard_service.py`

```python
# [G] 통계/순위 관련 환각 패턴 (RAG sources 없이 이런 주장은 환각일 가능성 높음)
STATISTICAL_CLAIM_PATTERNS = [
    r"TOP\s*\d+",
    r"가장\s*많이",
    r"가장\s*빈번",
    r"\d+\s*위",
    r"순위",
    r"통계에\s*따르면",
    r"약?\s*\d+\s*%",      # "45%", "약 45%" 등 퍼센트 표현
    r"평균적으로",
    r"대다수",
]

STATISTICAL_CLAIM_REGEX = re.compile(
    "|".join(STATISTICAL_CLAIM_PATTERNS),
    re.IGNORECASE
)
```

#### 3.1.3 SYSTEM_PROMPT_NO_RAG 강화

**파일**: `app/services/chat/message_builder.py`

```python
SYSTEM_PROMPT_NO_RAG = """
당신은 사내 정책 안내 AI입니다.

## 중요 제약사항
1. 제공된 문서 내용만을 기반으로 답변하세요
2. 통계, 수치, 순위 정보는 문서에 명시된 경우에만 언급하세요
3. "약 X%", "TOP N", "N위" 같은 표현은 문서 근거 없이 사용하지 마세요
4. 확신이 없으면 "확인이 필요합니다"라고 안내하세요
"""
```

---

### 3.2 Phase 56: 통계 질문 할루시네이션 방지

#### 3.2.1 Stats Out-of-Scope Fast Path 추가

**파일**: `app/services/answer_guard_service.py`

```python
# [H] "순수 통계" 신호: 기간/랭킹/집계 표현 (문서 Q&A로 답 불가)
STATS_SIGNAL_PATTERNS = [
    r"최근\s*\d+\s*(년|개월|달|주|일)",
    r"지난\s*\d+\s*(년|개월|달|주|일)",
    r"\bTOP\s*\d+\b",
    r"상위\s*\d+",
    r"\d+\s*위",
    r"(통계|건수|횟수|비율|분포|추이|랭킹|순위)",
    r"(가장|최다|최고|최저)\s*(많|적|높|낮|자주|빈번)",
]

# Incident 도메인 통계 질문 키워드
INCIDENT_METRIC_PATTERNS = [
    r"(위반|보안\s*사고|사고|유출|침해|피해|해킹)",
]
```

#### 3.2.2 Stats Language Sanitizer 구현

```python
# [I] 시간/통계 표현 치환 규칙 (Sanitizer용)
STATS_REPLACEMENTS = [
    (re.compile(r"(최근|지난)\s*\d+\s*(년|개월|달|주|일)\s*(동안|간)?"), "일반적으로"),
    (re.compile(r"\bTOP\s*\d+\b", re.IGNORECASE), "주요"),
    (re.compile(r"(상위)\s*\d+"), "주요"),
    (re.compile(r"\d+\s*위"), "주요"),
    (re.compile(r"(가장)\s*(많이|자주)"), "자주"),
]
```

#### 3.2.3 STATS_GAP 에러 라벨 추가

```python
class AnswerGuardError(str, Enum):
    NO_RAG_EVIDENCE = "NO_RAG_EVIDENCE"
    CITATION_HALLUCINATION = "CITATION_HALLUCINATION"
    LANGUAGE_ERROR = "LANGUAGE_ERROR"
    REQUEST_ID_MISMATCH = "REQUEST_ID_MISMATCH"
    STATS_GAP = "STATS_GAP"  # Phase 56: 통계/집계 데이터 부족
```

#### 3.2.4 통계 질문 Out-of-Scope 응답 템플릿

```python
# Phase 56: 통계 질문 Out-of-Scope 템플릿
STATS_OUT_OF_SCOPE = (
    "요청하신 '최근 N기간/Top N' 위반 통계는 현재 시스템에 집계 데이터가 없어 "
    "제공할 수 없습니다.\n\n"
    "대신 '자주 언급되는 위반 유형(안내문서 기준)' 요약은 제공할 수 있어요.\n"
    "필요하시면 다시 질문해 주세요."
)
```

---

### 3.3 Privacy Query Gate 구현

#### 3.3.1 개인정보 명단 요청 차단 게이트

**파일**: `app/services/privacy_query_gate.py` (신규)

```python
class PrivacyQueryGate:
    """
    개인정보성 명단 요청을 차단하는 게이트

    차단 조건 (3개 동시 성립):
    1. 대상(사람/직원 집합) 지시 - score +2
    2. 명단화/추출 행위 - score +3
    3. 민감 속성(교육/점수/평가) - score +3

    총점 >= 6 이면 차단

    허용 조건:
    - 1인칭(내/저) 중심의 개인화 조회
    """
```

#### 3.3.2 키워드 사전 정의

```python
# 대상(사람/직원 집합) 지시어
TARGET_PEOPLE_TERMS: Set[str] = {
    "직원", "사원", "팀원", "부서원", "동료", "인원",
    "담당자", "실무자", "근무자", "재직자",
    "미이수자", "수료자", "대상자", "저성과자",
}

# 명단화/추출 행위 동사
LIST_ACTION_TERMS: Set[str] = {
    "리스트", "명단", "목록", "현황",
    "뽑아", "추출", "조회", "확인", "보여",
    "랭킹", "순위", "상위", "하위",
}

# 민감 속성 (교육/점수/평가/징계 등)
SENSITIVE_ATTRIBUTE_TERMS: Set[str] = {
    "교육", "이수", "미이수", "수료", "진도",
    "퀴즈", "점수", "성적", "평가", "결과",
    "성과", "실적", "등급", "고과",
}
```

#### 3.3.3 표준 차단 응답

```python
PRIVACY_BLOCK_RESPONSE = """요청하신 내용은 특정 직원의 교육 이수 여부나 퀴즈 점수처럼 **개인 식별이 가능한 인사·교육 정보**를 포함할 수 있어 제공할 수 없습니다.

대신 다음과 같은 방법으로 도움드릴 수 있어요:
- **본인 정보 조회**: 본인의 교육/퀴즈 현황은 조회해 드릴 수 있습니다.
- **익명화된 통계**: 조직 단위로는 부서 평균, 분포, 미이수 인원 수 등 집계 형태로만 안내 가능합니다.

본인 교육 현황이나 조직 통계가 필요하시면 다시 질문해 주세요."""
```

---

### 3.4 라우트별 차등 타임아웃 정책

#### 3.4.1 timeout_policy.py 신규 생성

**파일**: `app/services/chat/timeout_policy.py`

```python
# 장문 생성이 필요한 인텐트 (체크리스트, 가이드, 요약 등)
LONGFORM_INTENTS: Set[str] = {
    "Q13",  # 교육 자료 요약
    "Q14",  # 체크리스트 생성
    "CHECKLIST", "GUIDE", "SUMMARY", "REPORT",
}

# 복잡한 쿼리가 예상되는 인텐트
COMPLEX_INTENTS: Set[str] = {
    "Q05",  # 부서별 교육 현황 통계
    "Q06",  # 사고 통계
    "Q12",  # 연차 사용 이력
    "Q15",  # 복지 포인트 사용 내역
}

# 단순 조회 인텐트 (빠른 응답 기대)
SIMPLE_INTENTS: Set[str] = {
    "Q01", "Q02", "Q03", "Q04",
    "FAQ", "GREETING",
}
```

#### 3.4.2 타임아웃 결정 함수

```python
def pick_llm_timeout(settings: "Settings", ctx: TimeoutContext) -> float:
    if ctx.is_longform:
        return settings.TIMEOUT_LLM_LONGFORM_SEC  # 120초
    if ctx.is_complex:
        return settings.TIMEOUT_LLM_COMPLEX_SEC   # 60초
    return settings.TIMEOUT_LLM_SIMPLE_SEC        # 30초

def pick_rag_timeout(settings: "Settings", ctx: TimeoutContext) -> float:
    if ctx.is_longform:
        return settings.TIMEOUT_RAG_LONGFORM_SEC  # 30초
    if ctx.is_complex:
        return settings.TIMEOUT_RAG_COMPLEX_SEC   # 20초
    return settings.TIMEOUT_RAG_SIMPLE_SEC        # 10초
```

---

### 3.5 도메인 구조 6개로 확장

**파일**: `app/clients/milvus_client.py`, `.env.example`

```
기존 3개 도메인:
- POLICY (사내규정)
- EDUCATION (교육)
- INCIDENT (사건/사고)

확장된 6개 도메인:
- 사내규정
- 직무교육
- 장애인인식개선교육
- 직장내괴롭힘교육
- 직장내성희롱교육
- 정보보안교육
```

**효과**: 교육 도메인을 세분화하여 더 정확한 RAG 검색 지원

---

### 3.6 Q12, Q15 개인화 기능 추가

**파일**: `app/clients/personalization_client.py`, `app/services/answer_generator.py`

| 인텐트 | 기능 | 데이터 소스 |
|--------|------|------------|
| Q12 | 연차 사용 이력 조회 | Backend API |
| Q15 | 복지 포인트 사용 내역 조회 | Backend API |

```python
# PersonalizationClient에 topic 파라미터 추가
async def get_leave_history(
    self,
    employee_id: str,
    year: Optional[int] = None,
) -> LeaveHistoryResponse:
    """Q12: 연차 사용 이력 조회"""
    ...

async def get_welfare_points(
    self,
    employee_id: str,
) -> WelfarePointsResponse:
    """Q15: 복지 포인트 사용 내역 조회"""
    ...
```

---

### 3.7 운영 환경 개선

#### 3.7.1 하드코딩된 IP 주소를 환경변수로 변경

**파일**: `app/core/config.py`, `.env.example`

```python
# Before (하드코딩)
MILVUS_HOST = "58.127.241.84"
LLM_BASE_URL = "http://58.127.241.84:1237"

# After (환경변수)
MILVUS_HOST: str = os.getenv("MILVUS_HOST", "localhost")
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "http://localhost:1237")
ALLOWED_EXTERNAL_LLM_HOSTS: str = os.getenv("ALLOWED_EXTERNAL_LLM_HOSTS", "")
```

#### 3.7.2 department 필드 없을 때 Milvus 쿼리 에러 수정

**파일**: `app/clients/milvus_client.py`

```python
# Before: department 필드 없으면 에러 발생
def get_document_chunks(self, doc_id: str, department: Optional[str] = None):
    # department가 스키마에 없으면 쿼리 실패

# After: 스키마 확인 후 조건부 필터
def get_document_chunks(self, doc_id: str, department: Optional[str] = None):
    # 스키마에 department 필드가 있는지 확인
    if self._has_department_field() and department:
        filter_expr = f'doc_id == "{doc_id}" and department == "{department}"'
    else:
        filter_expr = f'doc_id == "{doc_id}"'
```

---

## 4. 테스트 결과

### 4.1 테스트 환경
- **Phase 56 테스트**: `tests/unit/test_phase39_answer_guard.py` (회귀 테스트 12종 추가)
- **Privacy Gate 테스트**: `tests/unit/test_privacy_query_gate.py` (245개 테스트 케이스)
- **환각 방지 테스트**: `scripts/test_hallucination_guard.py`

### 4.2 Phase 56 통계 질문 테스트 결과

| 쿼리 | 기대 동작 | 결과 |
|------|-----------|------|
| "최근 3년간 보안 사고 TOP5" | STATS_GAP 차단 | **PASS** |
| "지난 1년간 위반 통계" | STATS_GAP 차단 | **PASS** |
| "상위 10개 위반 유형" | STATS_GAP 차단 | **PASS** |
| "보안 사고 신고 절차" | 정상 처리 (신고는 통계 아님) | **PASS** |

### 4.3 Privacy Query Gate 테스트 결과

| 쿼리 | 기대 동작 | 결과 |
|------|-----------|------|
| "교육 미이수자 명단 뽑아줘" | BLOCK_PII_LIST | **PASS** |
| "우리 팀 퀴즈 점수 낮은 사람" | BLOCK_PII_LIST | **PASS** |
| "직원 성과 순위" | BLOCK_PII_LIST | **PASS** |
| "내 교육 현황" | ALLOW (1인칭) | **PASS** |
| "본인 퀴즈 점수" | ALLOW (1인칭) | **PASS** |

### 4.4 타임아웃 정책 테스트 결과

| 인텐트 | 타임아웃 | 결과 |
|--------|----------|------|
| Q01 (단순 정책 조회) | 30초 | 정상 응답 |
| Q12 (연차 이력) | 60초 | 정상 응답 |
| Q13 (교육 자료 요약) | 120초 | 정상 응답 |

---

## 5. 기술적 인사이트

### 5.1 환각 방지 패턴

```
[Anti-Pattern] LLM에게 통계 생성 허용
Query: "TOP5 위반 유형" + sources=0
→ LLM이 임의로 "1위: X (45%)" 생성
→ 사실과 다른 환각

[Best Practice] 통계 질문 Fast Path 차단
Query: "TOP5 위반 유형"
→ Stats Signal 감지
→ STATS_GAP 에러로 조기 차단
→ 안내 메시지 반환
```

### 5.2 개인정보 보호 게이트 패턴

```
[Anti-Pattern] 모든 요청을 RAG/LLM으로 처리
Query: "교육 미이수자 명단"
→ RAG 검색 → LLM 응답 → 개인정보 노출 위험

[Best Practice] 점수 기반 조합 규칙 차단
Query: "교육 미이수자 명단"
→ 대상(미이수자) +2점
→ 행위(명단) +3점
→ 속성(교육) +3점
→ 총 8점 >= 6점 → 차단
```

**구현**:
```python
if result.score_total >= self.block_threshold:
    result.decision = PrivacyGateDecision.BLOCK_PII_LIST
    result.block_response = PRIVACY_BLOCK_RESPONSE
```

### 5.3 차등 타임아웃 패턴

```
[Anti-Pattern] 단일 타임아웃
모든 요청 → 30초 타임아웃
→ 장문 생성 → 타임아웃 실패
→ 단순 조회 → 불필요한 대기

[Best Practice] 인텐트 기반 차등 타임아웃
장문 인텐트 → 120초
복잡 인텐트 → 60초
단순 인텐트 → 30초
```

### 5.4 도메인 세분화 패턴

```
[Anti-Pattern] 광범위한 도메인
EDUCATION 도메인
→ 장애인인식, 성희롱, 괴롭힘, 직무교육 모두 포함
→ RAG 검색 정확도 저하

[Best Practice] 세분화된 도메인
6개 도메인으로 분리
→ dataset_id 필터 정확도 향상
→ RAG 검색 품질 개선
```

---

## 6. 결론

### 6.1 성과 요약

| 항목 | 구현 내용 | 효과 |
|------|----------|------|
| Stats Fast Path | 통계 질문 조기 차단 | 환각 응답 방지 |
| Stats Sanitizer | 시간/통계 표현 치환 | 후처리 환각 제거 |
| Privacy Query Gate | 점수 기반 PII 차단 | 개인정보 보호 |
| 차등 타임아웃 | 인텐트별 타임아웃 | 안정성 향상 |
| 도메인 확장 | 3개 → 6개 | RAG 정확도 향상 |
| Q12/Q15 | 개인화 기능 추가 | 사용자 경험 향상 |
| 환경변수화 | IP 하드코딩 제거 | 운영 유연성 향상 |

### 6.2 핵심 개선 요인

| 순위 | 개선 항목 | 기여도 |
|------|----------|--------|
| 1 | Stats Fast Path | **결정적** (통계 환각 방지) |
| 2 | Privacy Query Gate | **높음** (개인정보 보호) |
| 3 | 차등 타임아웃 | **중간** (안정성 향상) |
| 4 | 도메인 세분화 | **보조** (RAG 품질 향상) |

### 6.3 개선 전후 비교

| 지표 | Before (Phase 50) | After (Phase 56) |
|------|-------------------|------------------|
| 통계 환각 응답 | 발생 가능 | **차단됨** |
| PII 명단 요청 | 처리됨 | **차단됨** |
| 타임아웃 정책 | 단일 30초 | **인텐트별 30/60/120초** |
| 도메인 수 | 3개 | **6개** |
| 개인화 인텐트 | 10개 | **14개 (Q12, Q15 등 추가)** |

### 6.4 향후 개선 방향

1. **ML 기반 환각 탐지**: 규칙 기반 → ML 모델 기반 환각 탐지
2. **동적 타임아웃 조정**: 응답 패턴 학습 기반 타임아웃 자동 조정
3. **Privacy Gate 고도화**: 형태소 분석 기반 정밀 차단
4. **도메인 자동 분류**: 문서 내용 기반 도메인 자동 태깅

---

## 부록

### A. 수정 파일 목록

| 파일 | 수정 내용 |
|------|----------|
| `app/services/answer_guard_service.py` | Phase 55/56 환각 방지 패턴, Stats Fast Path |
| `app/services/privacy_query_gate.py` | Privacy Query Gate 신규 구현 |
| `app/services/chat/timeout_policy.py` | 차등 타임아웃 정책 신규 구현 |
| `app/services/chat/message_builder.py` | SYSTEM_PROMPT_NO_RAG 강화 |
| `app/core/config.py` | HALLUCINATION_GUARD 설정, 환경변수화 |
| `app/clients/milvus_client.py` | 도메인 구조 변경, department 에러 수정 |
| `app/clients/personalization_client.py` | Q12, Q15 기능 추가 |

### B. 테스트 파일

| 파일 | 테스트 수 | 내용 |
|------|----------|------|
| `test_phase39_answer_guard.py` | 12+ | 환각 방지 회귀 테스트 |
| `test_privacy_query_gate.py` | 245 | Privacy Gate 전체 테스트 |
| `test_hallucination_guard.py` | 스크립트 | 환각 방지 통합 테스트 |

### C. 관련 상수 및 설정

```python
# app/core/config.py
HALLUCINATION_GUARD_ENABLED: bool = True
TIMEOUT_LLM_SIMPLE_SEC: float = 30.0
TIMEOUT_LLM_COMPLEX_SEC: float = 60.0
TIMEOUT_LLM_LONGFORM_SEC: float = 120.0

# app/services/privacy_query_gate.py
BLOCK_THRESHOLD: int = 6
TARGET_SCORE: int = 2
ACTION_SCORE: int = 3
SENSITIVE_SCORE: int = 3

# app/services/answer_guard_service.py
STATS_SIGNAL_PATTERNS = [...]
STATISTICAL_CLAIM_PATTERNS = [...]
```

### D. 파이프라인 흐름도

```
┌─────────────────────────────────────────────────────────┐
│                    User Query 입력                       │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                  Privacy Query Gate                      │
│           (개인정보 명단 요청 점수 계산)                  │
└─────────────────────────────────────────────────────────┘
                     │              │
              score < 6        score >= 6
                     │              │
                     ▼              ▼
              ┌──────────┐   ┌──────────────────────┐
              │ 다음 단계 │   │ BLOCK_PII_LIST       │
              │ 진행     │   │ 차단 응답 반환       │
              └──────────┘   └──────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                 Stats Signal 체크                        │
│       (통계/순위 질문 + INCIDENT 도메인)                 │
└─────────────────────────────────────────────────────────┘
                     │              │
              Stats 아님        Stats 감지
                     │              │
                     ▼              ▼
              ┌──────────┐   ┌──────────────────────┐
              │ 다음 단계 │   │ STATS_GAP            │
              │ 진행     │   │ Out-of-Scope 응답    │
              └──────────┘   └──────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│               타임아웃 컨텍스트 결정                     │
│        (장문/복잡/단순 인텐트 분류)                      │
└─────────────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│            RAG 검색 + LLM 생성 (차등 타임아웃)           │
└─────────────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              환각 후처리 (Stats Sanitizer)               │
│         (시간/통계 표현 치환 + prefix 추가)              │
└─────────────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│                    최종 응답 반환                        │
└─────────────────────────────────────────────────────────┘
```

---

**작성일**: 2026-01-08
**작성자**: CTRL+F AI 개발팀
**커밋 범위**: `871caf4` ~ `260f789` (Phase 50 이후 ~ 현재)
