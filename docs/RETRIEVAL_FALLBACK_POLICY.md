# Retrieval Fallback Policy

> **Version**: 3.0.0
> **Last Updated**: 2025-12-31
> **Status**: Production
> **Phase 48 변경**: RAGFlow 클라이언트 제거, Milvus 전용

---

## 주요 변경 사항 (Phase 48)

> **중요**: RAGFlow 클라이언트가 모든 서비스에서 제거되었습니다.
> - FAQ 서비스: Milvus 전용
> - 채팅 서비스: Milvus 전용
> - 스크립트 서비스: Milvus 전용 (RAGFlow 재개발 예정)

---

## 1. Overview

이 문서는 ctrlf-ai 서비스의 검색(Retrieval) 백엔드 정책을 정의합니다.

### 1.1 검색 백엔드

| 백엔드 | 상태 | 설명 |
|--------|------|------|
| **Milvus** | **Active** | 벡터 DB 직접 검색 (모든 서비스) |
| **RAGFlow** | **Removed** | Phase 48에서 제거됨 (재개발 예정) |

### 1.2 관련 환경변수

```bash
# Milvus 활성화 (필수)
MILVUS_ENABLED=true

# Milvus 연결 설정
MILVUS_HOST=your-milvus-host
MILVUS_PORT=19530

# 임베딩 설정
EMBEDDING_CONTRACT_STRICT=true
EMBEDDING_MODEL=jhgan/ko-sroberta-multitask
EMBEDDING_OUTPUT_DIM=768

# 채팅 컨텍스트 제한
CHAT_CONTEXT_MAX_CHARS=8000
CHAT_CONTEXT_MAX_SOURCES=5
```

> **Note**: `RETRIEVAL_BACKEND`, `FAQ_RETRIEVER_BACKEND`, `CHAT_RETRIEVER_BACKEND`, `SCRIPT_RETRIEVER_BACKEND` 환경변수는 레거시 호환성을 위해 존재하지만, RAGFlow가 제거되어 실질적으로 Milvus만 사용됩니다.

---

## 2. 서비스별 검색 정책

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
|  2. MILVUS_ENABLED=true?                                     |
|     +-- YES -> Milvus 검색 시도                               |
|     |         +-- 성공 -> 결과 사용 (answer_source: MILVUS)   |
|     |         +-- 실패 -> FaqGenerationError("NO_DOCS_FOUND")|
|     +-- NO  -> FaqGenerationError("NO_DOCS_FOUND")           |
|                                                             |
+-------------------------------------------------------------+
```

**answer_source 값:**
| 값 | 설명 |
|----|------|
| `TOP_DOCS` | 클라이언트가 제공한 top_docs 사용 |
| `MILVUS` | Milvus 검색 결과 사용 |

**Fallback 없음:**
- RAGFlow가 제거되어 Milvus 실패 시 바로 에러 반환
- `MilvusSearchError` 발생 → `FaqGenerationError("NO_DOCS_FOUND")`

**코드 참조:** `app/services/faq_service.py:170-175`
```python
# RAGFlow 클라이언트는 제거되었습니다. Milvus만 사용합니다.
logger.info("FaqDraftService: Milvus search enabled (RAGFlow removed)")
```

---

### 2.2 채팅 서비스 (`rag_handler.py`)

```
+-------------------------------------------------------------+
|                   채팅 서비스 검색 흐름                        |
+-------------------------------------------------------------+
|                                                             |
|  MILVUS_ENABLED=true?                                        |
|     +-- YES -> Milvus 검색 시도                               |
|     |         +-- 성공 + 결과 있음                            |
|     |         |   -> 결과 사용 (retriever_used: MILVUS)       |
|     |         +-- 결과 0건                                    |
|     |         |   -> RagSearchUnavailableError (HTTP 503)    |
|     |         +-- 실패 (MilvusSearchError)                    |
|     |             -> RagSearchUnavailableError (HTTP 503)    |
|     +-- NO  -> RagSearchUnavailableError (HTTP 503)          |
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

> **Note**: `RAGFLOW`, `RAGFLOW_FALLBACK` 값은 레거시 호환성을 위해 코드에 정의되어 있지만, RAGFlow가 제거되어 실제로 사용되지 않습니다.

**Fallback 없음:**
- Phase 48에서 RAGFlow fallback 제거
- Milvus 실패 시 바로 503 에러 반환

**코드 참조:** `app/services/chat/rag_handler.py:595`
```python
# Phase 48 bugfix: RAGFlow fallback 제거 - Milvus만 사용합니다.
```

---

### 2.3 스크립트 생성 (`source_set_orchestrator.py`)

```
+-------------------------------------------------------------+
|              스크립트 생성 청크 로드 흐름                      |
+-------------------------------------------------------------+
|                                                             |
|  MILVUS_ENABLED=true?                                        |
|     +-- YES -> Milvus에서 청크 조회 시도                       |
|     |         +-- 성공 + 청크 있음                            |
|     |         |   -> Milvus 청크 사용                          |
|     |         +-- 청크 0건 또는 실패                          |
|     |             -> DOCUMENT_PROCESSING_FAILED              |
|     +-- NO  -> DOCUMENT_PROCESSING_FAILED                    |
|               (RAGFlow removed 에러)                         |
|                                                             |
+-------------------------------------------------------------+
```

**doc_id 매핑:**
- Milvus: `doc_id` = 파일명 (예: `장애인식관련법령.docx`)
- Spring: `documentId` = UUID
- 매핑 방법: source_url에서 파일명 추출 (`_extract_milvus_doc_id`)

**Fallback 없음:**
- RAGFlow 클라이언트가 제거됨 (재개발 예정)
- Milvus에서 청크를 찾지 못하면 실패 처리

**코드 참조:** `app/services/source_set_orchestrator.py:129`
```python
# RAGFlow 클라이언트는 제거되었습니다 (재개발 예정).
```

---

## 3. 설정 조합별 동작

| MILVUS_ENABLED | FAQ 서비스 | 채팅 서비스 | 스크립트 |
|----------------|------------|-------------|----------|
| `true` | Milvus 검색 | Milvus 검색 | Milvus 조회 |
| `false` | 에러 (NO_DOCS_FOUND) | 에러 (503) | 에러 (RAGFLOW_REMOVED) |

> **중요**: `MILVUS_ENABLED=true` 설정이 필수입니다. RAGFlow가 제거되어 Milvus 없이는 검색 기능을 사용할 수 없습니다.

---

## 4. 에러 코드 및 HTTP 응답

### 4.1 FAQ 서비스 에러

| 에러 코드 | 설명 | HTTP 상태 |
|-----------|------|-----------|
| `NO_DOCS_FOUND` | Milvus 검색 결과 없음 | 400 |
| `LOW_RELEVANCE_CONTEXT` | 컨텍스트 관련성 낮음 | 400 |
| `PII_DETECTED` | 입력에 PII 검출 | 400 |
| `PII_DETECTED_OUTPUT` | 출력에 PII 검출 | 400 |
| `PII_DETECTED_CONTEXT` | 컨텍스트에 PII 검출 | 400 |

### 4.2 채팅 서비스 에러

| 에러 코드 | 설명 | HTTP 상태 |
|-----------|------|-----------|
| `RagSearchUnavailableError` | Milvus 검색 불가 | 503 |

### 4.3 스크립트 생성 에러

| 에러 코드 | 설명 |
|-----------|------|
| `RAGFLOW_REMOVED` | RAGFlow 클라이언트가 제거됨 |
| `DOCUMENT_PROCESSING_FAILED` | 문서 처리 실패 |
| `NO_CHUNKS_GENERATED` | 청크 생성 실패 |

---

## 5. 런타임 로그

### 5.1 Milvus 연결 정보 (서버 시작 시)

```
[MILVUS_RUNTIME] host=localhost:19530 | collection=ragflow_chunks_sroberta | collection_dim=768 | embedding_model=jhgan/ko-sroberta-multitask | embedding_output_dim=768
```

### 5.2 서비스 초기화 로그

```
# FAQ 서비스
INFO - FaqDraftService: Milvus search enabled (RAGFlow removed)

# 채팅 서비스
INFO - RagHandler: MILVUS_ENABLED=True, Milvus search enabled
# or
WARNING - RagHandler: MILVUS_ENABLED=False, RAG search unavailable (RAGFlow removed)

# 스크립트 서비스
INFO - SourceSetOrchestrator: Milvus enabled for chunk retrieval
# or
WARNING - SourceSetOrchestrator: RAGFlow removed, document processing unavailable
```

### 5.3 에러 발생 시

```
# FAQ 서비스
ERROR - Milvus search failed: MilvusSearchError(...)
ERROR - FaqGenerationError: NO_DOCS_FOUND

# 채팅 서비스
ERROR - RagSearchUnavailableError: Milvus search failed and no fallback available

# 스크립트 서비스
ERROR - RAGFlow client removed - document processing unavailable
```

---

## 6. 마이그레이션 가이드 (RAGFlow → Milvus)

### 6.1 필수 설정 변경

```bash
# 이전 설정 (RAGFlow 사용)
RETRIEVAL_BACKEND=ragflow
MILVUS_ENABLED=false

# 현재 설정 (Milvus 전용)
MILVUS_ENABLED=true
MILVUS_HOST=your-milvus-host
MILVUS_PORT=19530
```

### 6.2 데이터 마이그레이션

1. RAGFlow의 기존 청크 데이터를 Milvus 컬렉션으로 마이그레이션
2. 임베딩 모델 일치 확인: `jhgan/ko-sroberta-multitask` (768차원)
3. 컬렉션명: `ragflow_chunks_sroberta`

### 6.3 클라이언트 코드 변경 불필요

- API 응답 형식은 동일하게 유지됨
- `answer_source`/`retriever_used` 값만 변경 (RAGFLOW → MILVUS)

---

## 7. 향후 계획

### RAGFlow 재개발 예정

- 스크립트 서비스의 문서 처리(upload → parse → chunks)를 위해 RAGFlow 재개발 계획
- 현재는 Milvus에 사전 적재된 청크만 사용 가능

### Fallback 전략 검토

- 현재: Fallback 없음 (Milvus 실패 시 에러)
- 검토 중: 캐시 기반 fallback, 다중 Milvus 인스턴스

---

## 8. 참고 문서

- [Milvus Client 구현](../app/clients/milvus_client.py)
- [FAQ Service 구현](../app/services/faq_service.py)
- [RAG Handler 구현](../app/services/chat/rag_handler.py)
- [SourceSet Orchestrator 구현](../app/services/source_set_orchestrator.py)
- [Config 설정](../app/core/config.py)

---

## 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2025-12-31 | 3.0.0 | Phase 48: RAGFlow 제거 반영, Milvus 전용으로 변경 |
| 2025-12-30 | 2.1.0 | RAGFlow fallback 정책 추가 |
| 2025-12-20 | 1.0.0 | 초기 작성 |
