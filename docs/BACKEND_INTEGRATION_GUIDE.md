# 백엔드팀 연동 가이드

> **최종 수정일**: 2025-12-28
> **대상**: ctrlf-back (Spring Backend) 개발팀

---

## 핵심 요약 (Quick Start)

### 서비스 연결 정보

| 서비스 | URL | 용도 |
|--------|-----|------|
| **Milvus** | `<MILVUS_HOST>:<MILVUS_PORT>` | 벡터 DB (RAG 검색) |
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

1. [로컬 테스트 환경 구축](#1-로컬-테스트-환경-구축)
2. [Milvus 직접 연동](#2-milvus-직접-연동)
3. [AI Gateway API 연동](#3-ai-gateway-api-연동)
4. [채팅 API 상세](#4-채팅-api-상세)
5. [에러 처리](#5-에러-처리)
6. [체크리스트](#6-체크리스트)

---

## 1. 로컬 테스트 환경 구축

### 1.1 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────────┐
│                        로컬 PC                                  │
│  ┌─────────────────┐      ┌─────────────────┐                  │
│  │  AI Gateway     │◄────►│  Backend        │                  │
│  │  (FastAPI)      │      │  (Spring)       │                  │
│  │  localhost:8000 │      │  localhost:9002 │                  │
│  └────────┬────────┘      └─────────────────┘                  │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    외부 GPU 서버                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ vLLM (LLM)   │  │ Embedding    │  │ Milvus       │          │
│  │ :<LLM_PORT>  │  │ :<EMB_PORT>  │  │ :<MILVUS_PORT>│         │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 서비스별 역할

| 서비스 | 위치 | 포트 | 역할 |
|--------|------|------|------|
| **AI Gateway** | 로컬 | 8000 | 채팅/RAG API, LLM 호출 |
| **Backend** | 로컬 | 9002 | 콜백 수신, 사용자/세션 관리 |
| **vLLM** | 외부 | .env 참조 | LLM 응답 생성 (Qwen2.5-7B) |
| **Embedding** | 외부 | .env 참조 | 임베딩 벡터 생성 (ko-sroberta) |
| **Milvus** | 외부 | .env 참조 | 벡터 검색 |
| **RAGFlow** | - | - | 불필요 (MILVUS_ENABLED=true) |

### 1.3 AI Gateway 실행 (터미널 1)

```bash
# 1. ctrlf-ai 폴더로 이동
cd ctrlf-ai

# 2. 가상환경 생성 & 활성화
python -m venv venv
.\venv\Scripts\activate        # Windows
# source venv/bin/activate     # Linux/Mac

# 3. 의존성 설치
pip install -r requirements.txt

# 4. .env 확인 (기본 설정 그대로 사용)
# AI_ENV=real
# LLM_BASE_URL=http://<GPU_SERVER_IP>:<LLM_PORT>
# EMBEDDING_BASE_URL=http://<GPU_SERVER_IP>:<EMBEDDING_PORT>
# MILVUS_HOST=<GPU_SERVER_IP>
# MILVUS_PORT=<MILVUS_PORT>
# MILVUS_ENABLED=true
# BACKEND_BASE_URL=http://localhost:9002

# 5. 서버 실행
uvicorn app.main:app --reload --port 8000
```

**확인:**
```bash
curl http://localhost:8000/health
```

### 1.4 Backend 실행 (터미널 2~6)

백엔드는 여러 마이크로서비스로 구성됨. 필요한 서비스만 실행.

#### 백엔드 서비스 목록

| 서비스 | 포트 | 용도 | AI 연동 |
|--------|------|------|---------|
| `api-gateway` | 9000 | API 게이트웨이 | - |
| `chat-service` | 9002 | 채팅 콜백 수신 | ✅ 채팅 |
| `education-service` | - | 교육/영상 관리 | ✅ 스크립트/영상 |
| `infra-service` | 9003 | S3 presigned URL | ✅ 파일 업로드 |
| `quiz-service` | - | 퀴즈 관리 | ✅ 퀴즈 |

#### Windows (PowerShell)

```powershell
cd ctrlf-back

# AWS 프로파일 설정 (S3 연동 필요 시)
$env:AWS_PROFILE = "sk_4th_team04"

# 각 서비스 별도 터미널에서 실행
./gradlew :chat-service:bootRun
./gradlew :education-service:bootRun --no-configuration-cache
./gradlew :infra-service:bootRun --no-configuration-cache
./gradlew :quiz-service:bootRun
./gradlew :api-gateway:bootRun
```

#### Mac/Linux

```bash
cd ctrlf-back

# 각 서비스 별도 터미널에서 실행
AWS_PROFILE=sk_4th_team04 ./gradlew :chat-service:bootRun
AWS_PROFILE=sk_4th_team04 ./gradlew :education-service:bootRun --no-configuration-cache
AWS_PROFILE=sk_4th_team04 ./gradlew :infra-service:bootRun --no-configuration-cache
AWS_PROFILE=sk_4th_team04 ./gradlew :quiz-service:bootRun
AWS_PROFILE=sk_4th_team04 ./gradlew :api-gateway:bootRun
```

#### 기능별 필요한 백엔드 서비스

| 테스트 기능 | 필요한 백엔드 서비스 |
|------------|---------------------|
| 채팅만 테스트 | `chat-service` |
| 스크립트/영상 생성 | `education-service`, `infra-service` |
| 퀴즈 생성 | `quiz-service` |
| 전체 테스트 | 모두 실행 |

> **주의**: `chat-service`는 `9002` 포트로 실행되어야 AI Gateway의 `BACKEND_BASE_URL`과 일치합니다.

### 1.5 테스트

**채팅 API:**
```bash
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "user_id": "emp-001",
    "user_role": "EMPLOYEE",
    "domain": "POLICY",
    "messages": [{"role": "user", "content": "연차휴가 규정 알려줘"}]
  }'
```

**CLI 테스트:**
```bash
python chat_cli.py
```

**Swagger UI:**
```
http://localhost:8000/docs
```

### 1.6 스크립트/영상 생성 테스트

#### 전체 흐름 (Backend → AI)

```
Backend                         AI Gateway
   │                                │
   │  POST /internal/ai/source-sets/{id}/start
   │ ─────────────────────────────► │
   │                                │ (문서 처리 + 스크립트 생성)
   │  ◄───────── 콜백 ───────────── │
   │                                │
   │  POST /internal/ai/render-jobs │
   │ ─────────────────────────────► │
   │                                │ (TTS + 영상 렌더링)
   │  ◄───────── 콜백 ───────────── │
```

#### 스크립트 생성 API 테스트 (curl)

```bash
# SourceSet 처리 시작 (스크립트 자동 생성)
curl -X POST http://localhost:8000/internal/ai/source-sets/test-source-set-001/start \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: your-token" \
  -d '{
    "video_id": "video-001",
    "education_id": "edu-001"
  }'
```

**응답 (202 Accepted):**
```json
{
  "source_set_id": "test-source-set-001",
  "status": "PROCESSING",
  "message": "Processing started"
}
```

#### 렌더 잡 API 테스트 (curl)

```bash
# 렌더 잡 시작 (영상 생성)
curl -X POST http://localhost:8000/internal/ai/render-jobs \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: your-token" \
  -d '{
    "job_id": "render-job-001",
    "video_id": "video-001",
    "script_id": "script-001"
  }'
```

**응답 (202 Accepted):**
```json
{
  "job_id": "render-job-001",
  "status": "PROCESSING"
}
```

#### 테스트 스크립트 사용 (script.json → 영상)

```bash
cd ctrlf-ai/scripts

# script.json 파일로 영상 생성 테스트
python test_script_video.py
```

**결과:** `test_output_script/` 폴더에 영상 생성
- `video.mp4` - 렌더링된 영상
- `audio.mp3` - TTS 오디오
- `subtitle.srt` - 자막

#### 필요한 환경변수 (.env)

```env
# TTS 설정 (gtts = 무료, polly = AWS 유료)
TTS_PROVIDER=gtts

# 영상 스타일 (basic = 텍스트만, animated = 이미지+효과)
VIDEO_VISUAL_STYLE=basic

# 렌더링 출력 디렉토리
RENDER_OUTPUT_DIR=./video_output
```

#### FFmpeg 설치 (영상 렌더링 필수)

```bash
# Windows
winget install FFmpeg

# Mac
brew install ffmpeg

# Linux
sudo apt install ffmpeg

# 설치 확인
ffmpeg -version
```

### 1.7 기능별 필요 서비스

| 기능 | AI Gateway | Backend | LLM | Milvus | Embedding | FFmpeg |
|------|:----------:|:-------:|:---:|:------:|:---------:|:------:|
| 채팅 (RAG) | O | - | O | O | O | - |
| 채팅 로깅/콜백 | O | chat-service | O | O | O | - |
| 스크립트 생성 | O | education-service | O | O | O | - |
| 영상 렌더링 | O | education-service, infra-service | - | - | - | **O** |
| FAQ/퀴즈 생성 | O | quiz-service | O | O | O | - |

### 1.8 트러블슈팅

**외부 서버 연결 안 될 때:**
```bash
# LLM 서버 확인
curl http://<GPU_SERVER_IP>:<LLM_PORT>/health

# Embedding 서버 확인
curl http://<GPU_SERVER_IP>:<EMBEDDING_PORT>/health

# Milvus 확인
curl http://<GPU_SERVER_IP>:<MILVUS_PORT>/health
```

**Backend 콜백 에러:**
```
ERROR | Failed to send callback: Name or service not known
```
→ Backend가 `localhost:9002`에서 실행 중인지 확인

**Docker로 AI Gateway 띄울 때:**
```env
# .env에서 localhost 대신 host.docker.internal 사용
BACKEND_BASE_URL=http://host.docker.internal:9002
```

**FFmpeg 없음 에러:**
```
[ERROR] FFmpeg not found
```
→ FFmpeg 설치 필요: `winget install FFmpeg` (Windows) / `brew install ffmpeg` (Mac)

**영상 렌더링 실패:**
1. FFmpeg 설치 확인: `ffmpeg -version`
2. 출력 디렉토리 권한 확인
3. 디스크 공간 확인

---

## 2. Milvus 직접 연동

### 2.1 컬렉션 스키마

| 필드 | 타입 | 설명 |
|------|------|------|
| `pk` | INT64 | Primary Key (자동 생성) |
| `dataset_id` | VARCHAR | 데이터셋 ID (한글 카테고리명) |
| `doc_id` | VARCHAR | 문서 ID (파일명) |
| `chunk_id` | INT64 | 청크 번호 (0부터 시작) |
| `chunk_hash` | VARCHAR | 청크 해시값 |
| `text` | VARCHAR | 청크 텍스트 내용 |
| `embedding` | FLOAT_VECTOR(768) | 임베딩 벡터 |

### 2.2 임베딩 생성 API

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

### 2.3 Python 검색 예시

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

### 2.4 Java 검색 예시

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

### 2.5 특정 Dataset 필터링

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

## 3. AI Gateway API 연동

### 3.1 아키텍처

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

### 3.2 API 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/health` | GET | 서버 상태 확인 |
| `/health/ready` | GET | 준비 상태 확인 |
| `/ai/chat/messages` | POST | **채팅 응답 생성** |
| `/ai/rag/process` | POST | RAG 문서 처리 |
| `/ai/gap/policy-edu/suggestions` | POST | RAG Gap 보완 제안 |

### 3.3 환경 설정

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

## 4. 채팅 API 상세

### 4.1 요청

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

### 4.2 응답

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

### 4.3 Java/Spring 연동 예시

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

### 4.4 curl 테스트

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

## 5. 에러 처리

### 5.1 HTTP 상태 코드

| 코드 | 설명 |
|------|------|
| 200 | 성공 |
| 400 | 잘못된 요청 |
| 422 | 유효성 검증 실패 |
| 500 | 서버 내부 오류 |
| 503 | 서비스 불가 |

### 5.2 Fallback 응답

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

## 6. 체크리스트

### Milvus 직접 연동 시

- [ ] Milvus 연결 확인 (`<MILVUS_HOST>:<MILVUS_PORT>`)
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
