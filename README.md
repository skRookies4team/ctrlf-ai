# ctrlf-ai-gateway

CTRL+F 프로젝트의 AI Gateway 서비스입니다.
FastAPI 기반으로 RAGFlow 및 LLM 서비스와 연동하여 AI 기능을 제공합니다.

## 연동 서비스

- **ctrlf-back** (Spring): 백엔드 비즈니스 로직
- **ctrlf-ragflow**: RAG(Retrieval-Augmented Generation) 처리
- **ctrlf-front** (React): 프론트엔드 UI

## 디렉터리 구조

```
ctrlf-ai/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI 앱 진입점
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── health.py        # 헬스체크 엔드포인트
│   │       ├── chat.py          # AI 채팅 엔드포인트
│   │       └── rag.py           # RAG 문서 처리 엔드포인트
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py            # 설정 관리
│   │   └── logging.py           # 로깅 설정
│   ├── models/
│   │   ├── __init__.py
│   │   ├── chat.py              # 채팅 관련 Pydantic 모델
│   │   └── rag.py               # RAG 관련 Pydantic 모델
│   └── services/
│       ├── __init__.py
│       ├── chat_service.py      # 채팅 비즈니스 로직
│       └── rag_service.py       # RAG 비즈니스 로직
├── tests/
│   ├── __init__.py
│   ├── test_health.py           # 헬스체크 테스트
│   ├── test_chat_api.py         # 채팅 API 테스트
│   └── test_rag_api.py          # RAG API 테스트
├── .env.example                 # 환경변수 예시
├── Dockerfile
├── README.md
└── requirements.txt
```

## 로컬 개발 환경 설정

### 1. Python 가상환경 생성

```bash
# Python 3.12.7 사용
python -m venv .venv

# 가상환경 활성화
# Linux/macOS:
source .venv/bin/activate

# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# Windows (CMD):
.\.venv\Scripts\activate.bat

# Windows (Git Bash):
source .venv/Scripts/activate
```

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

### 3. 환경변수 설정

```bash
# .env.example을 복사하여 .env 파일 생성
cp .env.example .env

# 필요에 따라 .env 파일 수정
```

### 4. 로컬 서버 실행

```bash
# 개발 모드 (자동 리로드 활성화)
uvicorn app.main:app --reload

# 또는 포트 지정
uvicorn app.main:app --reload --port 8000
```

### 5. 헬스체크 확인

```bash
# Liveness check
curl http://localhost:8000/health

# Readiness check
curl http://localhost:8000/health/ready
```

예상 응답:
```json
{
  "status": "ok",
  "app": "ctrlf-ai-gateway",
  "version": "0.1.0",
  "env": "local"
}
```

### 6. API 문서 확인

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

## 테스트 실행

```bash
# 단위 테스트 실행 (기본, 빠름)
pytest

# 상세 출력
pytest -v

# 특정 테스트 파일 실행
pytest tests/test_health.py

# 커버리지 포함 (pytest-cov 설치 필요)
pytest --cov=app tests/
```

### Docker Compose 통합 테스트 (Mock 모드)

```bash
# Mock 서비스 시작
docker compose --profile mock up -d

# Mock 통합 테스트 실행
pytest -m integration -v

# 서비스 종료
docker compose --profile mock down
```

### Real 환경 Smoke 테스트 (Phase 9)

```bash
# 환경변수 설정 (실제 서비스 URL)
export RAGFLOW_BASE_URL_REAL=http://your-ragflow:8080
export LLM_BASE_URL_REAL=http://your-llm:8001
export BACKEND_BASE_URL_REAL=http://your-backend:8080

# Real 모드로 AI Gateway 시작
docker compose --profile real up -d

# Real 환경 smoke 테스트 실행
pytest -m real_integration -v

# 서비스 종료
docker compose --profile real down
```

**주의**: Real 테스트는 실제 LLM API를 호출하므로 비용이 발생할 수 있습니다.

## Docker 빌드 및 실행

### 이미지 빌드

```bash
docker build -t ctrlf-ai:0.1.0 .
```

### 컨테이너 실행

```bash
# 기본 실행
docker run -p 8000:8000 ctrlf-ai:0.1.0

# 환경변수 파일 사용
docker run -p 8000:8000 --env-file .env ctrlf-ai:0.1.0

# 백그라운드 실행
docker run -d -p 8000:8000 --name ctrlf-ai --env-file .env ctrlf-ai:0.1.0

# 로그 확인
docker logs -f ctrlf-ai

# 컨테이너 중지 및 삭제
docker stop ctrlf-ai && docker rm ctrlf-ai
```

### 헬스체크 확인 (Docker)

```bash
curl http://localhost:8000/health
```

## 환경변수

### 기본 설정

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `APP_NAME` | 애플리케이션 이름 | `ctrlf-ai-gateway` |
| `APP_ENV` | 실행 환경 (local/dev/prod) | `local` |
| `APP_VERSION` | 버전 | `0.1.0` |
| `LOG_LEVEL` | 로그 레벨 | `INFO` |
| `CORS_ORIGINS` | 허용할 Origin (쉼표 구분) | `*` |

### Mock/Real 모드 설정 (Phase 9)

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `AI_ENV` | 서비스 모드 (`mock` 또는 `real`) | `mock` |
| `RAGFLOW_BASE_URL` | RAGFlow URL (직접 지정, 최우선) | - |
| `RAGFLOW_BASE_URL_MOCK` | Mock 모드용 RAGFlow URL | `http://ragflow:8080` |
| `RAGFLOW_BASE_URL_REAL` | Real 모드용 RAGFlow URL | - |
| `LLM_BASE_URL` | LLM URL (직접 지정, 최우선) | - |
| `LLM_BASE_URL_MOCK` | Mock 모드용 LLM URL | `http://llm-internal:8001` |
| `LLM_BASE_URL_REAL` | Real 모드용 LLM URL | - |
| `BACKEND_BASE_URL` | Backend URL (직접 지정, 최우선) | - |
| `BACKEND_BASE_URL_MOCK` | Mock 모드용 Backend URL | `http://backend-mock:8081` |
| `BACKEND_BASE_URL_REAL` | Real 모드용 Backend URL | - |
| `BACKEND_API_TOKEN` | Backend API 인증 토큰 | - |

### PII 마스킹 설정

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `PII_BASE_URL` | PII 마스킹 서비스 URL (선택) | - |
| `PII_ENABLED` | PII 마스킹 활성화 여부 | `true` |

### URL 선택 우선순위

1. `*_BASE_URL` (직접 지정) - 설정되어 있으면 최우선
2. `AI_ENV=real` → `*_BASE_URL_REAL` 사용
3. `AI_ENV=mock` (기본값) → `*_BASE_URL_MOCK` 사용

## API 엔드포인트

### Health Check API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | Liveness 체크 |
| GET | `/health/ready` | Readiness 체크 |

### AI API (현재 더미 응답)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/ai/chat/messages` | AI 채팅 응답 생성 |
| POST | `/ai/rag/process` | RAG 문서 처리 요청 |

---

## AI API 상세

### POST /ai/chat/messages

백엔드(ctrlf-back)에서 사용자의 질문과 대화 히스토리를 전달하면, AI 게이트웨이가 답변을 생성하여 반환합니다.

**현재 상태**: 더미 응답 반환 (RAG/LLM 연동은 다음 단계에서 구현)

**Request Body:**
```json
{
  "session_id": "chat-session-123",
  "user_id": "employee-456",
  "user_role": "EMPLOYEE",
  "department": "HR",
  "domain": "POLICY",
  "channel": "WEB",
  "messages": [
    {"role": "user", "content": "연차 이월 규정이 어떻게 되나요?"}
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| session_id | string | O | 채팅 세션 ID |
| user_id | string | O | 사용자 ID |
| user_role | string | O | 사용자 역할 (EMPLOYEE, MANAGER, ADMIN 등) |
| department | string | X | 사용자 소속 부서 |
| domain | string | X | 질문 도메인 (POLICY, INCIDENT, EDUCATION 등) |
| channel | string | X | 요청 채널 (기본값: WEB) |
| messages | array | O | 대화 히스토리 (role: user/assistant/system) |

**Response:**
```json
{
  "answer": "연차 이월 규정은...",
  "sources": [
    {
      "doc_id": "HR-001",
      "title": "인사규정",
      "page": 15,
      "score": 0.95
    }
  ],
  "meta": {
    "used_model": "gpt-4",
    "route": "ROUTE_RAG_INTERNAL",
    "masked": true,
    "latency_ms": 1500
  }
}
```

**테스트:**
```bash
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session",
    "user_id": "user-123",
    "user_role": "EMPLOYEE",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

---

### POST /ai/rag/process

백엔드에서 RAG용 문서를 전달하면, AI 게이트웨이가 RAGFlow를 통해 전처리/임베딩을 수행합니다.

**현재 상태**: 더미 응답 반환 (RAGFlow 연동은 다음 단계에서 구현)

**Request Body:**
```json
{
  "doc_id": "HR-001",
  "file_url": "https://storage.example.com/docs/hr-policy.pdf",
  "domain": "POLICY",
  "acl": {
    "roles": ["EMPLOYEE", "MANAGER"],
    "departments": ["ALL"]
  }
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| doc_id | string | O | 문서 ID |
| file_url | string (URL) | O | 문서 파일 URL |
| domain | string | O | 문서 도메인 (POLICY, INCIDENT, EDUCATION 등) |
| acl | object | X | 접근 제어 설정 |
| acl.roles | array | X | 접근 가능한 역할 목록 |
| acl.departments | array | X | 접근 가능한 부서 목록 |

**Response:**
```json
{
  "doc_id": "HR-001",
  "success": true,
  "message": "Document successfully processed and indexed"
}
```

**테스트:**
```bash
curl -X POST http://localhost:8000/ai/rag/process \
  -H "Content-Type: application/json" \
  -d '{
    "doc_id": "HR-001",
    "file_url": "https://example.com/docs/hr-001.pdf",
    "domain": "POLICY"
  }'
```

---

## 개발 가이드

### 아키텍처 레이어

```
API Layer (app/api/v1/)
    ↓
Service Layer (app/services/)
    ↓
External Services (RAGFlow, LLM)
```

- **API Layer**: HTTP 요청/응답 처리, 라우팅
- **Service Layer**: 비즈니스 로직 (현재 더미 구현)
- **Models**: Pydantic 스키마 정의

### 새 API 라우터 추가

1. `app/models/`에 Pydantic 모델 정의
2. `app/services/`에 서비스 클래스 구현
3. `app/api/v1/`에 라우터 파일 생성
4. `app/api/v1/__init__.py`에 import 추가
5. `app/main.py`에서 라우터 등록
6. `tests/`에 테스트 파일 추가

### 구현 완료

- [x] PII 마스킹 로직 (Phase 4)
- [x] 의도 분류 및 라우팅 (Phase 4)
- [x] RAGFlow 연동 (Phase 3)
- [x] LLM 연동 (Phase 3)
- [x] AI 로그 백엔드 전송 (Phase 5)
- [x] Docker Compose 통합 테스트 (Phase 8)
- [x] Mock/Real 모드 전환 (Phase 9)
- [x] AI 로그 camelCase JSON (Phase 10)

## 라이선스

Private - CTRL+F Team
