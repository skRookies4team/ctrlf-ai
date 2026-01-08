# Conversation State 설계 문서

## 개요

멀티턴 대화에서 맥락을 유지하기 위한 ConversationState 시스템 설계.
"최근 N턴 히스토리 + 구조화 상태 슬롯" 조합으로 토큰 효율성과 맥락 정확도를 동시에 확보.

---

## A. 저장소 설계 (Storage)

### A.1 키 정책

```
키 형식: conversation_state:{user_id}:{session_id}
```

- `user_id` + `session_id` 조합으로 스푸핑/공유 위험 방지
- `session_id` 단독 사용 금지 (보안 취약)

### A.2 저장소 선택

| 환경 | 저장소 | 이유 |
|------|--------|------|
| **Production** | Redis | 멀티 인스턴스 대응, 재시작 시 상태 유지 |
| **Development** | In-Memory (Dict) | 단일 인스턴스, 빠른 개발 |
| **Test** | In-Memory (Mock) | 격리된 테스트 |

```python
# 설정으로 전환 가능
STATE_STORE_BACKEND: Literal["redis", "memory"] = "memory"
STATE_STORE_REDIS_URL: Optional[str] = None
```

### A.3 TTL 정책

```python
STATE_TTL_SECONDS: int = 3600  # 기본 60분
STATE_TTL_SLIDING: bool = True  # 활동 시 TTL 갱신
STATE_TTL_MAX_SECONDS: int = 7200  # 최대 2시간 (sliding 상한)
```

- **Sliding TTL**: 사용자 활동(메시지 전송) 시 TTL 리셋
- **상한선**: 무한 연장 방지 (2시간 후 강제 만료)
- **pending_action**: 기존 5분 TTL 유지 (별도 관리)

---

## B. 상태 갱신 규칙 (Update Policy)

### B.1 신뢰도 메타 저장

상태 슬롯에 "왜 이 값이 저장됐는지"를 함께 기록:

```python
@dataclass
class DocReference:
    """문서 참조 정보 (신뢰도 메타 포함)"""
    doc_id: str
    title: str
    domain: str

    # 신뢰도 메타
    score: float  # retrieval score
    reason: DocReferenceReason  # 갱신 사유
    turn: int  # 몇 번째 턴에서 확정됐는지

class DocReferenceReason(Enum):
    USER_SELECTED = "user_selected"  # 사용자가 명시적 선택
    RAG_TOP1_HIGH = "rag_top1_high"  # RAG top1이 고신뢰
    RAG_TOP1_LOW = "rag_top1_low"    # RAG top1이 저신뢰 (보류 상태)
    FALLBACK_FILTER = "fallback_filter"  # fallback으로 잡힘
    ANAPHORA_RESOLVED = "anaphora_resolved"  # 지시어 해소로 확정
```

### B.2 갱신 조건 우선순위

```
1. USER_SELECTED (최우선)
   - 사용자가 되묻기에서 명시적으로 선택한 경우
   - 무조건 갱신, 이전 상태 덮어쓰기

2. RAG_TOP1_HIGH
   - top1 score >= HIGH_CONFIDENCE_THRESHOLD (예: 0.75)
   - top1 - top2 격차 >= GAP_THRESHOLD (예: 0.1)
   - 갱신 허용

3. RAG_TOP1_LOW
   - top1 score < HIGH_CONFIDENCE_THRESHOLD
   - "보류 상태"로 저장 (부스팅에는 사용하지만 갱신 강도 약함)
   - 다음 턴에서 더 좋은 결과 나오면 덮어쓰기 허용

4. FALLBACK_FILTER
   - doc_id filter로만 잡힌 결과
   - 갱신하지 않음 (기존 상태 유지)
   - 오답 고착 방지
```

### B.3 갱신 안전장치

```python
def should_update_state(
    current: Optional[DocReference],
    candidate: DocReference,
) -> bool:
    """상태 갱신 여부 판단"""

    # 1. USER_SELECTED는 항상 갱신
    if candidate.reason == DocReferenceReason.USER_SELECTED:
        return True

    # 2. FALLBACK_FILTER는 갱신 안 함
    if candidate.reason == DocReferenceReason.FALLBACK_FILTER:
        return False

    # 3. 기존 상태가 없으면 갱신
    if current is None:
        return True

    # 4. 기존이 USER_SELECTED면 HIGH 이상만 덮어쓰기 가능
    if current.reason == DocReferenceReason.USER_SELECTED:
        return candidate.reason in (
            DocReferenceReason.USER_SELECTED,
            DocReferenceReason.RAG_TOP1_HIGH,
        )

    # 5. 신뢰도 비교
    return candidate.score > current.score
```

---

## C. 라우터 결과 단일 진실 (Single Source of Truth)

### C.1 원칙

> **현재 턴의 도메인/인텐트는 라우터 결과를 유일한 기준으로 사용**

- `classify_domain_quick()` 같은 별도 분류는 "라우터 호출 전 임시 판단"으로만 사용
- 부스팅/지시어 해소/상태 갱신은 모두 라우터 결과를 따름

### C.2 파이프라인 순서

```
사용자 쿼리
    ↓
[1] 라우팅 (RouterOrchestrator)
    → domain, intent 확정
    → state.current_domain, state.current_intent 설정
    ↓
[2] 토픽 전환 감지
    → state.last_domain != current_domain 이면 토픽 전환
    → 부스팅 비활성화, recent_docs 초기화 고려
    ↓
[3] 지시어 해소 + RAG 검색
    → 라우터 결과 기반으로 처리
    ↓
[4] 응답 생성
    ↓
[5] 상태 업데이트
    → current_domain → last_domain
    → RAG 결과 → recent_docs
```

### C.3 토픽 전환 처리

```python
def detect_topic_switch(
    state: ConversationState,
    current_domain: str,
    current_intent: str,
) -> TopicSwitchResult:
    """토픽 전환 감지 (라우터 결과 기반)"""

    if state.last_domain is None:
        return TopicSwitchResult(switched=False, action="none")

    if state.last_domain != current_domain:
        return TopicSwitchResult(
            switched=True,
            action="reset_boost",  # 부스팅 비활성화
            reason=f"domain_change:{state.last_domain}→{current_domain}",
        )

    # 같은 도메인 내에서 인텐트가 크게 다르면 부분 전환
    if is_major_intent_change(state.last_intent, current_intent):
        return TopicSwitchResult(
            switched=True,
            action="decay_boost",  # 부스팅 약화
            reason=f"intent_change:{state.last_intent}→{current_intent}",
        )

    return TopicSwitchResult(switched=False, action="none")
```

---

## D. Recent Docs 스택 (Multi-Doc Reference)

### D.1 구조

```python
@dataclass
class ConversationState:
    # ... 기존 필드 ...

    # D: Recent docs 스택 (최근 3~5개)
    recent_docs: List[DocReference] = field(default_factory=list)
    RECENT_DOCS_MAX_SIZE: ClassVar[int] = 5
```

### D.2 스택 관리

```python
def add_recent_doc(self, doc: DocReference) -> None:
    """recent_docs에 문서 추가 (중복 제거, 크기 제한)"""

    # 중복 제거 (같은 doc_id면 갱신)
    self.recent_docs = [d for d in self.recent_docs if d.doc_id != doc.doc_id]

    # 앞에 추가 (최신이 앞)
    self.recent_docs.insert(0, doc)

    # 크기 제한
    if len(self.recent_docs) > self.RECENT_DOCS_MAX_SIZE:
        self.recent_docs = self.recent_docs[:self.RECENT_DOCS_MAX_SIZE]
```

### D.3 사용처

1. **후보잠금 LLM**: `recent_docs`를 후보 목록으로 제공
2. **되묻기 옵션**: `recent_docs`에서 선택지 구성
3. **부스팅**: `recent_docs[0]` (가장 최근) 기준으로 부스팅

---

## E. 검색 병합 전략 (Merge Strategy)

### E.1 원칙

> **fallback_with_boost(doc_id filter)는 단독 반환 금지, 항상 병합 후보로만 사용**

### E.2 병합 알고리즘

```python
def search_with_merge(
    query: str,
    state: ConversationState,
    top_k: int = 5,
) -> List[SearchResult]:
    """일반 검색 + filter 검색 → merge + rerank"""

    # 1차: 일반 검색
    general_results = milvus_search(query, top_k=top_k * 2)

    # 2차: doc_id filter 검색 (조건 충족 시에만)
    filter_results = []
    if should_apply_boost(query, state):
        for doc in state.recent_docs[:2]:  # 최근 2개만
            filtered = milvus_search(
                query,
                top_k=3,
                filter_expr=f"doc_id == '{doc.doc_id}'"
            )
            filter_results.extend(filtered)

    # 3차: 병합 + 중복 제거
    merged = merge_results(general_results, filter_results)

    # 4차: Rerank (score 기준, 동점 시 general 우선)
    reranked = rerank_with_priority(merged, priority="general")

    # 5차: Rank bump (조건부)
    if should_apply_boost(query, state):
        reranked = apply_rank_bump(reranked, state.recent_docs[0].doc_id)

    return reranked[:top_k]
```

### E.3 Rank Bump 규칙

```python
def apply_rank_bump(
    results: List[SearchResult],
    target_doc_id: str,
    max_bump: int = 2,
) -> List[SearchResult]:
    """순위 기반 승급 (점수 가산 아님)"""

    target_idx = next(
        (i for i, r in enumerate(results) if r.doc_id == target_doc_id),
        None
    )

    if target_idx is None or target_idx <= 1:
        return results  # 없거나 이미 top-2

    # 최대 2칸만 위로 (1등은 건드리지 않음)
    new_idx = max(target_idx - max_bump, 1)

    result = results.copy()
    item = result.pop(target_idx)
    result.insert(new_idx, item)

    return result
```

---

## F. 저품질 결과 판정 (Quality Gate)

### F.1 복합 지표

단순 keyword coverage 대신 3가지 지표 조합:

```python
@dataclass
class QualityAssessment:
    """검색 결과 품질 평가"""

    # 지표 1: Top1 점수 하한
    top1_score: float
    top1_threshold: float = 0.55  # RAG_MIN_MAX_SCORE

    # 지표 2: Top1-Top2 격차
    score_gap: float
    gap_threshold: float = 0.05

    # 지표 3: Keyword coverage (보조)
    keyword_coverage: float
    coverage_threshold: float = 0.3

    @property
    def is_low_quality(self) -> bool:
        """저품질 여부 판정"""
        # 점수 기반 (primary)
        if self.top1_score < self.top1_threshold:
            return True

        # 격차 기반 (애매한 결과)
        if self.score_gap < self.gap_threshold and self.top1_score < 0.7:
            return True

        # keyword coverage (보조, 위 조건과 AND)
        # 단독으로는 저품질 판정 안 함

        return False

    @property
    def action(self) -> QualityAction:
        """품질에 따른 액션"""
        if self.top1_score < 0.4:
            return QualityAction.FALLBACK_BOOST  # 부스팅 재검색
        elif self.is_low_quality:
            return QualityAction.CLARIFY  # 되묻기 고려
        else:
            return QualityAction.PROCEED  # 정상 진행
```

### F.2 품질 평가 로직

```python
def assess_quality(
    results: List[SearchResult],
    query: str,
) -> QualityAssessment:
    """검색 결과 품질 평가"""

    if not results:
        return QualityAssessment(
            top1_score=0.0,
            score_gap=0.0,
            keyword_coverage=0.0,
        )

    top1_score = results[0].score
    top2_score = results[1].score if len(results) > 1 else 0.0
    score_gap = top1_score - top2_score

    # Keyword coverage (보조 지표)
    keywords = extract_keywords(query)
    if keywords:
        top3_content = " ".join(r.content for r in results[:3])
        matched = sum(1 for kw in keywords if kw in top3_content)
        coverage = matched / len(keywords)
    else:
        coverage = 1.0  # 키워드 없으면 패스

    return QualityAssessment(
        top1_score=top1_score,
        score_gap=score_gap,
        keyword_coverage=coverage,
    )
```

---

## 전체 파이프라인

```
┌─────────────────────────────────────────────────────────────────┐
│                        Request 수신                              │
│  ChatRequest { session_id, user_id, messages[] }                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ [1] State 로드                                                   │
│     key = f"conversation_state:{user_id}:{session_id}"          │
│     state = state_store.get(key) or ConversationState()         │
│     state_store.touch(key)  # TTL 갱신 (sliding)                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ [2] 히스토리 Truncation                                          │
│     history = truncate_history_safe(                            │
│         messages=req.messages,                                  │
│         max_turns=4,                                            │
│         max_tokens=2000,                                        │
│     )                                                           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ [3] 라우팅 (Single Source of Truth)                              │
│     route_result = router.route(query)                          │
│     current_domain = route_result.domain                        │
│     current_intent = route_result.intent                        │
│                                                                 │
│     topic_switch = detect_topic_switch(state, current_domain)   │
│     if topic_switch.action == "reset_boost":                    │
│         boost_enabled = False                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ [4] 지시어 해소                                                  │
│     ┌──────────────────────────────────────────────────────────┐│
│     │ 4a. 규칙 기반 해소                                        ││
│     │     resolved = resolve_anaphora_rule(query, state)       ││
│     │     if resolved: goto [5]                                ││
│     └──────────────────────────────────────────────────────────┘│
│                              │                                  │
│                              ▼                                  │
│     ┌──────────────────────────────────────────────────────────┐│
│     │ 4b. 후보잠금 LLM (state.recent_docs 기반)                 ││
│     │     candidates = state.recent_docs[:3]                   ││
│     │     resolved = resolve_anaphora_llm(query, candidates)   ││
│     │     if resolved: goto [5]                                ││
│     └──────────────────────────────────────────────────────────┘│
│                              │                                  │
│                              ▼                                  │
│     ┌──────────────────────────────────────────────────────────┐│
│     │ 4c. 되묻기 (명시적 선택 요청)                              ││
│     │     return clarify_response(state.recent_docs)           ││
│     └──────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ [5] RAG 검색 (병합 전략)                                         │
│     results = search_with_merge(                                │
│         query=resolved_query,                                   │
│         state=state,                                            │
│         boost_enabled=boost_enabled,                            │
│     )                                                           │
│                                                                 │
│     quality = assess_quality(results, query)                    │
│     if quality.action == FALLBACK_BOOST:                        │
│         results = fallback_with_merge(query, state)             │
│     elif quality.action == CLARIFY:                             │
│         # 되묻기 고려 (낮은 신뢰도 경고)                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ [6] LLM 응답 생성                                                │
│     context = build_context(                                    │
│         state_context=state.to_prompt_context(),                │
│         rag_sources=results,                                    │
│         history=history,                                        │
│     )                                                           │
│     response = llm.generate(context, query)                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ [7] 상태 업데이트                                                │
│     # 도메인/인텐트 갱신                                         │
│     state.last_domain = current_domain                          │
│     state.last_intent = current_intent                          │
│                                                                 │
│     # recent_docs 갱신 (갱신 규칙 적용)                          │
│     if results and should_update_state(state.recent_docs[0],    │
│                                         new_doc_ref):           │
│         state.add_recent_doc(new_doc_ref)                       │
│                                                                 │
│     # 턴 카운터                                                  │
│     state.turn_count += 1                                       │
│                                                                 │
│     # 저장                                                       │
│     state_store.set(key, state, ttl=STATE_TTL_SECONDS)          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ [8] 응답 반환                                                    │
│     return ChatResponse(answer=response, sources=results, ...)  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 설정값 정리

```python
# config.py 추가 설정

# A. 저장소
STATE_STORE_BACKEND: Literal["redis", "memory"] = "memory"
STATE_STORE_REDIS_URL: Optional[str] = None
STATE_TTL_SECONDS: int = 3600  # 60분
STATE_TTL_SLIDING: bool = True
STATE_TTL_MAX_SECONDS: int = 7200  # 2시간

# B. 상태 갱신
STATE_UPDATE_HIGH_SCORE_THRESHOLD: float = 0.75
STATE_UPDATE_SCORE_GAP_THRESHOLD: float = 0.1

# C. 토큰 관리
CHAT_HISTORY_MAX_TURNS: int = 4
CHAT_HISTORY_MAX_TOKENS: int = 2000
CHAT_TOKEN_COUNTING_MODE: Literal["tiktoken", "char_conservative"] = "char_conservative"

# D. Recent docs
STATE_RECENT_DOCS_MAX_SIZE: int = 5

# E. 검색 병합
SEARCH_MERGE_ENABLED: bool = True
SEARCH_RANK_BUMP_MAX: int = 2

# F. 품질 게이트
QUALITY_TOP1_THRESHOLD: float = 0.55
QUALITY_GAP_THRESHOLD: float = 0.05
```

---

## 구현 파일 구조

```
app/
├── models/
│   └── conversation_state.py    # ConversationState, DocReference
├── services/
│   ├── state_store.py           # StateStore (Redis/Memory)
│   ├── history_manager.py       # truncate_history_safe
│   ├── anaphora_resolver.py     # 지시어 해소 (규칙 + LLM)
│   ├── search_merger.py         # 검색 병합 + rank bump
│   └── quality_gate.py          # 품질 평가
└── core/
    └── config.py                # 설정 추가
```

---

## 구현 우선순위

| 순서 | 작업 | 파일 | 의존성 |
|-----|------|------|--------|
| 1 | ConversationState 모델 | `models/conversation_state.py` | - |
| 2 | StateStore 구현 | `services/state_store.py` | 1 |
| 3 | 설정 추가 | `core/config.py` | - |
| 4 | truncate_history_safe | `services/history_manager.py` | - |
| 5 | 지시어 해소 (규칙) | `services/anaphora_resolver.py` | 1 |
| 6 | 검색 병합 + rank bump | `services/search_merger.py` | 1 |
| 7 | 품질 평가 | `services/quality_gate.py` | - |
| 8 | ChatService 통합 | `services/chat_service.py` | 1-7 |
| 9 | MessageBuilder 통합 | `services/chat/message_builder.py` | 1 |
