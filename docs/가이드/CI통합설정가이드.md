# CI Integration Tests Setup

## 필수 GitHub Secrets 설정

Repository Settings → Secrets and variables → Actions → New repository secret

### 필수 (Required)

| Secret Name | 설명 | 예시 |
|-------------|------|------|
| `LLM_BASE_URL` | LLM 서비스 URL (OpenAI compatible) | `http://gpu-server:8000/v1` |
| `RAGFLOW_BASE_URL` | RAGFlow 서비스 URL | `http://ragflow-server:9380` |

### 선택 (Optional)

| Secret Name | 설명 | 예시 |
|-------------|------|------|
| `MILVUS_HOST` | Milvus 호스트 | `milvus-server` |
| `MILVUS_PORT` | Milvus 포트 | `19530` |
| `BACKEND_BASE_URL` | Spring Backend URL | `http://backend:8080` |
| `RAGFLOW_API_KEY` | RAGFlow API 키 | `ragflow-xxx` |

---

## 실행 시나리오

### 시나리오 A: Self-hosted Runner (서비스 외부 URL)

```
self-hosted runner → [LLM_BASE_URL] → GPU 서버
                   → [RAGFLOW_BASE_URL] → RAGFlow 서버
                   → [MILVUS_HOST:PORT] → Milvus 서버
```

- 모든 서비스가 이미 실행 중이어야 함
- GitHub Secrets에 실제 서비스 URL 등록
- `skip_service_setup: true`로 실행

### 시나리오 B: Self-hosted Runner + Docker Compose

```
self-hosted runner → docker compose up (Milvus)
                   → [LLM_BASE_URL] → 외부 GPU 서버
                   → [RAGFLOW_BASE_URL] → 외부 RAGFlow 서버
```

- Milvus만 CI에서 띄움 (docker-compose.ci.yml)
- LLM/RAGFlow는 외부 URL 사용

---

## 수동 실행 방법

GitHub Actions → Integration Tests → Run workflow

- `skip_service_setup`:
  - `false` (기본): docker compose로 Milvus 기동
  - `true`: 서비스가 이미 실행 중 (외부 URL만 사용)

---

## 로컬 E2E Smoke 테스트 실행

### 테스트 항목

| 테스트 | 설명 | 필수 환경변수 |
|--------|------|---------------|
| `test_llm_service_reachable` | LLM 서비스 연결 확인 | `LLM_BASE_URL` |
| `test_chat_api_responds` | FastAPI 앱 헬스체크 | - |
| `test_llm_generates_response` | LLM 응답 생성 확인 | `LLM_BASE_URL` |

### 실행 방법

#### 방법 1: .env 파일 사용 (권장)

```bash
# 1. .env에 필요한 환경변수 설정
# LLM_BASE_URL=http://your-llm-server:8001

# 2. .env 로드 후 테스트 실행
set -a && source .env && set +a
pytest tests/integration/ -v
```

#### 방법 2: 환경변수 직접 지정

```bash
# Linux/Mac
LLM_BASE_URL=http://localhost:8001 pytest tests/integration/ -v

# Windows PowerShell
$env:LLM_BASE_URL="http://localhost:8001"; pytest tests/integration/ -v

# Windows CMD
set LLM_BASE_URL=http://localhost:8001 && pytest tests/integration/ -v
```

### 선택적 환경변수

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `MILVUS_HOST` | - | Milvus 연결 시 필요 |
| `MILVUS_PORT` | `19530` | Milvus 포트 |
| `BACKEND_BASE_URL` | - | Spring 백엔드 URL |

### 로컬 vs CI 동작 차이

| 환경 | 필수 환경변수 누락 시 |
|------|---------------------|
| **로컬** | 테스트 **skip** (편의성) |
| **CI** (`GITHUB_ACTIONS=true`) | 즉시 **fail** (strict) |

### 예상 출력

```
tests/integration/test_e2e_smoke.py::TestServiceHealth::test_llm_service_reachable PASSED
tests/integration/test_e2e_smoke.py::TestE2ESmoke::test_chat_api_responds PASSED
tests/integration/test_e2e_smoke.py::TestE2ESmoke::test_llm_generates_response PASSED

3 passed in 2.50s
```

환경변수 미설정 시:
```
3 skipped (local): missing env vars: LLM_BASE_URL (LLM 서비스 URL)
```

---

## 트러블슈팅

### 1. "Missing required env vars" 에러

```
🚨 FATAL: Missing required env vars: LLM_BASE_URL RAGFLOW_BASE_URL
```

**해결**: GitHub Secrets 설정 확인
1. Repository Settings → Secrets and variables → Actions
2. `LLM_BASE_URL`, `RAGFLOW_BASE_URL` 추가

### 2. "Cannot connect to service" 에러

```
Cannot connect to LLM service at http://...
```

**해결**:
- 서비스가 실행 중인지 확인
- Self-hosted runner에서 해당 URL에 접근 가능한지 확인
- 방화벽/네트워크 설정 확인

### 3. "All tests skipped" 상황

CI에서는 발생하면 안 됨 (strict fail).
로컬에서만 skip 허용.

---

## Phase 42 관련 변경사항

### 변경 내역

| 커밋 | 내용 |
|------|------|
| `5f6a79d` | Direct Milvus 인덱싱 제거 |
| `700178d` | MilvusSearchClient 읽기 전용 변환 |
| `bc0bcda` | KB 인덱싱 서비스 제거 |

### 영향

- AI 서버에서 `upsert_chunks`, `delete_chunks` 제거됨
- 인덱싱은 RAGFlow가 담당하는 전제
- `/internal/rag/*` 엔드포인트 Deprecated (410 Gone)

### 롤백 필요 시

```bash
# Phase 42 커밋 4개 revert
git revert --no-commit 5f6a79d 700178d 3afc150 bc0bcda
git commit -m "revert: Phase 42 롤백 (Direct Milvus 인덱싱 복구)"
```

---

## 팀 공지 템플릿

```
[AI 서버] Phase 42 변경 공지

main에 Phase 42 커밋으로 AI 서버의 Direct Milvus upsert/delete 인덱싱 코드가 제거됐습니다.

- 변경 내역: DocumentProcessor, IndexingService, JobService 삭제
- 전제: 인덱싱은 RAGFlow가 담당
- 영향: /internal/rag/* 엔드포인트 Deprecated (410 Gone)

우리 결정대로 인덱싱은 RAGFlow가 맡는 전제로 통합 테스트/연동을 맞춰야 합니다.
만약 아직 direct 인덱싱이 필요했다면 revert로 되돌릴게요.

관련 커밋: 5f6a79d, 700178d, 3afc150, bc0bcda
```
