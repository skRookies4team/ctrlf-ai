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
MILVUS_COLLECTION_NAME=ragflow_chunks

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
