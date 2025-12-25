# Retrieval Fallback Policy

> **Version**: 1.0.0
> **Last Updated**: 2024-12-25
> **Status**: Production

---

## 1. Overview

이 문서는 ctrlf-ai 서비스의 검색(Retrieval) 백엔드 선택 및 장애 시 Fallback 정책을 정의합니다.

### 1.1 검색 백엔드

| 백엔드 | 설명 | 용도 |
|--------|------|------|
| **RAGFlow** | RAGFlow 서버의 `/v1/retrieval` API | 채팅, 스크립트 생성 |
| **Milvus** | Milvus 벡터 DB 직접 검색 | FAQ 생성 (Option 3) |

### 1.2 관련 환경변수

```bash
# 검색 백엔드 선택 (FAQ 서비스에만 적용)
RETRIEVAL_BACKEND=milvus  # ragflow | milvus

# Milvus 활성화
MILVUS_ENABLED=true

# 임베딩 계약 검증 (strict 모드)
EMBEDDING_CONTRACT_STRICT=true
```

---

## 2. 서비스별 Fallback 정책

### 2.1 FAQ 서비스 (`faq_service.py`)

```
┌─────────────────────────────────────────────────────────────┐
│                   FAQ 서비스 검색 흐름                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. top_docs 제공됨?                                         │
│     ├── YES → top_docs 사용 (answer_source: TOP_DOCS)       │
│     └── NO  → 다음 단계                                      │
│                                                             │
│  2. MILVUS_ENABLED=true?                                    │
│     ├── YES → Milvus 검색 시도                               │
│     │         ├── 성공 → 결과 사용 (answer_source: MILVUS)   │
│     │         └── 실패 → RAGFlow로 Fallback                  │
│     └── NO  → RAGFlow 검색                                   │
│                                                             │
│  3. RAGFlow 검색                                             │
│     ├── 성공 → 결과 사용 (answer_source: RAGFLOW)            │
│     └── 실패 → FaqGenerationError("NO_DOCS_FOUND")          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Fallback 조건:**
- `MilvusSearchError` 발생 시 → RAGFlow로 자동 Fallback
- 예기치 않은 Exception 발생 시 → RAGFlow로 자동 Fallback

**코드 참조:** `app/services/faq_service.py:307-337`

```python
async def _search_milvus(self, req: FaqDraftGenerateRequest):
    try:
        results = await self._milvus_client.search(...)
    except MilvusSearchError as e:
        logger.error(f"Milvus search error: {e}")
        logger.info("Falling back to RAGFlow search")
        return await self._search_ragflow(req)  # Fallback
    except Exception as e:
        logger.error(f"Milvus unexpected error: {e}")
        logger.info("Falling back to RAGFlow search")
        return await self._search_ragflow(req)  # Fallback
```

---

### 2.2 채팅 서비스 (`rag_handler.py`)

```
┌─────────────────────────────────────────────────────────────┐
│                   채팅 서비스 검색 흐름                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  RAGFlow 검색 (단일 경로, A안 확정)                           │
│     ├── 성공 → 결과 반환                                     │
│     │         └── 결과 0건도 정상 (is_failed=False)          │
│     └── 실패 → RagSearchUnavailableError 발생               │
│               └── HTTP 503 반환 (Fallback 없음)              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Fallback 정책: 없음 (A안 확정)**

Phase 42에서 A안(RAGFlow 단일 검색 엔진)으로 확정되었습니다:
- RAGFlow 장애 시 503 Service Unavailable 반환
- Milvus Fallback 미적용

**코드 참조:** `app/services/chat/rag_handler.py:109-190`

```python
async def perform_search_with_fallback(self, query, domain, req, request_id):
    try:
        sources = await self._ragflow.search_as_sources(...)
        return sources, False  # 0건도 정상
    except UpstreamServiceError as e:
        # A안: RAGFlow 장애 시 503 반환 (fallback 없음)
        raise RagSearchUnavailableError(f"RAG 검색 서비스 장애: {e.message}")
    except Exception as e:
        raise RagSearchUnavailableError(f"RAG 검색 서비스 장애: {type(e).__name__}")
```

---

### 2.3 스크립트 생성 (`source_set_orchestrator.py`)

```
┌─────────────────────────────────────────────────────────────┐
│              스크립트 생성 청크 로드 흐름                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  RAGFlow get_document_chunks() (단일 경로)                   │
│     ├── 성공 → 청크 목록 반환                                │
│     └── 실패 → Exception 전파                                │
│               └── Milvus Fallback 미적용                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Fallback 정책: 없음**

현재 스크립트 생성은 RAGFlow의 `get_document_chunks()` API만 사용합니다.

**코드 참조:** `app/services/source_set_orchestrator.py:560-616`

---

## 3. 설정 조합별 동작

| MILVUS_ENABLED | RETRIEVAL_BACKEND | FAQ 서비스 | 채팅 서비스 | 스크립트 |
|----------------|-------------------|------------|-------------|----------|
| `false` | `ragflow` | RAGFlow only | RAGFlow only | RAGFlow only |
| `true` | `ragflow` | RAGFlow only | RAGFlow only | RAGFlow only |
| `true` | `milvus` | Milvus → RAGFlow fallback | RAGFlow only | RAGFlow only |
| `false` | `milvus` | RAGFlow only (MILVUS_ENABLED=false 우선) | RAGFlow only | RAGFlow only |

---

## 4. 에러 코드 및 HTTP 응답

### 4.1 FAQ 서비스 에러

| 에러 코드 | 설명 | HTTP 상태 |
|-----------|------|-----------|
| `NO_DOCS_FOUND` | 검색 결과 없음 (Milvus + RAGFlow 모두) | 400 |
| `LOW_RELEVANCE_CONTEXT` | 컨텍스트 관련성 낮음 | 400 |
| `PII_DETECTED` | 입력에 PII 검출 | 400 |
| `PII_DETECTED_OUTPUT` | 출력에 PII 검출 | 400 |
| `PII_DETECTED_CONTEXT` | 컨텍스트에 PII 검출 | 400 |

### 4.2 채팅 서비스 에러

| 에러 코드 | 설명 | HTTP 상태 |
|-----------|------|-----------|
| `RagSearchUnavailableError` | RAGFlow 서비스 장애 | 503 |

---

## 5. 런타임 로그

### 5.1 Milvus 연결 정보 (서버 시작 시)

```
[MILVUS_RUNTIME] host=58.127.241.84:19540 | collection=ragflow_chunks_sroberta | collection_dim=768 | embedding_model=jhgan/ko-sroberta-multitask | embedding_output_dim=768
```

### 5.2 Fallback 발생 시

```
ERROR - Milvus search error: Connection timeout
INFO  - Falling back to RAGFlow search
INFO  - Found 5 documents from RAGFlow search
```

---

## 6. 향후 계획

### 6.1 채팅 서비스 Milvus 전환 (미정)

현재 채팅 서비스는 RAGFlow만 사용합니다 (A안 확정).
향후 Option 3 전환 시 FAQ 서비스와 동일한 Fallback 정책 적용 예정.

### 6.2 스크립트 생성 Milvus 전환 (미정)

현재 스크립트 생성은 RAGFlow의 `get_document_chunks()` 사용.
Milvus의 `get_full_document_text()` 전환은 별도 검토 필요.

---

## 7. 참고 문서

- [Phase 42 개발 보고서](./DEVELOPMENT_REPORT_PHASE42.md) - A안 확정
- [Milvus Client 구현](../app/clients/milvus_client.py)
- [FAQ Service 구현](../app/services/faq_service.py)
- [RAG Handler 구현](../app/services/chat/rag_handler.py)
