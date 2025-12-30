# Retrieval Fallback Policy

> **Version**: 2.1.0
> **Last Updated**: 2025-12-31
> **Status**: Production

---

## 1. Overview

이 문서는 ctrlf-ai 서비스의 검색(Retrieval) 백엔드 선택 및 장애 시 Fallback 정책을 정의합니다.

### 1.1 검색 백엔드

| 백엔드 | 설명 | 용도 |
|--------|------|------|
| **RAGFlow** | RAGFlow 서버의 `/v1/retrieval` API | 기본 검색 (Fallback용) |
| **Milvus** | Milvus 벡터 DB 직접 검색 | FAQ, 채팅, 스크립트 생성 (Option 3) |

### 1.2 관련 환경변수

```bash
# 기본 검색 백엔드 (모든 서비스 기본값)
RETRIEVAL_BACKEND=milvus  # ragflow | milvus

# 서비스별 검색 백엔드 (선택적 - 설정하지 않으면 RETRIEVAL_BACKEND 사용)
FAQ_RETRIEVER_BACKEND=milvus      # ragflow | milvus
CHAT_RETRIEVER_BACKEND=milvus     # ragflow | milvus
SCRIPT_RETRIEVER_BACKEND=milvus   # ragflow | milvus

# Milvus 활성화 (필수)
MILVUS_ENABLED=true

# 임베딩 계약 검증 (strict 모드)
EMBEDDING_CONTRACT_STRICT=true

# 채팅 컨텍스트 제한
CHAT_CONTEXT_MAX_CHARS=8000
CHAT_CONTEXT_MAX_SOURCES=5
```

---

## 2. 서비스별 Fallback 정책

### 2.1 FAQ 서비스 (`faq_service.py`)

```
+-------------------------------------------------------------+
|                   FAQ 서비스 검색 흐름                        |
+-------------------------------------------------------------+
|                                                             |
|  1. top_docs 제공됨?                                         |
|     +-- YES -> top_docs 사용 (answer_source: TOP_DOCS)       |
|     +-- NO  -> 다음 단계                                      |
|                                                             |
|  2. FAQ_RETRIEVER_BACKEND=milvus && MILVUS_ENABLED=true?    |
|     +-- YES -> Milvus 검색 시도                               |
|     |         +-- 성공 -> 결과 사용 (answer_source: MILVUS)   |
|     |         +-- 실패 -> RAGFlow로 Fallback                  |
|     +-- NO  -> RAGFlow 검색                                   |
|                                                             |
|  3. RAGFlow 검색                                             |
|     +-- 성공 -> 결과 사용 (answer_source: RAGFLOW)            |
|     +-- 실패 -> FaqGenerationError("NO_DOCS_FOUND")          |
|                                                             |
+-------------------------------------------------------------+
```

**Fallback 조건:**
- `MilvusSearchError` 발생 시 -> RAGFlow로 자동 Fallback
- 예기치 않은 Exception 발생 시 -> RAGFlow로 자동 Fallback

**코드 참조:** `app/services/faq_service.py`

---

### 2.2 채팅 서비스 (`rag_handler.py`) - Option 3 통합

```
+-------------------------------------------------------------+
|                   채팅 서비스 검색 흐름                        |
+-------------------------------------------------------------+
|                                                             |
|  CHAT_RETRIEVER_BACKEND=milvus && MILVUS_ENABLED=true?      |
|     +-- YES -> Milvus 검색 시도                               |
|     |         +-- 성공 + 결과 있음                            |
|     |         |   -> 결과 사용 (retriever_used: MILVUS)       |
|     |         +-- 결과 0건                                    |
|     |         |   -> RAGFlow Fallback                         |
|     |         +-- 실패 (MilvusSearchError)                    |
|     |             -> RAGFlow Fallback                         |
|     +-- NO  -> RAGFlow 검색                                   |
|               (retriever_used: RAGFLOW)                      |
|                                                             |
|  RAGFlow Fallback:                                           |
|     +-- 성공 -> 결과 사용 (retriever_used: RAGFLOW_FALLBACK)  |
|     +-- 실패 -> RagSearchUnavailableError (HTTP 503)         |
|                                                             |
|  컨텍스트 제한 적용:                                           |
|     CHAT_CONTEXT_MAX_CHARS (기본 8000)                       |
|     CHAT_CONTEXT_MAX_SOURCES (기본 5)                        |
|                                                             |
+-------------------------------------------------------------+
```

**retriever_used 필드:**
| 값 | 설명 |
|----|------|
| `MILVUS` | Milvus 검색 성공 |
| `RAGFLOW` | RAGFlow 검색 사용 (기본 또는 CHAT_RETRIEVER_BACKEND=ragflow) |
| `RAGFLOW_FALLBACK` | Milvus 실패/0건으로 RAGFlow fallback |

**코드 참조:** `app/services/chat/rag_handler.py:126-334`

```python
async def perform_search_with_fallback(
    self, query, domain, req, request_id=None
) -> Tuple[List[ChatSource], bool, RetrieverUsed]:
    if self._use_milvus and self._milvus:
        return await self._search_with_milvus_fallback(...)
    return await self._search_ragflow_only(...)
```

---

### 2.3 스크립트 생성 (`source_set_orchestrator.py`) - Option 3 통합

```
+-------------------------------------------------------------+
|              스크립트 생성 청크 로드 흐름                      |
+-------------------------------------------------------------+
|                                                             |
|  SCRIPT_RETRIEVER_BACKEND=milvus && MILVUS_ENABLED=true?    |
|     +-- YES -> Milvus에서 청크 조회 시도                       |
|     |         +-- 성공 + 청크 있음                            |
|     |         |   -> Milvus 청크 사용                          |
|     |         +-- 청크 0건                                    |
|     |         |   -> RAGFlow 처리 Fallback                    |
|     |         +-- 실패 (MilvusError)                          |
|     |             -> RAGFlow 처리 Fallback                    |
|     +-- NO  -> RAGFlow 처리                                   |
|               (upload -> parse -> get_chunks)                |
|                                                             |
+-------------------------------------------------------------+
```

**doc_id 매핑:**
- Milvus: `doc_id` = 파일명 (예: `장애인식관련법령.docx`)
- Spring: `documentId` = UUID (예: `550e8400-e29b-41d4-a716-446655440000`)
- 매핑 방법: source_url에서 파일명 추출 (`_extract_milvus_doc_id`)

**코드 참조:** `app/services/source_set_orchestrator.py:529-646`

```python
async def _process_document_with_routing(
    self, source_set_id, doc, job
) -> DocumentProcessingResult:
    if self._use_milvus and self._milvus_client:
        try:
            result = await self._process_document_milvus(...)
            if result.success and result.chunks:
                return result
        except MilvusError:
            pass  # fallback to RAGFlow
    return await self._process_document(...)  # RAGFlow
```

---

## 3. 설정 조합별 동작

| MILVUS_ENABLED | XXX_RETRIEVER_BACKEND | FAQ 서비스 | 채팅 서비스 | 스크립트 |
|----------------|----------------------|------------|-------------|----------|
| `false` | `ragflow` | RAGFlow only | RAGFlow only | RAGFlow only |
| `true` | `ragflow` | RAGFlow only | RAGFlow only | RAGFlow only |
| `true` | `milvus` | Milvus -> RAGFlow | Milvus -> RAGFlow | Milvus -> RAGFlow |
| `false` | `milvus` | RAGFlow only | RAGFlow only | RAGFlow only |

**Note:** `XXX_RETRIEVER_BACKEND`는 각 서비스별 설정 (FAQ/CHAT/SCRIPT)을 의미합니다.
미설정 시 `RETRIEVAL_BACKEND` 기본값을 사용합니다.

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
| `RagSearchUnavailableError` | Milvus + RAGFlow 모두 장애 | 503 |

### 4.3 스크립트 생성 에러

| 에러 코드 | 설명 | HTTP 상태 |
|-----------|------|-----------|
| `DOCUMENT_PROCESSING_FAILED` | Milvus + RAGFlow 처리 모두 실패 | - |
| `NO_CHUNKS_GENERATED` | 청크 생성 실패 | - |

---

## 5. 런타임 로그

### 5.1 Milvus 연결 정보 (서버 시작 시)

```
[MILVUS_RUNTIME] host=localhost:19540 | collection=ragflow_chunks_sroberta | collection_dim=768 | embedding_model=jhgan/ko-sroberta-multitask | embedding_output_dim=768
```

### 5.2 RagHandler 초기화

```
INFO - RagHandler initialized with Milvus (CHAT_RETRIEVER_BACKEND=milvus)
# or
INFO - RagHandler initialized with RAGFlow only (CHAT_RETRIEVER_BACKEND=ragflow)
```

### 5.3 SourceSetOrchestrator 초기화

```
INFO - SourceSetOrchestrator initialized with Milvus (SCRIPT_RETRIEVER_BACKEND=milvus)
# or
INFO - SourceSetOrchestrator initialized with RAGFlow only (SCRIPT_RETRIEVER_BACKEND=ragflow)
```

### 5.4 Fallback 발생 시

```
# Chat service
WARNING - Milvus search returned 0 results, falling back to RAGFlow
INFO - RAGFlow fallback returned 5 sources (retriever_used=RAGFLOW_FALLBACK)

# Script generation
WARNING - Milvus returned no chunks for doc_id=test-doc, falling back to RAGFlow
INFO - Document processed via RAGFlow: doc_id=test-doc, chunks=15
```

---

## 6. 테스트 스크립트

### 6.1 Option 3 통합 테스트

```bash
# 기본 Milvus 클라이언트 테스트
python scripts/test_option3_integration.py

# 채팅 서비스 Milvus 통합 테스트
python scripts/test_option3_chat_integration.py

# 스크립트 생성 Milvus 통합 테스트
python scripts/test_option3_script_integration.py
```

### 6.2 개별 서비스 테스트

```bash
# FAQ 서비스 테스트
python scripts/test_faq_milvus.py

# Milvus 연결 테스트
python scripts/test_milvus_connection.py
```

---

## 7. 참고 문서

- [Milvus Client 구현](../app/clients/milvus_client.py)
- [FAQ Service 구현](../app/services/faq_service.py)
- [RAG Handler 구현](../app/services/chat/rag_handler.py)
- [SourceSet Orchestrator 구현](../app/services/source_set_orchestrator.py)
- [Config 설정](../app/core/config.py)
