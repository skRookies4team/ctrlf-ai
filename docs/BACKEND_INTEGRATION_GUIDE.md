# 백엔드팀 연동 가이드

> **최종 수정일**: 2025-12-26
> **대상**: ctrlf-back (Spring Backend) 개발팀

---

## 핵심 요약 (Quick Start)

### 서비스 연결 정보

| 서비스 | URL | 용도 |
|--------|-----|------|
| **Milvus** | `your-milvus-host:19540` | 벡터 DB (RAG 검색) |
| **Embedding Server** | `http://your-embedding-server:port/v1/embeddings` | 임베딩 생성 |
| **LLM Server** | `http://your-llm-server:port/v1` | LLM 응답 생성 |
| **RAGFlow** | `http://your-ragflow-host:9380` | RAG 검색 (대안) |
| **AI Gateway** | `http://localhost:8000` | 통합 API |

### Milvus 핵심 정보

```
Collection: ragflow_chunks_sroberta
Embedding Model: jhgan/ko-sroberta-multitask
Dimension: 768
Metric: COSINE
```

### Dataset ID (한글 카테고리명)

| dataset_id | 도메인 | 청크 수 |
|------------|--------|--------|
| `사내규정` | POLICY | 390 |
| `정보보안교육` | EDUCATION | 1,442 |
| `직장내성희롱교육` | EDUCATION | 486 |
| `직무교육` | EDUCATION | 330 |
| `직장내괴롭힘교육` | EDUCATION | 152 |
| `장애인인식개선교육` | EDUCATION | 125 |

> **주의**: `kb_policy_001` 같은 ID는 존재하지 않습니다. 실제 dataset_id는 위의 한글 값입니다.

---

## 목차

1. [Milvus 직접 연동](#1-milvus-직접-연동)
2. [AI Gateway API 연동](#2-ai-gateway-api-연동)
3. [채팅 API 상세](#3-채팅-api-상세)
4. [에러 처리](#4-에러-처리)
5. [체크리스트](#5-체크리스트)

---

## 1. Milvus 직접 연동

### 1.1 컬렉션 스키마

| 필드 | 타입 | 설명 |
|------|------|------|
| `pk` | INT64 | Primary Key (자동 생성) |
| `dataset_id` | VARCHAR | 데이터셋 ID (한글 카테고리명) |
| `doc_id` | VARCHAR | 문서 ID (파일명) |
| `chunk_id` | INT64 | 청크 번호 (0부터 시작) |
| `chunk_hash` | VARCHAR | 청크 해시값 |
| `text` | VARCHAR | 청크 텍스트 내용 |
| `embedding` | FLOAT_VECTOR(768) | 임베딩 벡터 |

### 1.2 임베딩 생성 API

```bash
curl -X POST http://your-embedding-server:port/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "input": "연차휴가 규정이 어떻게 되나요?",
    "model": "jhgan/ko-sroberta-multitask"
  }'
```

**응답:**
```json
{
  "data": [
    {
      "embedding": [0.123, -0.456, ...],
      "index": 0
    }
  ],
  "model": "jhgan/ko-sroberta-multitask"
}
```

### 1.3 Python 검색 예시

```python
from pymilvus import connections, Collection
import httpx

# 1. 임베딩 생성
def get_embedding(text: str) -> list[float]:
    response = httpx.post(
        "http://your-embedding-server:port/v1/embeddings",
        json={"input": text, "model": "jhgan/ko-sroberta-multitask"},
        timeout=10.0
    )
    return response.json()["data"][0]["embedding"]

# 2. Milvus 연결
connections.connect(host="your-milvus-host", port=19540)
collection = Collection("ragflow_chunks_sroberta")
collection.load()

# 3. 검색
query_embedding = get_embedding("연차휴가 규정이 어떻게 되나요?")

results = collection.search(
    data=[query_embedding],
    anns_field="embedding",
    param={"metric_type": "COSINE", "params": {"nprobe": 10}},
    limit=5,
    output_fields=["text", "doc_id", "dataset_id", "chunk_id"]
)

# 4. 결과 출력
for hits in results:
    for hit in hits:
        print(f"Score: {hit.score:.4f}, Doc: {hit.entity.get('doc_id')}")
        print(f"Text: {hit.entity.get('text')[:200]}...")
```

### 1.4 Java 검색 예시

```java
import io.milvus.client.*;
import io.milvus.param.*;
import io.milvus.param.dml.*;

// 1. 연결
MilvusServiceClient client = new MilvusServiceClient(
    ConnectParam.newBuilder()
        .withHost("your-milvus-host")
        .withPort(19540)
        .build()
);

// 2. 컬렉션 로드
client.loadCollection(
    LoadCollectionParam.newBuilder()
        .withCollectionName("ragflow_chunks_sroberta")
        .build()
);

// 3. 임베딩 생성 (별도 HTTP 호출)
List<Float> queryEmbedding = getEmbeddingFromServer("연차휴가 규정이 어떻게 되나요?");

// 4. 검색
SearchParam searchParam = SearchParam.newBuilder()
    .withCollectionName("ragflow_chunks_sroberta")
    .withVectorFieldName("embedding")
    .withVectors(List.of(queryEmbedding))
    .withTopK(5)
    .withMetricType(MetricType.COSINE)
    .withParams("{\"nprobe\": 10}")
    .withOutFields(List.of("text", "doc_id", "dataset_id", "chunk_id"))
    .build();

R<SearchResults> response = client.search(searchParam);
```

### 1.5 특정 Dataset 필터링

```python
# 사내규정만 검색
results = collection.search(
    data=[query_embedding],
    anns_field="embedding",
    param={"metric_type": "COSINE", "params": {"nprobe": 10}},
    limit=5,
    expr='dataset_id == "사내규정"',  # 필터
    output_fields=["text", "doc_id", "dataset_id"]
)

# 교육 관련 검색 (여러 dataset_id)
results = collection.search(
    data=[query_embedding],
    anns_field="embedding",
    param={"metric_type": "COSINE", "params": {"nprobe": 10}},
    limit=5,
    expr='dataset_id in ["정보보안교육", "직무교육"]',
    output_fields=["text", "doc_id", "dataset_id"]
)
```

---

## 2. AI Gateway API 연동

### 2.1 아키텍처

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│ ctrlf-back  │────▶│  AI Gateway     │────▶│  Milvus     │
│ (Spring)    │     │  (FastAPI)      │     │  (벡터 DB)  │
└─────────────┘     └─────────────────┘     └─────────────┘
                            │
                            ▼
                    ┌─────────────────┐
                    │  LLM Server     │
                    │  (Qwen2.5-7B)   │
                    └─────────────────┘
```

### 2.2 API 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/health` | GET | 서버 상태 확인 |
| `/health/ready` | GET | 준비 상태 확인 |
| `/ai/chat/messages` | POST | **채팅 응답 생성** |
| `/ai/rag/process` | POST | RAG 문서 처리 |
| `/ai/gap/policy-edu/suggestions` | POST | RAG Gap 보완 제안 |

### 2.3 환경 설정

```bash
# 서버 실행
git clone https://github.com/skRookies4team/ctrlf-ai.git
cd ctrlf-ai
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**.env 설정:**
```env
LLM_BASE_URL=http://your-llm-server:port/v1
RAGFLOW_BASE_URL=http://your-ragflow-host:9380
MILVUS_HOST=your-milvus-host
MILVUS_PORT=19540
MILVUS_COLLECTION_NAME=ragflow_chunks_sroberta
```

---

## 3. 채팅 API 상세

### 3.1 요청

**POST** `/ai/chat/messages`

```json
{
  "session_id": "sess-uuid-1234",
  "user_id": "emp-001",
  "user_role": "EMPLOYEE",
  "domain": "POLICY",
  "department": "개발팀",
  "channel": "WEB",
  "messages": [
    {
      "role": "user",
      "content": "연차 이월 규정이 어떻게 되나요?"
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | string | O | 세션 식별자 |
| `user_id` | string | O | 사용자 ID |
| `user_role` | string | O | `EMPLOYEE`, `ADMIN`, `INCIDENT_MANAGER` |
| `domain` | string | O | `POLICY`, `EDU`, `INCIDENT` |
| `department` | string | X | 부서명 |
| `channel` | string | X | `WEB`, `MOBILE`, `SLACK` |
| `messages` | array | O | 메시지 배열 |

### 3.2 응답

```json
{
  "answer": "연차휴가는 다음 해 말일까지 최대 10일까지 이월할 수 있습니다.\n\n[참고 근거]\n- 연차휴가 관리 규정 제10조(연차 이월) 참조",
  "sources": [
    {
      "doc_id": "doc-001",
      "title": "연차휴가 관리 규정",
      "page": 5,
      "score": 0.92,
      "snippet": "연차는 다음 해 말일까지 최대 10일까지 이월할 수 있다...",
      "article_label": "제10조(연차 이월) 참조"
    }
  ],
  "meta": {
    "user_role": "EMPLOYEE",
    "route": "RAG_INTERNAL",
    "intent": "POLICY_QA",
    "rag_used": true,
    "latency_ms": 1250
  }
}
```

### 3.3 Java/Spring 연동 예시

```java
@Service
public class AiGatewayClient {

    private final WebClient webClient;

    public AiGatewayClient(@Value("${ai.gateway.url}") String baseUrl) {
        this.webClient = WebClient.builder()
            .baseUrl(baseUrl)
            .build();
    }

    public Mono<ChatResponse> chat(ChatRequest request) {
        return webClient.post()
            .uri("/ai/chat/messages")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(request)
            .retrieve()
            .bodyToMono(ChatResponse.class);
    }
}
```

### 3.4 curl 테스트

```bash
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "user_id": "emp-001",
    "user_role": "EMPLOYEE",
    "domain": "POLICY",
    "messages": [{"role": "user", "content": "연차 이월 규정이 어떻게 되나요?"}]
  }'
```

---

## 4. 에러 처리

### 4.1 HTTP 상태 코드

| 코드 | 설명 |
|------|------|
| 200 | 성공 |
| 400 | 잘못된 요청 |
| 422 | 유효성 검증 실패 |
| 500 | 서버 내부 오류 |
| 503 | 서비스 불가 |

### 4.2 Fallback 응답

```json
{
  "answer": "죄송합니다. 현재 AI 서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.",
  "sources": [],
  "meta": {
    "route": "ERROR",
    "error_type": "UPSTREAM_TIMEOUT"
  }
}
```

---

## 5. 체크리스트

### Milvus 직접 연동 시

- [ ] Milvus 연결 확인 (`your-milvus-host:19540`)
- [ ] 임베딩 서버 연결 확인 (`your-embedding-server:port`)
- [ ] `jhgan/ko-sroberta-multitask` 모델 사용
- [ ] 768차원 벡터 확인
- [ ] `collection.load()` 호출
- [ ] dataset_id는 한글 사용 (예: `사내규정`)

### AI Gateway 연동 시

- [ ] `/health` 엔드포인트로 연결 확인
- [ ] `session_id`, `user_id`, `user_role` 필수 전달
- [ ] 타임아웃 설정 (권장: 30초)
- [ ] Fallback 응답 처리

---

## 문의

- GitHub: https://github.com/skRookies4team/ctrlf-ai
- Swagger: http://[AI_GATEWAY_HOST]:8000/docs
