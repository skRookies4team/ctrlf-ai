# Phase 6: RAG E2E 플로우 완성 보고서

## 개요

Phase 6에서는 POLICY 도메인 질문에 대해 실제로 동작하는 RAG E2E 플로우를 완성했습니다.

**목표**: Intent/Route → RAGFlow 검색 → LLM 응답 → PII 마스킹 → ChatResponse(answer + sources + meta)

## 변경 사항 요약

### 1. RagDocument 모델 추가 (`app/models/rag.py`)

RAGFlow 검색 결과를 정규화하는 Pydantic 모델을 추가했습니다.

```python
class RagDocument(BaseModel):
    doc_id: str       # 문서 ID
    title: str        # 문서 제목
    page: Optional[int]     # 페이지 번호
    score: float      # 관련도 점수 (0.0 ~ 1.0)
    snippet: Optional[str]  # 텍스트 발췌문
```

### 2. RagflowClient 개선 (`app/clients/ragflow_client.py`)

#### 새로운 기능
- **`search()` 메서드**: `List[RagDocument]` 반환
- **`search_as_sources()` 메서드**: `List[ChatSource]` 반환 (ChatService용)
- **`health()` 메서드**: 헬스체크 (기존 `health_check()` 별칭 유지)
- **`dataset` 파라미터**: 도메인별 검색 지원
- **`timeout` 파라미터**: HTTP 타임아웃 설정 (기본 10초)

#### 예외 클래스
```python
class RagflowError(Exception): ...
class RagflowConnectionError(RagflowError): ...
class RagflowSearchError(RagflowError): ...
```

#### API 스펙
```
POST ${RAGFLOW_BASE_URL}/search
Body: {
  "query": "검색어",
  "top_k": 5,
  "dataset": "POLICY",
  "user_role": "EMPLOYEE",
  "department": "개발팀"
}

Response: {
  "results": [
    {
      "doc_id": "HR-001",
      "title": "연차휴가 관리 규정",
      "page": 12,
      "score": 0.92,
      "snippet": "연차휴가의 이월은 최대 10일을..."
    }
  ]
}
```

### 3. ChatService RAG 통합 (`app/services/chat_service.py`)

#### ROUTE_RAG_INTERNAL 플로우
1. PII masking (INPUT) - 사용자 질문에서 PII 마스킹
2. Intent classification - 의도 분류 및 라우팅 결정
3. **RAG search** - RAGFlow에서 관련 문서 검색 (`dataset=domain`)
4. **Build LLM prompt** - RAG context를 포함한 프롬프트 구성
5. Generate response - LLM으로 답변 생성
6. PII masking (OUTPUT) - LLM 응답에서 PII 마스킹
7. Generate AI log - 백엔드로 로그 전송
8. Return ChatResponse - `answer` + `sources` + `meta` 반환

#### RAG Fallback 정책

| 상황 | 처리 방식 |
|------|----------|
| RAG 호출 실패 | 경고 로그 남기고 RAG 없이 LLM-only 진행 |
| RAG 결과 0건 | "관련 문서를 찾지 못했습니다" 안내와 함께 일반 QA 처리 |
| `meta.rag_used` | `len(sources) > 0` |
| `meta.rag_source_count` | `len(sources)` |

#### System Prompt 분기
- **RAG 결과 있음**: 문서 기반 답변 유도
- **RAG 결과 없음**: 일반 지식 기반 + 담당 부서 안내
- **RAG 시도 안 함**: ROUTE_LLM_ONLY 등

### 4. ChatResponse.sources 매핑

```
RagDocument          →  ChatSource
─────────────────────────────────────
doc_id               →  doc_id
title                →  title
page                 →  page
score                →  score
snippet              →  snippet
```

`sources`는 최대 5개까지 반환 (`top_k=5`).

### 5. POLICY 도메인 E2E 테스트 (`tests/test_chat_rag_integration.py`)

#### 테스트 시나리오

| # | 시나리오 | 검증 항목 |
|---|----------|----------|
| 1 | 정상 RAG + POLICY | `rag_used=True`, `sources` 개수 일치 |
| 2 | RAG 결과 없음 | `rag_used=False`, fallback 안내 문구 |
| 3 | RAG 호출 실패 | LLM-only fallback, 에러 로깅 |
| 4 | LLM 호출 실패 | `route=ROUTE_ERROR`, 에러 메시지 |
| 5 | ROUTE_LLM_ONLY | RAG 검색 스킵, `rag_used=False` |
| 6 | meta 필드 완전성 | 모든 필드 null 아님 |
| 7 | 빈 메시지 처리 | `route=ROUTE_FALLBACK` |
| 8 | Source 매핑 | RagDocument → ChatSource 정확성 |

#### Fake 클래스
- `FakeRagflowClient`: 미리 설정된 문서 반환
- `FakeLLMClient`: 미리 설정된 응답 반환
- `FakeIntentService`: 미리 설정된 IntentResult 반환

## 테스트 결과

```
$ pytest --tb=short -q
........................................................................ [ 87%]
..........                                                               [100%]
82 passed in 2.56s
```

**기존 73개 + 새로 추가 9개 = 총 82개 테스트 통과**

## 파일 변경 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `app/models/rag.py` | 수정 | `RagDocument` 모델 추가 |
| `app/clients/ragflow_client.py` | 전면 수정 | Phase 6 스펙 구현 |
| `app/services/chat_service.py` | 수정 | RAG 통합 강화, fallback 정책 |
| `tests/test_chat_rag_integration.py` | 신규 | E2E 통합 테스트 8개 시나리오 |

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                          ChatService                                  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐   │
│  │ PiiService  │ │IntentService│ │RagflowClient│ │  LLMClient  │   │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘   │
│         │               │               │               │           │
│         ▼               ▼               ▼               ▼           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    handle_chat() Pipeline                    │   │
│  │                                                             │   │
│  │  1. Extract user query                                      │   │
│  │  2. PII mask (INPUT)                                        │   │
│  │  3. Intent classify → route                                 │   │
│  │  4. RAG search (if ROUTE_RAG_INTERNAL)  ◄── Phase 6        │   │
│  │  5. Build LLM messages with RAG context ◄── Phase 6        │   │
│  │  6. LLM generate                                            │   │
│  │  7. PII mask (OUTPUT)                                       │   │
│  │  8. Return ChatResponse                                     │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                         ┌───────────────┐
                         │ ChatResponse  │
                         ├───────────────┤
                         │ answer        │
                         │ sources[]     │ ◄── RAG 결과
                         │ meta          │ ◄── rag_used, rag_source_count
                         └───────────────┘
```

## 다음 단계 (Phase 7 후보)

1. **다중 도메인 RAG 지원**: INCIDENT, EDUCATION 도메인 검색 테스트
2. **RAG 성능 최적화**: 캐싱, 병렬 검색
3. **대화 히스토리 활용**: 멀티턴 컨텍스트 지원
4. **실제 RAGFlow 연동 테스트**: Mock이 아닌 실제 서버 통합 테스트
5. **Streaming 응답 지원**: SSE 기반 스트리밍

---

**작성일**: 2025-12-09
**작성자**: Claude Opus 4.5 (AI Assistant)
