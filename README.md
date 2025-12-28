# CTRL+F AI Gateway

CTRL+F 프로젝트의 AI Gateway 서비스입니다.
FastAPI 기반으로 RAG, LLM, 벡터 검색, 교육 영상 자동 생성 기능을 제공합니다.

## 주요 기능

- **AI 채팅**: 사규/정책, 교육, HR 관련 질의응답 (RAG + LLM)
- **스트리밍 응답**: 실시간 토큰 스트리밍 지원
- **RAG 검색**: Milvus/RAGFlow 기반 문서 검색
- **교육 영상 생성**: 스크립트 생성 → TTS → 영상 렌더링 파이프라인
- **FAQ/퀴즈 생성**: 문서 기반 FAQ, 퀴즈 자동 생성
- **PII 마스킹**: 개인정보 자동 탐지 및 마스킹
- **의도 분류**: 사용자 질문 의도 분류 및 라우팅

## 연동 서비스

| 서비스          | 주소                  | 설명                      |
| --------------- | --------------------- | ------------------------- |
| **vLLM**        | `your-llm-server:port`  | LLM (Qwen2.5-7B-Instruct) |
| **Embedding**   | `your-embedding-server:port`  | 임베딩 (ko-sroberta)      |
| **Milvus**      | `your-milvus-host:19540` | 벡터 DB                   |
| **RAGFlow**     | `localhost:9380`      | RAG 파이프라인            |
| **ctrlf-back**  | Spring                | 백엔드 API                |
| **ctrlf-front** | React                 | 프론트엔드                |

## 빠른 시작

### 1. 환경 설정

```bash
# 가상환경 생성
python -m venv venv

# 가상환경 활성화
# Windows PowerShell:
.\venv\Scripts\activate
# Windows CMD:
venv\Scripts\activate.bat
# Linux/Mac:
source venv/bin/activate

# 의존성 설치 (Python 3.12+ 권장)
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env   # Linux/Mac
copy .env.example .env # Windows

# .env 파일에서 서비스 URL 수정
```

### 2. 서버 실행

```bash
# 개발 모드
uvicorn app.main:app --reload --port 8000

# 프로덕션 모드
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3. 확인

```bash
# 헬스체크
curl http://localhost:8000/health

# API 문서 (브라우저)
http://localhost:8000/docs
```

## 채팅 테스트

### CLI 도구 (권장)

`chat_cli.py`는 실제 서비스(vLLM, Milvus, RAGFlow)를 사용하여 챗봇을 테스트합니다.

```
chat_cli.py → localhost:8000 (FastAPI) → 실제 서비스들
                                         ├── vLLM (LLM 응답 생성)
                                         ├── Milvus (벡터 검색)
                                         └── RAGFlow (RAG 파이프라인)
```

**사용 방법:**

```bash
# 1. 서버 실행 (터미널 1)
uvicorn app.main:app --reload --port 8000

# 2. CLI 테스트 (터미널 2)
python chat_cli.py
```

**실행 예시:**

```
==================================================
CTRL+F AI 채팅 테스트 (종료: q 또는 Ctrl+C)
==================================================

질문> 연차휴가 규정 알려줘
응답 대기중...

연차휴가는 근로기준법에 따라...

(RAG_INTERNAL | 3500ms)

질문> q
종료합니다.
```

> **참고**: `.env`의 `AI_ENV=real` 설정 시 실제 서비스 연동, `AI_ENV=mock` 설정 시 Mock 응답

**필수 서비스:**

| 환경변수               | RAGFlow | Milvus | vLLM | 설명                    |
| ---------------------- | ------- | ------ | ---- | ----------------------- |
| `MILVUS_ENABLED=true`  | 불필요  | 필수   | 필수 | Milvus 직접 연동 (권장) |
| `MILVUS_ENABLED=false` | 필수    | 불필요 | 필수 | RAGFlow 파이프라인 사용 |

- **vLLM**: 항상 필수 (LLM 응답 생성, 임베딩)
- **RAGFlow 없이 테스트**: `MILVUS_ENABLED=true` 설정 후 Milvus만 연결하면 됨
- **모든 서비스 없이 테스트**: `AI_ENV=mock` 설정 시 Mock 응답 반환

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

## API 엔드포인트

### 채팅

| 메서드 | 경로                | 설명          |
| ------ | ------------------- | ------------- |
| POST   | `/ai/chat/messages` | AI 채팅       |
| POST   | `/ai/chat/stream`   | 스트리밍 채팅 |

### 교육 영상 생성

| 메서드 | 경로                           | 설명             |
| ------ | ------------------------------ | ---------------- |
| POST   | `/api/scripts`                 | 스크립트 생성    |
| GET    | `/api/scripts/{id}`            | 스크립트 조회    |
| POST   | `/api/scripts/{id}/approve`    | 스크립트 승인    |
| POST   | `/api/videos/{id}/render-jobs` | 영상 렌더링 시작 |
| GET    | `/api/render-jobs/{id}`        | 렌더링 상태 조회 |

### FAQ/퀴즈

| 메서드 | 경로                | 설명      |
| ------ | ------------------- | --------- |
| POST   | `/ai/faq/generate`  | FAQ 생성  |
| POST   | `/ai/quiz/generate` | 퀴즈 생성 |

### Internal RAG (백엔드 연동)

| 메서드 | 경로                     | 설명           |
| ------ | ------------------------ | -------------- |
| POST   | `/internal/rag/index`    | 문서 인덱싱    |
| POST   | `/internal/rag/delete`   | 문서 삭제      |
| GET    | `/internal/jobs/{jobId}` | 작업 상태 조회 |

## 환경변수 (.env)

```env
# AI 환경 (mock / real)
AI_ENV=real

# LLM 서버 (vLLM - 채팅 + 임베딩 통합)
LLM_BASE_URL=http://your-llm-server:port
LLM_MODEL_NAME=Qwen/Qwen2.5-7B-Instruct
EMBEDDING_MODEL_NAME=BAAI/bge-m3

# Milvus (MILVUS_ENABLED=true면 RAGFlow 대신 Milvus 직접 사용)
MILVUS_ENABLED=true
MILVUS_HOST=your-server-host
MILVUS_PORT=19530
MILVUS_COLLECTION_NAME=ragflow_chunks

# RAGFlow (MILVUS_ENABLED=false일 때 사용)
RAGFLOW_BASE_URL=http://localhost:9380
RAGFLOW_API_KEY=your-api-key

# TTS (mock / gtts / polly / gcp)
TTS_PROVIDER=gtts

# Storage (local / s3 / backend_presigned)
STORAGE_PROVIDER=local
```

## 테스트

```bash
# 전체 테스트
pytest

# 상세 출력
pytest -v

# 특정 테스트
pytest tests/test_internal_rag.py -v
```

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
├── chat_cli.py                 # 채팅 CLI 도구
├── requirements.txt
└── .env
```

## 아키텍처

```
┌─────────────┐     ┌─────────────────────────────────────┐
│  Frontend   │────▶│         AI Gateway (FastAPI)        │
│  (React)    │     │                                     │
└─────────────┘     │  ┌─────────────────────────────┐    │
                    │  │     Router Orchestrator      │    │
┌─────────────┐     │  │  (Rule Router + LLM Router)  │    │
│  Backend    │────▶│  └─────────────────────────────┘    │
│  (Spring)   │     │                │                    │
└─────────────┘     │  ┌─────────────▼─────────────┐      │
                    │  │      Chat / Video Service  │      │
                    │  │  (RAG, LLM, TTS, Render)   │      │
                    │  └─────────────┬─────────────┘      │
                    └────────────────┼────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
              ┌──────────┐    ┌──────────┐    ┌──────────┐
              │  Milvus  │    │   vLLM   │    │ Embedding│
              │ (Vector) │    │  (Chat)  │    │(ko-sbert)│
              └──────────┘    └──────────┘    └──────────┘
```

## 개발 히스토리

### 영상 생성 파이프라인

- **Phase 37**: Ken Burns + Fade 효과
- **Phase 36**: Presigned 업로드
- **Phase 33-35**: Render Job 운영화
- **Phase 29-32**: KB Index, Script Gen, Video Rendering

### RAG/채팅

- **Phase 25**: Internal RAG (Milvus 직접 연동)
- **Phase 24**: Milvus 벡터 검색
- **Phase 22-23**: Router Orchestrator, 개인화
- **Phase 10-12**: 역할×도메인 라우팅, Fallback

## 라이선스

Private - CTRL+F Team
