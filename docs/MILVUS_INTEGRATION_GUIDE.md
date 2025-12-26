# Milvus 연동 가이드 (백엔드팀용)

> 작성일: 2025-12-26
> 대상: ctrlf-backend 팀

---

## 1. 연결 정보

| 항목 | 값 |
|------|-----|
| **Host** | `58.127.241.84` |
| **Port** | `19540` |
| **Collection** | `ragflow_chunks_sroberta` |
| **Embedding Model** | `jhgan/ko-sroberta-multitask` |
| **Embedding Dimension** | `768` |
| **Metric Type** | `COSINE` |

---

## 2. 컬렉션 스키마 (`ragflow_chunks_sroberta`)

| 필드 | 타입 | 설명 |
|------|------|------|
| `pk` | INT64 | Primary Key (자동 생성) |
| `dataset_id` | VARCHAR | 데이터셋 ID (한글 카테고리명) |
| `doc_id` | VARCHAR | 문서 ID (파일명) |
| `chunk_id` | INT64 | 청크 번호 (0부터 시작) |
| `chunk_hash` | VARCHAR | 청크 해시값 |
| `text` | VARCHAR | 청크 텍스트 내용 |
| `embedding` | FLOAT_VECTOR(768) | 임베딩 벡터 |

---

## 3. 실제 Dataset 정보

| dataset_id | 도메인 | 문서 수 | 청크 수 |
|------------|--------|--------|--------|
| `사내규정` | POLICY | 1 | 390 |
| `정보보안교육` | EDUCATION | 9 | 1,442 |
| `직장내성희롱교육` | EDUCATION | 11 | 486 |
| `직무교육` | EDUCATION | 17 | 330 |
| `직장내괴롭힘교육` | EDUCATION | 2 | 152 |
| `장애인인식개선교육` | EDUCATION | 5 | 125 |

**총 2,925 청크**

> **주의:** `kb_policy_001` 같은 ID는 존재하지 않습니다. 실제 dataset_id는 위의 한글 값입니다.

---

## 4. 임베딩 생성

벡터 검색을 위해서는 쿼리 텍스트를 768차원 벡터로 변환해야 합니다.

### vLLM 임베딩 서버

```
URL: http://58.127.241.84:1234/v1/embeddings
Model: jhgan/ko-sroberta-multitask
```

### API 호출 예시

```bash
curl -X POST http://58.127.241.84:1234/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "input": "연차휴가 규정이 어떻게 되나요?",
    "model": "jhgan/ko-sroberta-multitask"
  }'
```

### 응답 형식 (OpenAI 호환)

```json
{
  "data": [
    {
      "embedding": [0.123, -0.456, ...],  // 768차원 벡터
      "index": 0
    }
  ],
  "model": "jhgan/ko-sroberta-multitask",
  "usage": {
    "prompt_tokens": 15,
    "total_tokens": 15
  }
}
```

---

## 5. Milvus 검색 방법

### Python (pymilvus)

```python
from pymilvus import connections, Collection
import httpx

# 1. 임베딩 생성
def get_embedding(text: str) -> list[float]:
    response = httpx.post(
        "http://58.127.241.84:1234/v1/embeddings",
        json={
            "input": text,
            "model": "jhgan/ko-sroberta-multitask"
        },
        timeout=10.0
    )
    return response.json()["data"][0]["embedding"]

# 2. Milvus 연결
connections.connect(host="58.127.241.84", port=19540)

# 3. 컬렉션 로드
collection = Collection("ragflow_chunks_sroberta")
collection.load()

# 4. 검색
query = "연차휴가 규정이 어떻게 되나요?"
query_embedding = get_embedding(query)

results = collection.search(
    data=[query_embedding],
    anns_field="embedding",
    param={
        "metric_type": "COSINE",
        "params": {"nprobe": 10}
    },
    limit=5,
    output_fields=["text", "doc_id", "dataset_id", "chunk_id"]
)

# 5. 결과 처리
for hits in results:
    for hit in hits:
        print(f"Score: {hit.score:.4f}")
        print(f"Doc: {hit.entity.get('doc_id')}")
        print(f"Dataset: {hit.entity.get('dataset_id')}")
        print(f"Text: {hit.entity.get('text')[:200]}...")
        print("---")
```

### Java (Milvus Java SDK)

```java
import io.milvus.client.*;
import io.milvus.param.*;
import io.milvus.param.dml.*;
import io.milvus.response.*;

// 1. 연결
MilvusServiceClient client = new MilvusServiceClient(
    ConnectParam.newBuilder()
        .withHost("58.127.241.84")
        .withPort(19540)
        .build()
);

// 2. 컬렉션 로드
client.loadCollection(
    LoadCollectionParam.newBuilder()
        .withCollectionName("ragflow_chunks_sroberta")
        .build()
);

// 3. 임베딩 생성 (별도 HTTP 호출 필요)
List<Float> queryEmbedding = getEmbedding("연차휴가 규정이 어떻게 되나요?");

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

---

## 6. 특정 Dataset 필터링

dataset_id로 특정 카테고리만 검색할 수 있습니다.

```python
# 사내규정만 검색
results = collection.search(
    data=[query_embedding],
    anns_field="embedding",
    param={"metric_type": "COSINE", "params": {"nprobe": 10}},
    limit=5,
    expr='dataset_id == "사내규정"',  # 필터 추가
    output_fields=["text", "doc_id", "dataset_id", "chunk_id"]
)

# 교육 관련만 검색 (여러 dataset_id)
results = collection.search(
    data=[query_embedding],
    anns_field="embedding",
    param={"metric_type": "COSINE", "params": {"nprobe": 10}},
    limit=5,
    expr='dataset_id in ["정보보안교육", "직무교육", "직장내성희롱교육"]',
    output_fields=["text", "doc_id", "dataset_id", "chunk_id"]
)
```

---

## 7. 문서의 전체 청크 조회

특정 문서의 모든 청크를 순서대로 가져올 때:

```python
# doc_id로 모든 청크 조회
chunks = collection.query(
    expr='doc_id == "사규.docx"',
    output_fields=["chunk_id", "text", "dataset_id"],
    limit=10000
)

# chunk_id로 정렬
sorted_chunks = sorted(chunks, key=lambda x: x.get("chunk_id", 0))

# 전체 텍스트 합치기
full_text = "\n\n".join([c["text"] for c in sorted_chunks])
```

---

## 8. Health Check

```python
from pymilvus import connections, utility

connections.connect(host="58.127.241.84", port=19540)

# 컬렉션 존재 확인
has_collection = utility.has_collection("ragflow_chunks_sroberta")
print(f"Collection exists: {has_collection}")

# 컬렉션 통계
if has_collection:
    collection = Collection("ragflow_chunks_sroberta")
    print(f"Entities: {collection.num_entities}")
```

---

## 9. 주의사항

1. **임베딩 필수**: 텍스트 검색이 아닌 벡터 검색입니다. 반드시 임베딩 서버를 통해 쿼리를 벡터로 변환해야 합니다.

2. **모델 일치**: 임베딩 모델은 반드시 `jhgan/ko-sroberta-multitask`를 사용해야 합니다. 다른 모델 사용 시 차원 불일치 또는 검색 품질 저하가 발생합니다.

3. **Dimension**: 768차원입니다. 1024차원 모델(BGE-M3 등)과 혼용하면 안 됩니다.

4. **dataset_id**: 한글 카테고리명입니다. `kb_policy_001` 같은 ID는 존재하지 않습니다.

5. **Collection 로드**: 검색 전 `collection.load()` 필수입니다.

---

## 10. 연락처

문의사항은 AI팀에 연락해주세요.
