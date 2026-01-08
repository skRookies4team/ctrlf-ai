# ctrlf-ai-gateway 개발 진행 보고서

**작성일**: 2025년 12월 8일
**프로젝트명**: CTRL+F AI Gateway Service
**버전**: 0.1.0 (초기 스켈레톤)
**작성자**: AI 개발 어시스턴트

---

## 1. 프로젝트 개요

### 1.1 목적
CTRL+F 프로젝트의 AI 기능을 담당하는 게이트웨이 서비스의 초기 스켈레톤을 구축합니다. 이 서비스는 향후 RAGFlow, LLM 서비스와 연동하여 AI 채팅 및 RAG(Retrieval-Augmented Generation) 기능을 제공할 예정입니다.

### 1.2 연동 예정 서비스
| 서비스 | 레포지토리 | 역할 |
|--------|-----------|------|
| ctrlf-back | github.com/skRookies4team/ctrlf-back | Spring 기반 백엔드 (인증, 비즈니스 로직) |
| ctrlf-ragflow | github.com/skRookies4team/ctrlf-ragflow | RAG 처리 서비스 |
| ctrlf-front | github.com/skRookies4team/ctrlf-front | React 기반 프론트엔드 UI |

### 1.3 기술 스택
- **언어**: Python 3.12.7
- **웹 프레임워크**: FastAPI 0.124.0
- **ASGI 서버**: Uvicorn 0.38.0
- **설정 관리**: pydantic-settings 2.12.0
- **HTTP 클라이언트**: httpx 0.28.1
- **테스트**: pytest 9.0.2, pytest-anyio
- **컨테이너**: Docker (python:3.12-slim 기반)

---

## 2. 완료된 작업 목록

### 2.1 디렉터리 구조 생성

```
ctrlf-ai/
├── app/                          # 메인 애플리케이션 패키지
│   ├── __init__.py              # 패키지 초기화
│   ├── main.py                  # FastAPI 앱 진입점 (135줄)
│   ├── api/                     # API 라우터 패키지
│   │   ├── __init__.py
│   │   └── v1/                  # API 버전 1
│   │       ├── __init__.py
│   │       └── health.py        # 헬스체크 엔드포인트 (131줄)
│   └── core/                    # 핵심 유틸리티 패키지
│       ├── __init__.py
│       ├── config.py            # 설정 관리 (73줄)
│       └── logging.py           # 로깅 설정 (106줄)
├── tests/                       # 테스트 패키지
│   ├── __init__.py
│   └── test_health.py           # 헬스체크 테스트 (89줄)
├── .env.example                 # 환경변수 템플릿
├── Dockerfile                   # Docker 빌드 설정 (61줄)
├── README.md                    # 사용자 가이드 (한국어)
└── requirements.txt             # Python 의존성 목록
```

**총 코드 라인 수**: 약 600줄 (주석 및 docstring 포함)

---

## 3. 각 모듈별 상세 구현 내용

### 3.1 설정 모듈 (`app/core/config.py`)

#### 구현 내용
pydantic-settings를 활용한 타입 안전 설정 관리 시스템을 구현했습니다.

#### 주요 클래스 및 함수

**`Settings` 클래스**
```python
class Settings(BaseSettings):
    # 앱 기본 정보
    APP_NAME: str = "ctrlf-ai-gateway"
    APP_ENV: str = "local"           # local / dev / prod
    APP_VERSION: str = "0.1.0"

    # 로깅 설정
    LOG_LEVEL: str = "INFO"

    # 외부 서비스 URL
    RAGFLOW_BASE_URL: Optional[HttpUrl] = None    # RAGFlow 서비스
    LLM_BASE_URL: Optional[HttpUrl] = None        # LLM 서비스
    BACKEND_BASE_URL: Optional[HttpUrl] = None    # Spring 백엔드

    # CORS 설정
    CORS_ORIGINS: str = "*"
```

**`get_settings()` 함수**
- `@lru_cache()` 데코레이터를 사용한 싱글턴 패턴 구현
- 애플리케이션 전체에서 동일한 설정 인스턴스 공유
- 최초 호출 시에만 `.env` 파일 파싱

#### 설정 로딩 우선순위
1. 환경변수 (가장 높은 우선순위)
2. `.env` 파일
3. 클래스에 정의된 기본값

#### 특징
- `env_file_encoding="utf-8"`: 한글 포함 설정 파일 지원
- `extra="ignore"`: 정의되지 않은 환경변수 무시 (유연한 배포)
- `HttpUrl` 타입: URL 형식 자동 검증

---

### 3.2 로깅 모듈 (`app/core/logging.py`)

#### 구현 내용
Python 표준 logging 모듈을 활용한 통합 로깅 시스템을 구현했습니다.

#### 주요 함수

**`setup_logging(settings)` 함수**
- 앱 시작 시 한 번 호출하여 로깅 초기화
- uvicorn 로거와의 충돌 방지 로직 포함

**로그 포맷**
```
2025-12-08 14:30:00 | INFO     | app.main | Starting ctrlf-ai-gateway v0.1.0
```
- 시간 (YYYY-MM-DD HH:MM:SS)
- 로그 레벨 (8자 고정폭)
- 로거 이름
- 메시지

**`get_logger(name)` 함수**
- 모듈별 로거 인스턴스 생성
- 사용 예: `logger = get_logger(__name__)`

#### uvicorn 연동
```python
uvicorn_loggers = ["uvicorn", "uvicorn.error", "uvicorn.access"]
for logger_name in uvicorn_loggers:
    uvicorn_logger = logging.getLogger(logger_name)
    uvicorn_logger.setLevel(log_level)
```
- uvicorn의 기본 로거 레벨을 설정과 동기화
- 중복 핸들러 추가 방지

---

### 3.3 FastAPI 메인 애플리케이션 (`app/main.py`)

#### 구현 내용
FastAPI 인스턴스 생성 및 미들웨어, 라우터 설정을 담당합니다.

#### 라이프사이클 관리
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 시작 시: 로깅 설정, 초기화 작업
    setup_logging(settings)
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    yield  # 애플리케이션 실행

    # 종료 시: 리소스 정리
    logger.info(f"Shutting down {settings.APP_NAME}")
```

#### FastAPI 인스턴스 설정
```python
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="CTRL+F AI Gateway 서비스...",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
```

#### CORS 미들웨어 설정
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,      # 현재 ["*"], 프로덕션에서 제한 필요
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

#### 라우터 prefix 설계 결정
현재 헬스체크는 `prefix=""`로 설정하여 `/health`로 접근 가능하도록 했습니다.

**선택 이유**:
- 쿠버네티스 livenessProbe/readinessProbe 설정 단순화
- 로드밸런서 헬스체크 경로 설정 용이

**향후 비즈니스 API**:
- `/api/v1/chat/messages`
- `/api/v1/rag/process`
- 별도 라우터로 `/api/v1` prefix 사용 예정

---

### 3.4 헬스체크 API (`app/api/v1/health.py`)

#### 구현 내용
쿠버네티스 및 로드밸런서 호환 헬스체크 엔드포인트를 구현했습니다.

#### 엔드포인트 목록

| 메서드 | 경로 | 용도 | 응답 |
|--------|------|------|------|
| GET | `/health` | Liveness Probe | `{"status": "ok", ...}` |
| GET | `/health/ready` | Readiness Probe | `{"ready": true, ...}` |

#### Liveness Check (`GET /health`)
```json
{
  "status": "ok",
  "app": "ctrlf-ai-gateway",
  "version": "0.1.0",
  "env": "local"
}
```
- 애플리케이션 프로세스가 살아있는지 확인
- 실패 시 쿠버네티스가 컨테이너 재시작

#### Readiness Check (`GET /health/ready`)
```json
{
  "ready": true,
  "checks": {}
}
```
- 트래픽을 받을 준비가 되었는지 확인
- 현재는 항상 `ready: true` 반환
- **TODO**: 외부 서비스 연결 상태 체크 로직 추가 예정

#### 확장 예정 코드 (주석으로 문서화)
```python
# RAGFlow 연결 체크
if settings.RAGFLOW_BASE_URL:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{settings.RAGFLOW_BASE_URL}/health")
            checks["ragflow"] = response.status_code == 200
    except Exception:
        checks["ragflow"] = False
```

#### Pydantic 응답 스키마
```python
class HealthResponse(BaseModel):
    status: str      # "ok" 또는 "error"
    app: str         # 애플리케이션 이름
    version: str     # 버전
    env: str         # 환경 (local/dev/prod)

class ReadinessResponse(BaseModel):
    ready: bool      # 준비 상태
    checks: dict     # 각 의존성 체크 결과
```

---

### 3.5 테스트 (`tests/test_health.py`)

#### 구현 내용
pytest와 httpx.AsyncClient를 사용한 비동기 API 테스트를 구현했습니다.

#### 테스트 케이스 목록 (5개, 모두 통과)

| 테스트 함수 | 검증 내용 | 결과 |
|-------------|----------|------|
| `test_health_check_returns_200` | `/health`가 200 OK 반환 | PASSED |
| `test_health_check_returns_status_ok` | 응답에 `status="ok"` 포함 | PASSED |
| `test_health_check_contains_app_info` | 앱 이름, 버전 정보 검증 | PASSED |
| `test_readiness_check_returns_200` | `/health/ready`가 200 OK 반환 | PASSED |
| `test_readiness_check_returns_ready_true` | `ready=True` 반환 | PASSED |

#### 테스트 픽스처
```python
@pytest.fixture
async def client() -> AsyncClient:
    """테스트용 비동기 HTTP 클라이언트"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```
- 실제 HTTP 서버 없이 FastAPI 앱 직접 테스트
- `ASGITransport`로 ASGI 앱에 직접 요청

#### 테스트 실행 결과
```
============================= test session starts =============================
platform win32 -- Python 3.12.7, pytest-9.0.2, pluggy-1.6.0
plugins: anyio-4.12.0
collected 5 items

tests/test_health.py::test_health_check_returns_200 PASSED               [ 20%]
tests/test_health.py::test_health_check_returns_status_ok PASSED         [ 40%]
tests/test_health.py::test_health_check_contains_app_info PASSED         [ 60%]
tests/test_health.py::test_readiness_check_returns_200 PASSED            [ 80%]
tests/test_health.py::test_readiness_check_returns_ready_true PASSED     [100%]

============================== 5 passed in 1.58s ==============================
```

---

### 3.6 Dockerfile

#### 구현 내용
프로덕션 배포를 위한 최적화된 Docker 이미지 설정입니다.

#### 주요 특징

**1. 경량 베이스 이미지**
```dockerfile
FROM python:3.12-slim
```
- 전체 Python 이미지 대비 약 900MB 절약

**2. 보안 강화 (비루트 유저)**
```dockerfile
RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser
USER appuser
```
- 컨테이너 탈취 시 피해 최소화

**3. 레이어 캐싱 최적화**
```dockerfile
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
```
- 코드 변경 시에도 의존성 레이어 재사용

**4. Python 최적화 환경변수**
```dockerfile
ENV PYTHONUNBUFFERED=1          # 로그 즉시 출력
ENV PYTHONDONTWRITEBYTECODE=1   # .pyc 파일 미생성
ENV PIP_NO_CACHE_DIR=1          # pip 캐시 비활성화
```

**5. 내장 헬스체크**
```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"
```
- Docker Compose, Swarm에서 자동 헬스체크 활용 가능

---

### 3.7 환경변수 템플릿 (`.env.example`)

#### 포함된 설정 항목

```bash
# 앱 기본 설정
APP_NAME=ctrlf-ai-gateway
APP_ENV=local
APP_VERSION=0.1.0
LOG_LEVEL=INFO

# 외부 서비스 연동 (향후 사용)
RAGFLOW_BASE_URL=
LLM_BASE_URL=
BACKEND_BASE_URL=

# CORS 설정
CORS_ORIGINS=*
```

---

### 3.8 README.md (사용자 가이드)

#### 포함 내용
1. 프로젝트 소개 및 연동 서비스 설명
2. 디렉터리 구조 다이어그램
3. 로컬 개발 환경 설정 가이드 (한국어)
   - 가상환경 생성 (Windows/Linux/macOS)
   - 의존성 설치
   - 서버 실행
4. 테스트 실행 방법
5. Docker 빌드/실행 방법
6. 환경변수 설명 테이블
7. API 엔드포인트 목록
8. 향후 확장 가이드

---

## 4. 의존성 목록 (`requirements.txt`)

| 패키지 | 버전 | 용도 |
|--------|------|------|
| fastapi | ≥0.115.0 | 웹 프레임워크 |
| uvicorn[standard] | ≥0.32.0 | ASGI 서버 |
| pydantic | ≥2.9.0 | 데이터 검증 |
| pydantic-settings | ≥2.6.0 | 환경변수 관리 |
| httpx | ≥0.27.0 | HTTP 클라이언트 |
| python-dotenv | ≥1.0.0 | .env 파일 로딩 |
| pytest | ≥8.3.0 | 테스트 프레임워크 |
| pytest-anyio | ≥0.0.0 | 비동기 테스트 지원 |
| anyio | ≥4.6.0 | 비동기 유틸리티 |

---

## 5. API 문서 자동 생성

FastAPI의 OpenAPI 자동 문서화 기능이 활성화되어 있습니다.

| 경로 | 설명 |
|------|------|
| `/docs` | Swagger UI (인터랙티브 API 테스트) |
| `/redoc` | ReDoc (정적 API 문서) |
| `/openapi.json` | OpenAPI 3.0 스펙 JSON |

---

## 6. 향후 개발 예정 항목 (TODO)

### 6.1 비즈니스 API 추가
```
POST /api/v1/chat/messages     # AI 채팅 메시지 전송
POST /api/v1/rag/process       # RAG 처리 요청
GET  /api/v1/rag/documents     # 문서 목록 조회
```

### 6.2 외부 서비스 연동
- [ ] RAGFlow 서비스 클라이언트 구현
- [ ] LLM API 클라이언트 구현 (OpenAI 호환)
- [ ] Spring 백엔드 인증 연동

### 6.3 Readiness Check 확장
- [ ] RAGFlow 연결 상태 체크
- [ ] LLM 서비스 연결 상태 체크
- [ ] Backend 서비스 연결 상태 체크

### 6.4 인프라 설정
- [ ] CORS origins 환경별 분리
- [ ] Kubernetes 매니페스트 작성
- [ ] CI/CD 파이프라인 구성

---

## 7. 실행 방법 요약

### 로컬 개발 환경
```bash
# 1. 가상환경 생성 및 활성화
python -m venv .venv
source .venv/Scripts/activate  # Windows Git Bash

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 환경변수 설정
cp .env.example .env

# 4. 서버 실행
uvicorn app.main:app --reload

# 5. 테스트 실행
pytest -v

# 6. 헬스체크 확인
curl http://localhost:8000/health
```

### Docker 환경
```bash
# 빌드
docker build -t ctrlf-ai:0.1.0 .

# 실행
docker run -p 8000:8000 --env-file .env ctrlf-ai:0.1.0
```

---

## 8. 결론

ctrlf-ai-gateway의 초기 스켈레톤 구축이 완료되었습니다. 현재 상태에서는:

- **완료**: 기본 프로젝트 구조, 설정 관리, 로깅, 헬스체크 API, 테스트, Docker 지원
- **대기**: RAG/LLM 비즈니스 로직, 외부 서비스 연동

이 스켈레톤을 기반으로 향후 ctrlf-ragflow, ctrlf-back, ctrlf-front와의 연동 작업을 진행할 수 있습니다.

---

**보고서 끝**
