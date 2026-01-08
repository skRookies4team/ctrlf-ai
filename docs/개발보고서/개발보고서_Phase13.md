# Phase 13: RAG 조항 정보 메타데이터 확장 개발 보고서

## 개요

Phase 13에서는 RAG 검색 결과에 **조항/섹션 정보**를 포함하여 사용자에게 "어떤 문서의 몇 장/몇 조/몇 항인지" 정보를 제공할 수 있도록 확장했습니다.

## 주요 목표

- RAG 소스에 조항 메타데이터 추가 (section_label, section_path, article_id, clause_id)
- ChatSource에 article_label, article_path 필드 추가
- LLM 프롬프트에 조항 위치 정보 포함
- 답변에 "[참고 근거]" 섹션 작성 지침 추가

## 구현 내용

### Step 1: RagDocument 모델 확장

**파일: `app/models/rag.py`**

```python
class RagDocument(BaseModel):
    # 기존 필드
    doc_id: str
    title: str
    page: Optional[int]
    score: float
    snippet: Optional[str]

    # Phase 13: 조항/섹션 메타데이터 필드 (새로 추가)
    section_label: Optional[str] = None  # 예: "제10조 (정보보호 의무)"
    section_path: Optional[str] = None   # 예: "제3장 > 제10조 > 제2항"
    article_id: Optional[str] = None     # 예: "제10조"
    clause_id: Optional[str] = None      # 예: "제2항"
```

### Step 2: ChatSource 모델 확장

**파일: `app/models/chat.py`**

```python
class ChatSource(BaseModel):
    # 기존 필드 (하위 호환 유지)
    doc_id: str
    title: str
    page: Optional[int]
    score: Optional[float]
    snippet: Optional[str]

    # Phase 13: 조항 메타데이터 (새로 추가)
    article_label: Optional[str] = None  # 사람이 읽을 수 있는 라벨
    article_path: Optional[str] = None   # 계층 경로
```

### Step 3: RAGFlow → RagDocument 매핑 강화

**파일: `app/clients/ragflow_client.py`**

RAGFlow 응답의 `metadata`, `fields`, `extra` 키에서 조항 정보를 추출:

```python
# RAGFlow 응답 파싱
metadata = chunk.get("metadata", {}) or {}
fields = chunk.get("fields", {}) or {}
extra = chunk.get("extra", {}) or {}

# 조항 정보 추출 (graceful degradation)
section_label = (
    metadata.get("section_title")
    or metadata.get("section_label")
    or fields.get("section_title")
    or extra.get("section_title")
)
section_path = metadata.get("section_path") or fields.get("section_path")
article_id = metadata.get("article_number") or metadata.get("article_id")
clause_id = metadata.get("clause_number") or metadata.get("clause_id")
```

### Step 4: RagDocument → ChatSource 변환

**파일: `app/clients/ragflow_client.py` - `_to_chat_source()` 메서드**

```python
# article_label 생성 로직
# 우선순위: section_label > article_id + clause_id 조합
article_label = doc.section_label
if not article_label and (doc.article_id or doc.clause_id):
    parts = []
    if doc.article_id:
        parts.append(doc.article_id)
    if doc.clause_id:
        parts.append(doc.clause_id)
    article_label = " ".join(parts) if parts else None

return ChatSource(
    doc_id=doc.doc_id,
    title=doc.title,
    page=doc.page,
    score=doc.score,
    snippet=doc.snippet,
    article_label=article_label,
    article_path=doc.section_path,
)
```

### Step 5: LLM 프롬프트 포맷팅 개선

**파일: `app/services/chat_service.py` - `_format_sources_for_prompt()` 메서드**

기존 포맷:
```
1) [doc-001] 연차휴가 관리 규정 (p.5) [관련도: 0.92]
   발췌: 연차는 다음 해 말일까지...
```

새로운 포맷:
```
[근거 1]
- 문서: 연차휴가 관리 규정 (p.5)
- 위치: 제2장 근로시간 및 휴가 > 제10조 연차 이월 > 제2항
- 관련도: 0.92
- 내용: 연차는 다음 해 말일까지...
```

### Step 6: 시스템 프롬프트 개선

**파일: `app/services/chat_service.py` - `SYSTEM_PROMPT_WITH_RAG`**

```python
SYSTEM_PROMPT_WITH_RAG = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.
...

답변 시 반드시 출처 문서와 해당 조항을 인용해 주세요.
가능하다면 답변 마지막에 "[참고 근거]" 섹션을 추가해서:
- 문서명
- 조문/항 번호 또는 위치 (예: 제10조 제2항, 제3장 > 제5조)
를 bullet으로 정리해 주세요.

예시:
[참고 근거]
- 연차휴가 관리 규정 제10조 (연차 이월) 제2항
- 인사관리 규정 제3장 근태관리 제5조 (지각/조퇴 처리 기준)
...
"""
```

## HTTP 응답 JSON 예시

```json
{
  "answer": "연차는 최대 10일까지 이월할 수 있습니다...\n\n[참고 근거]\n- 연차휴가 관리 규정 제10조 제2항",
  "sources": [
    {
      "doc_id": "policy-annual-leave-v3",
      "title": "연차휴가 관리 규정 (2025.01 개정)",
      "page": 4,
      "score": 0.92,
      "snippet": "연차는 다음 해 말일까지 최대 10일까지 이월할 수 있다...",
      "article_label": "제10조 (연차 이월) 제2항",
      "article_path": "제2장 근로시간 및 휴가 > 제10조 연차 이월 > 제2항"
    }
  ],
  "meta": {...}
}
```

## 테스트 결과

```
tests/test_phase13_article_metadata.py: 18 passed
전체 테스트: 242 passed, 12 deselected
```

### 테스트 항목

1. **RagDocument 조항 메타 필드 테스트** (3개)
2. **ChatSource 조항 메타 필드 테스트** (3개)
3. **RagflowClient → RagDocument 매핑 테스트** (3개)
4. **RagDocument → ChatSource 변환 테스트** (4개)
5. **ChatService 프롬프트 포맷팅 테스트** (2개)
6. **HTTP 응답 JSON 구조 테스트** (2개)
7. **시스템 프롬프트 테스트** (1개)

## 파일 변경 요약

### 수정된 파일
| 파일 | 변경 내용 |
|------|----------|
| `app/models/rag.py` | RagDocument에 section_label, section_path, article_id, clause_id 필드 추가 |
| `app/models/chat.py` | ChatSource에 article_label, article_path 필드 추가 |
| `app/clients/ragflow_client.py` | RAGFlow 응답 매핑에 조항 메타 추출 로직 추가, `_to_chat_source()` 개선 |
| `app/services/chat_service.py` | `_format_sources_for_prompt()` 개선, `SYSTEM_PROMPT_WITH_RAG` 근거 인용 지침 추가 |

### 새로 생성된 파일
| 파일 | 설명 |
|------|------|
| `tests/test_phase13_article_metadata.py` | Phase 13 테스트 (18개) |

## 하위 호환성

- **기존 스키마 유지**: doc_id, title, page, snippet 등 기존 필드는 변경 없음
- **Optional 필드**: 모든 조항 메타 필드는 Optional로 정의
- **Graceful Degradation**: RAGFlow에서 조항 메타를 보내지 않으면 None으로 처리

## 향후 작업 (RAGFlow 팀)

RAGFlow에서 조항 메타데이터를 제공하려면 응답의 `metadata` 키에 다음 필드를 추가:

```json
{
  "chunks": [
    {
      "chunk_id": "...",
      "content": "...",
      "metadata": {
        "section_title": "제10조 (정보보호 의무)",
        "section_path": "제3장 정보보호 > 제10조 > 제2항",
        "article_number": "제10조",
        "clause_number": "제2항"
      }
    }
  ]
}
```

이 필드가 추가되면 AI Gateway에서 자동으로 매핑됩니다.
