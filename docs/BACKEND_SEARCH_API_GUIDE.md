# RAG 검색 API 백엔드 통합 가이드

> **작성일**: 2025-12-16  
> **대상**: ctrlf-back (Spring Backend) 개발팀  
> **API**: `POST /search`

---

## 1. 개요

`/search` API는 RAGFlow를 통해 문서를 검색하는 API입니다.  
**LLM 응답 생성 없이** 검색 결과만 반환합니다.

### 언제 사용하나요?

| 상황 | 사용 API |
|------|----------|
| 사용자 질문 → AI 답변 필요 | `/ai/chat/messages` |
| 문서 검색만 필요 (LLM 없이) | **`/search`** ✅ |
| FAQ 생성 시 top_docs 조회용 | **`/search`** ✅ |

---

## 2. API 스펙

### Endpoint

```
POST /search
```

### Request Body

```json
{
  "query": "연차휴가 규정",
  "top_k": 5,
  "dataset": "policy"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `query` | string | ✅ | 검색 쿼리 (자연어 질문) |
| `top_k` | int | ❌ | 반환할 결과 수 (기본값: 5, 최대: 100) |
| `dataset` | string | ✅ | 데이터셋 이름 (아래 목록 참조) |

### Response Body

```json
{
  "results": [
    {
      "doc_id": "chunk-abc123",
      "title": "인사규정",
      "page": 15,
      "score": 0.87,
      "snippet": "연차휴가는 1년 근무 시 15일이 부여됩니다. 신청은 최소 3일 전에...",
      "dataset": "policy",
      "source": "ragflow"
    },
    {
      "doc_id": "chunk-def456",
      "title": "휴가관리지침",
      "page": 3,
      "score": 0.82,
      "snippet": "연차휴가 이월은 다음 해 말일까지 최대 10일까지 가능합니다...",
      "dataset": "policy",
      "source": "ragflow"
    }
  ]
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `results` | array | 검색 결과 리스트 |
| `results[].doc_id` | string | 문서/청크 ID |
| `results[].title` | string | 문서 제목 |
| `results[].page` | int? | 페이지 번호 (없을 수 있음) |
| `results[].score` | float | 유사도 점수 (0.0 ~ 1.0) |
| `results[].snippet` | string | 문서 내용 발췌 |
| `results[].dataset` | string | 데이터셋 이름 |
| `results[].source` | string | 출처 (항상 "ragflow") |

---

## 3. Dataset 목록

| dataset 값 | 설명 | 용도 |
|------------|------|------|
| `policy` | 인사/경영 정책 | 연차, 복리후생, 인사규정 등 |
| `training` | 교육/훈련 자료 | 보안교육, 신입교육 등 |
| `incident` | 사건/사고 문서 | 사고 보고서, 대응 매뉴얼 등 |
| `security` | 보안 정책 | 정보보안, 접근제어 등 |
| `education` | 교육 자료 | 학습 콘텐츠 |

> **주의**: 잘못된 dataset 값을 보내면 400 에러가 발생합니다.

---

## 4. 에러 응답

### 400 Bad Request - 잘못된 dataset

```json
{
  "detail": "Dataset 'unknown' not found. Available: policy, training, incident"
}
```

### 422 Validation Error - 필수 필드 누락

```json
{
  "detail": [
    {
      "loc": ["body", "query"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

### 500 Internal Error - 서버 오류

```json
{
  "detail": "Internal server error during search"
}
```

---

## 5. 백엔드에서 해야 할 일

### 5.1 필수 구현

1. **API 호출 구현**
   ```
   POST http://[AI_GATEWAY_HOST]:8000/search
   Content-Type: application/json
   ```

2. **dataset 값 매핑**
   - 검색 대상에 따라 적절한 dataset 값 전달
   - 잘못된 dataset 전달 시 400 에러 처리

3. **빈 결과 처리**
   - `results: []` 응답 시 "검색 결과 없음" 처리

### 5.2 활용 예시

**FAQ 생성 시 top_docs 조회:**
```
1. POST /search → 검색 결과 받기
2. results를 top_docs로 변환
3. POST /ai/faq/generate 호출 시 top_docs 전달
```

---

## 6. Java/Spring 연동 예시

### DTO

```java
// Request
@Data
public class SearchRequest {
    private String query;
    private Integer topK = 5;
    private String dataset;
}

// Response
@Data
public class SearchResponse {
    private List<SearchResultItem> results;
    
    @Data
    public static class SearchResultItem {
        private String docId;
        private String title;
        private Integer page;
        private Double score;
        private String snippet;
        private String dataset;
        private String source;
    }
}
```

### Service

```java
@Service
@RequiredArgsConstructor
public class AiSearchService {
    
    private final WebClient webClient;
    
    public Mono<SearchResponse> search(String query, String dataset, int topK) {
        return webClient.post()
            .uri("/search")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(Map.of(
                "query", query,
                "dataset", dataset,
                "top_k", topK
            ))
            .retrieve()
            .bodyToMono(SearchResponse.class)
            .timeout(Duration.ofSeconds(15));
    }
}
```

---

## 7. curl 테스트

```bash
# 정책 문서 검색
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "연차휴가 신청 방법",
    "top_k": 5,
    "dataset": "policy"
  }'

# 교육 자료 검색
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "정보보안 교육 내용",
    "top_k": 10,
    "dataset": "training"
  }'
```

---

## 8. 주의사항

### score 해석

| score 범위 | 의미 |
|------------|------|
| 0.8 이상 | 매우 관련성 높음 |
| 0.6 ~ 0.8 | 관련성 있음 |
| 0.4 ~ 0.6 | 약한 관련성 |
| 0.4 미만 | 관련성 낮음 |

### 타임아웃

- 기본 타임아웃: 10초
- 백엔드에서 15초 정도로 설정 권장

### RAGFlow 미설정 시

- RAGFlow가 설정되지 않은 환경에서는 빈 결과 `{"results": []}` 반환
- 에러가 아닌 정상 응답으로 처리됨

---

## 9. 환경별 AI Gateway URL

| 환경 | URL |
|------|-----|
| 로컬 개발 | http://localhost:8000 |
| Docker Compose | http://ai-gateway:8000 |
| 프로덕션 | (배포 시 확정) |

---

## 문의

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
