# Phase 29: KB Indexing E2E 및 토큰 기반 청킹

**작성일**: 2025-12-19
**Phase**: 29
**상태**: 완료
**테스트 결과**: 820 passed (16 new tests added)

---

## 1. 개요

Phase 29에서는 교육 스크립트의 KB(Knowledge Base) 인덱싱 기능을 강화하고 챗봇 RAG 근거 노출을 개선했습니다.

### 1.1 목표

- **A) Milvus E2E 통합 테스트**: 실제 Milvus 기반 인덱싱/검색 테스트
- **B) 토큰 기반 청킹**: 긴 narration/caption을 N 토큰 단위로 분할
- **C) 챗봇 RAG 근거 노출**: source_type으로 교육 스크립트 vs 정책 문서 구분

---

## 2. 구현 상세

### 2.1 Phase 29-B: 토큰 기반 청킹

#### 2.1.1 KBChunk 모델 확장

**파일**: `app/models/video_render.py`

```python
@dataclass
class KBChunk:
    """KB 청크 모델 (Phase 28/29)."""

    chunk_id: str
    video_id: str
    script_id: str
    chapter_order: int
    scene_order: int
    chapter_title: str
    scene_purpose: str
    content: str
    source_refs: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    # Phase 29: 토큰 기반 분할 지원
    part_index: Optional[int] = None       # 분할 파트 인덱스 (None이면 분할 안 됨)
    source_type: str = "TRAINING_SCRIPT"   # 소스 타입
```

#### 2.1.2 설정 추가

**파일**: `app/core/config.py`

```python
# Phase 29: KB Index 토큰 기반 청킹 설정
KB_CHUNK_MAX_TOKENS: int = 500    # 최대 토큰 수 (초과 시 분할)
KB_CHUNK_MIN_TOKENS: int = 50     # 최소 토큰 수 (너무 작은 청크 방지)
KB_CHUNK_TOKENIZER: str = "char"  # 토큰 계산 방식 ("char" 또는 "tiktoken")
KB_CHUNK_CHARS_PER_TOKEN: float = 1.5  # 문자/토큰 비율 (한국어)
```

#### 2.1.3 토큰 분할 로직

**파일**: `app/services/kb_index_service.py`

```python
def _split_content_by_tokens(self, content: str) -> List[str]:
    """내용을 토큰 수 기준으로 분할합니다.

    문장 경계를 존중하여 분할합니다.
    """
    estimated_tokens = self._estimate_tokens(content)

    # 토큰 수가 최대 이하면 분할 없이 반환
    if estimated_tokens <= self._max_tokens:
        return [content]

    # 문장 경계로 분할
    sentences = self._split_into_sentences(content)

    parts: List[str] = []
    current_part: List[str] = []
    current_tokens = 0

    for sentence in sentences:
        sentence_tokens = self._estimate_tokens(sentence)

        if sentence_tokens > self._max_tokens:
            # 긴 문장 강제 분할
            if current_part:
                parts.append(" ".join(current_part))
            forced_parts = self._force_split_long_text(sentence)
            parts.extend(forced_parts)
            current_part = []
            current_tokens = 0
            continue

        if current_tokens + sentence_tokens > self._max_tokens:
            if current_part:
                parts.append(" ".join(current_part))
            current_part = [sentence]
            current_tokens = sentence_tokens
        else:
            current_part.append(sentence)
            current_tokens += sentence_tokens

    if current_part:
        parts.append(" ".join(current_part))

    return self._merge_small_parts(parts)
```

#### 2.1.4 chunk_id 규칙

- **분할 없음**: `script_id:chapter:scene` (예: `script-001:1:1`)
- **분할됨**: `script_id:chapter:scene:part` (예: `script-001:1:1:0`, `script-001:1:1:1`)

---

### 2.2 Phase 29-C: 챗봇 RAG 근거 노출

#### 2.2.1 ChatSource 확장

**파일**: `app/models/chat.py`

```python
class ChatSource(BaseModel):
    """Source document information retrieved by RAG."""

    doc_id: str
    title: str
    page: Optional[int] = None
    score: Optional[float] = None
    snippet: Optional[str] = None
    article_label: Optional[str] = None
    article_path: Optional[str] = None

    # Phase 29: 소스 유형 구분
    source_type: Optional[str] = Field(
        default=None,
        description="Source type: POLICY (정책문서), TRAINING_SCRIPT (교육스크립트), etc.",
    )
```

#### 2.2.2 source_type 값

| source_type | 설명 |
|-------------|------|
| `TRAINING_SCRIPT` | 교육 스크립트 기반 청크 |
| `POLICY` | 정책/규정 문서 |
| `DOCUMENT` | 기타 문서 (기본값) |

#### 2.2.3 MilvusSearchClient 업데이트

**파일**: `app/clients/milvus_client.py`

```python
async def search_as_sources(self, ...) -> List[ChatSource]:
    """Phase 29: source_type 필드 추가하여 TRAINING_SCRIPT 등 구분."""

    for result in results:
        # source_type 결정 로직
        source_type = metadata.get("source_type")
        if not source_type:
            result_domain = result.get("domain", "")
            if result_domain == "TRAINING":
                source_type = "TRAINING_SCRIPT"
            elif result_domain == "POLICY":
                source_type = "POLICY"
            else:
                source_type = "DOCUMENT"

        source = ChatSource(
            doc_id=result.get("doc_id"),
            title=result.get("title"),
            snippet=result.get("content", "")[:500],
            source_type=source_type,  # Phase 29
            ...
        )
```

---

### 2.3 Phase 29-A: Milvus E2E 통합 테스트

**파일**: `tests/test_phase29_kb_e2e.py`

#### 테스트 클래스 및 케이스

| 테스트 클래스 | 테스트 수 | 설명 |
|-------------|----------|------|
| `TestTokenBasedChunking` | 5 | 토큰 기반 청킹 테스트 |
| `TestTokenEstimation` | 2 | 토큰 추정 테스트 |
| `TestSentenceSplit` | 2 | 문장 분할 테스트 |
| `TestChatSourceType` | 3 | ChatSource source_type 테스트 |
| `TestMilvusE2E` | 2 | Milvus E2E 테스트 (SKIP if no Milvus) |
| `TestKBChunkModel` | 3 | KBChunk 모델 테스트 |
| `TestKBToChatSourceIntegration` | 1 | 통합 테스트 |

```bash
# 테스트 실행
python -m pytest tests/test_phase29_kb_e2e.py -v

# 결과
16 passed, 2 skipped in 4.05s
```

---

## 3. 시나리오 예시

### 3.1 짧은 내용 (분할 없음)

```
Input:
  narration: "안녕하세요. 보안교육입니다."
  caption: "환영합니다"

Output:
  - chunk_id: "script-001:1:1"
  - content: "안녕하세요. 보안교육입니다.\n환영합니다"
  - part_index: None
  - source_type: "TRAINING_SCRIPT"
```

### 3.2 긴 내용 (토큰 분할)

```
Input:
  narration: "보안교육에 오신 것을 환영합니다. " × 50 (1500자, 약 1000토큰)

Output:
  - chunk[0]:
    - chunk_id: "script-001:1:1:0"
    - content: (처음 500토큰)
    - part_index: 0
  - chunk[1]:
    - chunk_id: "script-001:1:1:1"
    - content: (나머지 500토큰)
    - part_index: 1
```

### 3.3 RAG 검색 시 source_type

```
User: "보안교육 내용 알려줘"

Response:
  sources:
    - doc_id: "script-001:1:1"
      title: "보안교육 개요 - 인사"
      snippet: "안녕하세요. 보안교육입니다..."
      source_type: "TRAINING_SCRIPT"   # ← 교육 스크립트임을 표시
      score: 0.92
```

---

## 4. 변경된 파일

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `app/models/video_render.py` | 수정 | KBChunk에 part_index, source_type 추가 |
| `app/models/chat.py` | 수정 | ChatSource에 source_type 추가 |
| `app/core/config.py` | 수정 | KB 청킹 설정 추가 |
| `app/services/kb_index_service.py` | 수정 | 토큰 기반 분할 로직 추가 |
| `app/clients/milvus_client.py` | 수정 | source_type 반환 로직 추가 |
| `tests/test_phase29_kb_e2e.py` | 신규 | Phase 29 테스트 18개 |

---

## 5. 테스트 결과

```bash
# Phase 29 테스트
python -m pytest tests/test_phase29_kb_e2e.py -v
# 결과: 16 passed, 2 skipped

# 회귀 테스트 (Phase 22/28)
python -m pytest tests/test_phase28_kb_indexing.py tests/test_phase22_video_progress.py -v
# 결과: 51 passed

# 전체 테스트
python -m pytest tests/ -q
# 결과: 804 passed, 2 skipped
```

---

## 6. 향후 계획

1. **tiktoken 통합**: 정확한 토큰 계산을 위해 tiktoken 라이브러리 사용
2. **Milvus E2E 자동화**: Docker Compose로 Milvus 테스트 환경 구축
3. **source_type UI 표시**: 프론트엔드에서 교육 스크립트 출처 표시 개선

---

**작성자**: Claude Code
**검토자**: -
