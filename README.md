# CTRL+F AI Gateway

CTRL+F 프로젝트의 AI Gateway 서비스입니다.
FastAPI 기반으로 RAG, LLM, 벡터 검색, 교육 영상 자동 생성 기능을 제공합니다.

## 주요 기능

- **AI 채팅**: 사규/정책, 교육, HR 관련 질의응답 (RAG + LLM)
- **스트리밍 응답**: 실시간 토큰 스트리밍 지원
- **RAG 검색**: Milvus/RAGFlow 기반 문서 검색
- **교육 영상 생성**: 씬 기반 RAG 스크립트 생성 → TTS → 영상 렌더링 파이프라인
- **FAQ/퀴즈 생성**: 문서 기반 FAQ, 퀴즈 자동 생성
- **PII 마스킹**: 개인정보 자동 탐지 및 마스킹
- **의도 분류**: 사용자 질문 의도 분류 및 라우팅
- **금지질문 필터**: Exact → Fuzzy → Embedding 3단계 매칭

## 연동 서비스

| 서비스          | 주소                     | 설명                            |
| --------------- | ------------------------ | ------------------------------- |
| **vLLM**        | `your-llm-server:port`   | LLM (EXAONE-3.5-7.8B-Instruct)  |
| **Embedding**   | OpenAI API               | 임베딩 (text-embedding-3-large) |
| **Milvus**      | `your-milvus-host:19540` | 벡터 DB                         |
| **RAGFlow**     | `localhost:9380`         | RAG 파이프라인 (문서 처리)      |
| **ctrlf-back**  | Spring                   | 백엔드 API                      |
| **ctrlf-front** | React                    | 프론트엔드                      |

---

## 실행 방법

### 방법 1: Docker (프로덕션/배포)

```bash
# 환경변수 설정
cp .env.example .env
# .env 파일에서 필수 값 설정 (RAGFLOW_BASE_URL_REAL, LLM_BASE_URL_REAL, BACKEND_BASE_URL_REAL, RAGFLOW_API_KEY, OPENAI_API_KEY)

# 실행
docker compose --profile real up -d

# 확인
curl http://localhost:8000/health
```

### 방법 2: 로컬 개발 (Hot Reload)

```bash
# 가상환경 생성 및 활성화
python -m venv venv
.\venv\Scripts\activate  # Windows
source venv/bin/activate # Linux/Mac

# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일 수정

# 서버 실행 (코드 수정 시 자동 재시작)
uvicorn app.main:app --reload --port 8000

# 확인
curl http://localhost:8000/health
```

### Mock 모드 (외부 서비스 없이 테스트)

```bash
# Docker Mock 모드
docker compose --profile mock up -d

# 또는 로컬에서 AI_ENV=mock 설정 후 실행
```

---

## API 테스트

### Swagger UI

브라우저에서 http://localhost:8000/docs 접속

### CLI 도구

```bash
python chat_cli.py
```

### curl

```bash
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test",
    "user_id": "user1",
    "user_role": "EMPLOYEE",
    "domain": "POLICY",
    "messages": [{"role": "user", "content": "연차휴가 규정 알려줘"}]
  }'
```

---

## 환경변수 (.env)

```env
# AI 환경 (mock / real)
AI_ENV=real

# LLM 서버 (vLLM)
LLM_BASE_URL=http://your-llm-server:port
LLM_MODEL_NAME=LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct

# 임베딩 (OpenAI API)
OPENAI_API_KEY=your-openai-api-key
OPENAI_EMBED_MODEL=text-embedding-3-large
OPENAI_EMBED_DIM=3072

# Milvus
MILVUS_ENABLED=true
MILVUS_HOST=your-server-host
MILVUS_PORT=19540
MILVUS_COLLECTION_NAME=ragflow_chunks_openai

# RAGFlow (MILVUS_ENABLED=false일 때 사용)
RAGFLOW_BASE_URL=http://localhost:9380
RAGFLOW_API_KEY=your-api-key
```

전체 환경변수는 `.env.example` 참고

---

## API 엔드포인트

### 채팅

| 메서드 | 경로                | 설명          |
| ------ | ------------------- | ------------- |
| POST   | `/ai/chat/messages` | AI 채팅       |
| POST   | `/ai/chat/stream`   | 스트리밍 채팅 |

### 교육 영상 생성

| 메서드 | 경로                                   | 설명             |
| ------ | -------------------------------------- | ---------------- |
| POST   | `/internal/ai/source-sets/{id}/start`  | 소스셋 처리 시작 |
| GET    | `/internal/ai/source-sets/{id}/status` | 처리 상태 조회   |
| POST   | `/internal/ai/render-jobs`             | 렌더 잡 생성     |
| POST   | `/ai/video/job/{job_id}/start`         | 영상 생성 시작   |

### FAQ/퀴즈

| 메서드 | 경로                | 설명      |
| ------ | ------------------- | --------- |
| POST   | `/ai/faq/generate`  | FAQ 생성  |
| POST   | `/ai/quiz/generate` | 퀴즈 생성 |

### Internal RAG

| 메서드 | 경로                     | 설명           |
| ------ | ------------------------ | -------------- |
| POST   | `/internal/rag/index`    | 문서 인덱싱    |
| POST   | `/internal/rag/delete`   | 문서 삭제      |
| GET    | `/internal/jobs/{jobId}` | 작업 상태 조회 |

---

## 테스트

```bash
# 전체 테스트
pytest

# 상세 출력
pytest -v

# 특정 테스트
pytest tests/test_internal_rag.py -v
```

---

## Docker + ELK 로그 수집

```bash
# 네트워크 생성 (최초 1회)
docker network create ctrlf-network

# AI Gateway + ELK 실행
docker compose -f docker-compose.yml -f elk/docker-compose.elk.yml --profile real up -d

# Kibana 접속
http://localhost:5601
```

자세한 ELK 설정은 [elk/README.md](elk/README.md) 참고

---

## 프로젝트 구조

```
ctrlf-ai/
├── app/
│   ├── main.py                 # FastAPI 진입점
│   ├── api/v1/                 # API 엔드포인트
│   ├── clients/                # 외부 서비스 클라이언트
│   ├── services/               # 비즈니스 로직
│   ├── models/                 # Pydantic 모델
│   └── core/                   # 설정, 로깅
├── tests/                      # 테스트
├── docs/                       # 개발 문서
├── elk/                        # ELK 로그 수집 설정
├── mock_*/                     # Mock 서버들
├── chat_cli.py                 # 채팅 CLI 도구
├── requirements.txt
└── .env
```

---

## 라이선스

Private - CTRL+F Team

---

## 기능별 테스트 가이드

### 1. Elasticsearch 로그 적재 테스트

AI 채팅 요청 시 운영 로그와 FAQ 후보 로그가 자동으로 Elasticsearch에 적재됩니다.

#### 1-1. ES + Kibana 실행

```bash
# ES + Kibana 실행
docker compose up elasticsearch kibana -d

# ES 상태 확인 (약 30초 대기 후)
curl http://localhost:9200
```

#### 1-2. 테스트 스크립트 실행

```bash
# ES 상태만 확인
python scripts/test_es_log.py --es-only

# 채팅 API + ES 로그 확인 (서버가 실행 중이어야 함)
python scripts/test_es_log.py --full

# ES URL 지정
python scripts/test_es_log.py --es-url http://localhost:9200
```

#### 1-3. ES에서 로그 직접 조회

```bash
# 인덱스 목록 확인
curl http://localhost:9200/_cat/indices?v

# AI 운영 로그 조회
curl -X GET "http://localhost:9200/ctrlf-logs-*/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{"query": {"match_all": {}}, "size": 10, "sort": [{"@timestamp": "desc"}]}'

# FAQ 후보 로그 조회
curl -X GET "http://localhost:9200/ctrlf-faq-log-*/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{"query": {"match_all": {}}, "size": 10, "sort": [{"@timestamp": "desc"}]}'
```

#### 1-4. Kibana에서 확인

1. **접속**: http://localhost:5601
2. **메뉴**: Stack Management → Index Patterns
3. **인덱스 패턴 생성**: `ctrlf-logs-*` 또는 `ctrlf-faq-log-*`
4. **메뉴**: Discover → 로그 확인

#### 로그 인덱스 구조

| 인덱스 패턴                | 용도          | 주요 필드                                                |
| -------------------------- | ------------- | -------------------------------------------------------- |
| `ctrlf-logs-YYYY.MM.DD`    | AI 운영 로그  | domain, intent, question_masked, answer_masked, rag_used |
| `ctrlf-faq-log-YYYY.MM.DD` | FAQ 후보 로그 | domain, intent, question_masked, source                  |

---

### 2. FAQ API 테스트

#### 2-1. 테스트 스크립트 실행

```bash
# Mock 기반 단위 테스트
python scripts/test_faq_api_http.py
```

#### 2-2. 실제 서버 HTTP 호출

```bash
# 서버 실행
uvicorn app.main:app --port 8000

# FAQ 단건 생성
curl -X POST http://localhost:8000/ai/faq/generate \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "SEC_POLICY",
    "cluster_id": "cluster-001",
    "canonical_question": "USB 반출 절차는?",
    "sample_questions": ["USB 어떻게 반출해요?"],
    "top_docs": [{
      "doc_id": "SEC-001",
      "title": "정보보안 정책",
      "snippet": "USB 반출 시 정보보호팀 승인 필요"
    }]
  }'

# FAQ 배치 생성
curl -X POST http://localhost:8000/ai/faq/generate/batch \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {"domain": "SEC_POLICY", "cluster_id": "c1", "canonical_question": "USB 반출 절차?"},
      {"domain": "HR_POLICY", "cluster_id": "c2", "canonical_question": "연차 신청 방법?"}
    ],
    "concurrency": 2
  }'

# FAQ 자동 생성 (로그 분석 기반)
curl -X POST http://localhost:8000/ai/faq/generate/auto \
  -H "Content-Type: application/json" \
  -d '{"domain": "SEC_POLICY", "min_frequency": 3, "days_back": 30}'
```

---

### 3. 채팅 API 테스트

#### 3-1. 기본 채팅

```bash
curl -X POST http://localhost:8000/ai/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "user_id": "user1",
    "message": "연차 규정 알려줘",
    "channel": "WEB",
    "user_role": "EMPLOYEE",
    "department": "개발팀"
  }'
```

#### 3-2. 스트리밍 채팅

```bash
curl -X POST http://localhost:8000/ai/chat/stream \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "session_id": "test-002",
    "user_id": "user1",
    "message": "정보보안 교육 내용 요약해줘",
    "channel": "WEB",
    "stream": true
  }'
```

#### 3-3. 채팅 CLI 도구

```bash
python chat_cli.py
```

---

### 4. RAGFlow Callback API 테스트

```bash
# 테스트 스크립트 실행
python scripts/test_callback_api.py

# 원격 서버 테스트
python scripts/test_callback_api.py http://your-server:8000
```

---

### 5. 단위/통합 테스트

#### 5-1. 전체 테스트

```bash
# 전체 테스트 실행
pytest

# 상세 출력
pytest -v

# 병렬 실행 (빠름)
pytest -n auto
```

#### 5-2. 기능별 테스트

```bash
# FAQ 관련 테스트
pytest tests/ -k "faq" -v

# 채팅 관련 테스트
pytest tests/ -k "chat" -v

# RAG 관련 테스트
pytest tests/ -k "rag" -v

# PII 마스킹 테스트
pytest tests/ -k "pii" -v

# 의도 분류 테스트
pytest tests/ -k "intent" -v
```

#### 5-3. 특정 파일 테스트

```bash
pytest tests/unit/test_phase50_low_relevance_gate.py -v
pytest tests/unit/test_ai_log.py -v
pytest tests/unit/test_faq_api_phase19.py -v
```

---

### 6. QA 배치 테스트 및 품질 평가

```bash
# 배치 테스트
python scripts/test/qa_batch_test.py

# 품질평가 (GPT-4o-mini 기반)
python scripts/test/qa_quality_evaluator.py -n 30 --model gpt-4o-mini
```

---

### 7. 헬스체크

```bash
# 서버 상태 확인
curl http://localhost:8000/health

# 예상 응답
# {"status":"ok","app":"ctrlf-ai-gateway","version":"0.1.0","env":"local"}
```
