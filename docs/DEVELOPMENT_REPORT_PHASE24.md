# Phase 24: Milvus 벡터 데이터베이스 직접 연결

**작성일**: 2025-12-18
**작성자**: AI Assistant (Claude)
**버전**: Phase 24

---

## 1. 개요

### 1.1 목표
RAGFlow 대신 Milvus 벡터 데이터베이스에 직접 연결하여 벡터 검색을 수행합니다.

### 1.2 배경
- 기존 시스템은 RAGFlow를 통해 벡터 검색을 수행
- Milvus에 직접 연결하면 중간 계층 제거로 레이턴시 감소 및 유연성 향상
- vLLM 서버의 /v1/embeddings API를 활용하여 임베딩 생성

### 1.3 서버 정보
| 서비스 | 환경변수 | 기본 포트 |
|--------|----------|-----------|
| vLLM (Embedding) | `LLM_BASE_URL` | 1234 |
| Milvus | `MILVUS_HOST`, `MILVUS_PORT` | 19530 |

---

## 2. 구현 내용

### 2.1 파일 변경 요약

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `requirements.txt` | 수정 | pymilvus>=2.4.0 추가 |
| `app/core/config.py` | 수정 | Milvus 관련 설정 변수 추가 |
| `app/clients/milvus_client.py` | **신규** | MilvusSearchClient 구현 |
| `app/clients/__init__.py` | 수정 | Milvus 클라이언트 export 추가 |
| `app/services/chat_service.py` | 수정 | Milvus/RAGFlow 선택 로직 추가 |
| `tests/test_milvus_client.py` | **신규** | 29개 단위 테스트 |

### 2.2 환경변수 설정

```env
# Milvus 연결 설정 (실제 서버 주소로 변경 필요)
MILVUS_HOST=your-milvus-server
MILVUS_PORT=19530
MILVUS_COLLECTION_NAME=ragflow_chunks

# 벡터 검색 설정
MILVUS_TOP_K=5
MILVUS_SEARCH_PARAMS={"metric_type": "COSINE", "params": {"nprobe": 10}}

# Milvus 활성화 (기본값: False)
MILVUS_ENABLED=true

# 임베딩 서버 설정 (실제 서버 주소로 변경 필요)
LLM_BASE_URL=http://your-embedding-server:1234

# 임베딩 모델 설정
EMBEDDING_MODEL_NAME=BAAI/bge-m3
EMBEDDING_DIMENSION=1024
```

### 2.3 MilvusSearchClient 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                      ChatService                             │
├─────────────────────────────────────────────────────────────┤
│  MILVUS_ENABLED?                                            │
│     ├─ True  → MilvusSearchClient.search_as_sources()       │
│     └─ False → RagflowClient.search_as_sources()            │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┴───────────────────┐
        ▼                                       ▼
┌─────────────────────┐             ┌─────────────────────┐
│ MilvusSearchClient  │             │   RagflowClient     │
├─────────────────────┤             ├─────────────────────┤
│ - generate_embedding│             │ - search_as_sources │
│ - search()          │             │                     │
│ - search_as_sources │             │                     │
│ - health_check()    │             │                     │
└─────────────────────┘             └─────────────────────┘
        │                                       │
        ▼                                       ▼
┌─────────────────────┐             ┌─────────────────────┐
│ vLLM /v1/embeddings │             │   RAGFlow API       │
│   (LLM_BASE_URL)    │             │                     │
└─────────────────────┘             └─────────────────────┘
        │
        ▼
┌─────────────────────┐
│   Milvus Server     │
│ (MILVUS_HOST:PORT)  │
└─────────────────────┘
```

### 2.4 주요 클래스 및 메서드

#### MilvusSearchClient
```python
class MilvusSearchClient:
    """Milvus 벡터 검색 클라이언트."""

    async def generate_embedding(self, text: str) -> List[float]:
        """vLLM 서버로 임베딩 벡터 생성."""

    async def search(
        self,
        query: str,
        domain: Optional[str] = None,
        top_k: Optional[int] = None,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """벡터 유사도 검색 수행."""

    async def search_as_sources(
        self,
        query: str,
        domain: Optional[str] = None,
        user_role: Optional[str] = None,
        department: Optional[str] = None,
        top_k: int = 5,
    ) -> List[ChatSource]:
        """검색 결과를 ChatSource 형식으로 반환."""

    async def health_check(self) -> bool:
        """Milvus 서비스 상태 확인."""
```

#### 예외 클래스
```python
class MilvusError(Exception):           # 기본 Milvus 예외
class MilvusConnectionError(MilvusError): # 연결 실패
class MilvusSearchError(MilvusError):     # 검색 실패
class EmbeddingError(MilvusError):        # 임베딩 생성 실패
```

---

## 3. 사용 방법

### 3.1 Milvus 활성화

환경변수 설정:
```bash
export MILVUS_ENABLED=true
```

또는 `.env` 파일:
```env
MILVUS_ENABLED=true
```

### 3.2 코드 사용 예시

```python
from app.clients.milvus_client import get_milvus_client

# 싱글턴 클라이언트 가져오기
client = get_milvus_client()

# 벡터 검색
results = await client.search(
    query="연차휴가 이월 규정",
    domain="POLICY",
    top_k=5,
)

# ChatSource 형식으로 검색
sources = await client.search_as_sources(
    query="4대교육 이수 여부",
    domain="EDU",
    top_k=5,
)

# 헬스체크
is_healthy = await client.health_check()
```

### 3.3 ChatService 자동 통합

`MILVUS_ENABLED=true`로 설정하면 ChatService가 자동으로 Milvus를 사용합니다:

```python
# ChatService 초기화 (자동으로 Milvus 사용)
service = ChatService()

# 채팅 처리 (내부적으로 Milvus 검색)
response = await service.handle_chat(request)
```

---

## 4. 테스트 결과

### 4.1 단위 테스트

| 테스트 카테고리 | 테스트 수 | 상태 |
|---------------|----------|------|
| 초기화 | 2 | ✅ PASS |
| 연결 관리 | 4 | ✅ PASS |
| 컬렉션 관리 | 2 | ✅ PASS |
| 임베딩 생성 | 5 | ✅ PASS |
| 벡터 검색 | 3 | ✅ PASS |
| search_as_sources | 3 | ✅ PASS |
| 헬스체크 | 3 | ✅ PASS |
| 싱글턴 패턴 | 2 | ✅ PASS |
| 예외 클래스 | 5 | ✅ PASS |
| **합계** | **29** | ✅ **ALL PASS** |

### 4.2 전체 테스트

```
659 passed, 12 skipped, 12 deselected, 12 warnings
```

기존 630개 테스트 + 새로운 29개 테스트 = 659개 테스트 모두 통과

---

## 5. 설계 결정 사항

### 5.1 기본값 MILVUS_ENABLED=False

**결정**: Milvus는 기본 비활성화, 명시적으로 환경변수 설정 시 활성화

**이유**:
- 기존 RAGFlow 기반 시스템과의 하위 호환성 유지
- 점진적 마이그레이션 지원
- 테스트 환경에서 불필요한 Milvus 연결 시도 방지

### 5.2 싱글턴 패턴

**결정**: `get_milvus_client()` 함수로 싱글턴 인스턴스 관리

**이유**:
- Milvus 연결 리소스 효율적 관리
- 애플리케이션 전체에서 일관된 클라이언트 사용
- 테스트 시 `clear_milvus_client()`로 상태 초기화 가능

### 5.3 ChatSource 호환성

**결정**: `search_as_sources()` 메서드로 기존 RagflowClient와 동일한 인터페이스 제공

**이유**:
- ChatService 코드 변경 최소화
- 기존 LLM 프롬프트 빌더와 호환
- 점진적 전환 용이

---

## 6. 컬렉션 스키마 요구사항

Milvus 컬렉션 (`ctrlf_documents`)에 필요한 필드:

| 필드명 | 타입 | 설명 |
|--------|------|------|
| `id` | INT64 (Primary) | 문서 ID |
| `embedding` | FLOAT_VECTOR(1024) | BGE-M3 임베딩 벡터 |
| `content` | VARCHAR | 문서 내용 |
| `title` | VARCHAR | 문서 제목 |
| `domain` | VARCHAR | 도메인 (POLICY, EDU 등) |
| `doc_id` | VARCHAR | 외부 문서 ID |
| `metadata` | JSON | 추가 메타데이터 |

### 인덱스 설정
- 벡터 필드: IVF_FLAT 또는 HNSW
- 메트릭: COSINE
- nprobe: 10 (검색 시)

---

## 7. 모니터링 및 로그

### 7.1 로그 레벨

| 이벤트 | 로그 레벨 | 예시 |
|--------|----------|------|
| 클라이언트 초기화 | INFO | "MilvusSearchClient initialized: host=..." |
| 연결 성공 | INFO | "Connected to Milvus at ..." |
| 검색 완료 | INFO | "Milvus search returned N results" |
| 임베딩 생성 | DEBUG | "Generated embedding with dimension 1024" |
| 연결 실패 | ERROR | "Failed to connect to Milvus: ..." |
| 검색 실패 | ERROR | "Milvus search failed: ..." |

### 7.2 성능 메트릭

ChatService에서 자동으로 측정되는 메트릭:
- `rag_latency_ms`: 검색 소요 시간
- 검색 소스 수
- fallback 여부

---

## 8. 향후 개선 사항

### 8.1 단기
- [ ] Milvus 컬렉션 자동 생성 스크립트
- [ ] 배치 임베딩 생성 최적화
- [ ] 연결 풀링 구현

### 8.2 중기
- [ ] ACL(접근 제어) 기반 필터링
- [ ] 멀티 컬렉션 지원
- [ ] 하이브리드 검색 (키워드 + 벡터)

### 8.3 장기
- [ ] RAGFlow 완전 대체 및 제거
- [ ] 실시간 임베딩 업데이트
- [ ] A/B 테스트 인프라

---

## 9. 체크리스트

- [x] pymilvus 의존성 추가
- [x] 환경변수 설정 추가
- [x] MilvusSearchClient 구현
- [x] ChatService 통합
- [x] 단위 테스트 29개 작성
- [x] 전체 테스트 통과 확인 (659개)
- [x] 개발 문서 작성
