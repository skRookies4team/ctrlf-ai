# Phase 8: Docker Compose 통합 테스트 환경 구축 보고서

## 개요

Phase 8에서는 RAGFlow, LLM, Backend API의 Mock 서비스를 포함한 완전한 Docker Compose 통합 테스트 환경을 구현했습니다.

**목표**: 컨테이너화된 서비스에 실제 HTTP 요청을 보내 전체 파이프라인을 검증
- AI Gateway (ctrlf-ai)
- RAGFlow (Mock)
- 내부 LLM (Mock)
- Backend AI Log API (Mock)

## 변경 사항 요약

### 1. Docker Compose 설정 (`docker-compose.yml`)

```yaml
services:
  ai-gateway:     # 포트 8000 - 메인 AI Gateway
  ragflow:        # 포트 8080 - Mock RAG 검색
  llm-internal:   # 포트 8001 - Mock LLM API
  backend-mock:   # 포트 8081 - Mock AI 로그 API

networks:
  ctrlf-net:      # 공유 브리지 네트워크
```

#### 서비스 의존성
```
ai-gateway
    ├── depends_on: ragflow (healthy)
    ├── depends_on: llm-internal (healthy)
    └── depends_on: backend-mock (healthy)
```

#### 환경변수
| 변수명 | 값 | 설명 |
|--------|-----|------|
| `RAGFLOW_BASE_URL` | `http://ragflow:8080` | RAG 검색 서비스 |
| `LLM_BASE_URL` | `http://llm-internal:8001` | 내부 LLM API |
| `BACKEND_BASE_URL` | `http://backend-mock:8081` | AI 로그 수집 |
| `PII_ENABLED` | `true` | PII 마스킹 활성화 |

### 2. Mock RAGFlow 서버 (`mock_ragflow/`)

#### 엔드포인트
| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/search` | RAG 문서 검색 |
| `GET` | `/health` | 헬스체크 |
| `GET` | `/stats` | 호출 통계 (테스트용) |
| `POST` | `/stats/reset` | 통계 초기화 |

#### 검색 로직
- "연차", "휴가", "규정" 키워드 포함 시 POLICY 문서 반환
- 그 외 쿼리는 빈 결과 반환 (fallback 테스트용)
- `dataset` 파라미터로 도메인 필터링 지원

#### 응답 예시
```json
{
  "results": [
    {
      "doc_id": "HR-001",
      "title": "연차휴가 관리 규정",
      "page": 12,
      "score": 0.92,
      "snippet": "연차휴가의 이월은 최대 10일을 초과할 수 없으며..."
    }
  ]
}
```

### 3. Mock LLM 서버 (`mock_llm/`)

#### 엔드포인트
| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/v1/chat/completions` | OpenAI 호환 채팅 완성 |
| `GET` | `/v1/models` | 사용 가능한 모델 목록 |
| `GET` | `/health` | 헬스체크 |
| `GET` | `/stats` | 호출 통계 |
| `POST` | `/stats/reset` | 통계 초기화 |

#### 응답 생성 로직
- 사용자 쿼리 키워드에 따라 컨텍스트 인식 응답 생성
- 시스템 메시지에 RAG 컨텍스트가 있으면 문서 기반 응답
- 테스트를 위한 결정적(deterministic) 응답 반환

### 4. Mock Backend 서버 (`mock_backend/`)

#### 엔드포인트
| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/ai-logs` | AI 로그 엔트리 수신 |
| `GET` | `/api/ai-logs` | 저장된 로그 조회 |
| `GET` | `/health` | 헬스체크 |
| `GET` | `/stats` | 호출 통계 |
| `POST` | `/stats/reset` | 통계 초기화 |

#### 로그 저장
- 모든 수신된 로그를 메모리에 저장
- 테스트 검증을 위해 로그 조회 가능
- 원본 PII 데이터 감지 시 경고 출력

### 5. 통합 테스트 (`tests/integration/test_docker_e2e.py`)

#### 테스트 시나리오

| # | 시나리오 | 검증 항목 |
|---|----------|----------|
| 1 | POLICY + RAG + LLM + PII + 로그 | 전체 해피패스 |
| 2 | LLM-only 라우트 (일반 질문) | RAG 스킵 |
| 3 | POLICY + RAG 결과 없음 | Fallback 처리 |
| 4 | 응답 스키마 완전성 | 모든 필드 존재 확인 |
| 5 | 모든 서비스 헬스체크 | 헬스체크 검증 |

#### 테스트 설정
```python
# pytest.ini
markers =
    integration: Docker Compose 통합 테스트

addopts = -m "not integration"  # 기본적으로 제외
```

## 테스트 결과

### 단위 테스트 (통합 테스트 제외)
```
$ pytest --tb=short -q
87 passed, 5 deselected in 3.56s
```

### 통합 테스트 (Docker 필요)
```
$ docker compose up -d
$ pytest -m integration -v

# 예상 출력 (Docker 실행 중):
# 5 passed
```

## 파일 구조

```
ctrlf-ai/
├── docker-compose.yml          # Docker Compose 설정
├── Dockerfile                  # AI Gateway Dockerfile (기존)
├── mock_ragflow/
│   ├── Dockerfile             # Mock RAGFlow 컨테이너
│   └── main.py                # FastAPI Mock 서버
├── mock_llm/
│   ├── Dockerfile             # Mock LLM 컨테이너
│   └── main.py                # OpenAI 호환 Mock
├── mock_backend/
│   ├── Dockerfile             # Mock Backend 컨테이너
│   └── main.py                # AI 로그 수집 Mock
├── tests/
│   └── integration/
│       ├── __init__.py
│       └── test_docker_e2e.py # 통합 테스트
└── pytest.ini                  # integration 마커 추가
```

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Docker Compose 네트워크 (ctrlf-net)                    │
│                                                                             │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐        │
│  │  Mock RAGFlow   │    │   Mock LLM      │    │  Mock Backend   │        │
│  │  :8080          │    │   :8001         │    │  :8081          │        │
│  │                 │    │                 │    │                 │        │
│  │ POST /search    │    │ POST /v1/chat/  │    │ POST /api/      │        │
│  │ GET /health     │    │   completions   │    │   ai-logs       │        │
│  │ GET /stats      │    │ GET /health     │    │ GET /health     │        │
│  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘        │
│           │                      │                      │                  │
│           └──────────────────────┼──────────────────────┘                  │
│                                  │                                          │
│                      ┌───────────▼───────────┐                             │
│                      │    AI Gateway         │                             │
│                      │    :8000              │                             │
│                      │                       │                             │
│                      │ POST /ai/chat/messages│                             │
│                      │ GET /health           │                             │
│                      └───────────┬───────────┘                             │
│                                  │                                          │
└──────────────────────────────────│──────────────────────────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │       통합 테스트           │
                    │  (pytest -m integration)   │
                    │                             │
                    │  - :8000으로 HTTP 요청      │
                    │  - Mock 통계 검증           │
                    │  - PII 마스킹 확인          │
                    │  - 응답 스키마 검증         │
                    └─────────────────────────────┘
```

## 실행 방법

### 1. 서비스 시작
```bash
cd ctrlf-ai
docker compose up -d

# 서비스 상태 확인
docker compose ps

# 로그 보기
docker compose logs -f
```

### 2. 통합 테스트 실행
```bash
# 통합 테스트만 실행
pytest -m integration -v

# 모든 테스트 실행 (통합 테스트 포함)
pytest --ignore-glob='**/integration/*' -v && pytest -m integration -v
```

### 3. 서비스 종료
```bash
docker compose down
```

### 4. 수동 API 테스트
```bash
# 헬스체크
curl http://localhost:8000/health
curl http://localhost:8080/health
curl http://localhost:8001/health
curl http://localhost:8081/health

# 채팅 요청
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "user_id": "emp-123",
    "user_role": "EMPLOYEE",
    "domain": "POLICY",
    "messages": [{"role": "user", "content": "연차휴가 규정 알려줘"}]
  }'

# Mock 서버 통계 확인
curl http://localhost:8080/stats  # RAGFlow
curl http://localhost:8001/stats  # LLM
curl http://localhost:8081/stats  # Backend
```

## 다음 단계 (Phase 9 후보)

1. **실제 서비스 연동**: Mock을 실제 RAGFlow/LLM 서비스로 교체
2. **CI/CD 파이프라인**: GitHub Actions 워크플로우 추가
3. **성능 테스트**: locust 또는 k6로 부하 테스트
4. **다중 도메인 테스트**: INCIDENT, EDUCATION 도메인 시나리오
5. **스트리밍 응답**: SSE 기반 스트리밍 통합 테스트

---

**작성일**: 2025-12-09
**작성자**: Claude Opus 4.5 (AI Assistant)
