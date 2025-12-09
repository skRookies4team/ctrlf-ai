# Phase 8: Docker Compose Integration Testing Report

## Overview

Phase 8 implements a complete Docker Compose environment for integration testing, including mock services for RAGFlow, LLM, and Backend APIs.

**Goal**: Run real HTTP requests against containerized services to validate the entire pipeline:
- AI Gateway (ctrlf-ai)
- RAGFlow (Mock)
- Internal LLM (Mock)
- Backend AI Log API (Mock)

## Changes Summary

### 1. Docker Compose Configuration (`docker-compose.yml`)

```yaml
services:
  ai-gateway:     # Port 8000 - Main AI Gateway
  ragflow:        # Port 8080 - Mock RAG Search
  llm-internal:   # Port 8001 - Mock LLM API
  backend-mock:   # Port 8081 - Mock AI Log API

networks:
  ctrlf-net:      # Shared bridge network
```

#### Service Dependencies
```
ai-gateway
    ├── depends_on: ragflow (healthy)
    ├── depends_on: llm-internal (healthy)
    └── depends_on: backend-mock (healthy)
```

#### Environment Variables
| Variable | Value | Description |
|----------|-------|-------------|
| `RAGFLOW_BASE_URL` | `http://ragflow:8080` | RAG search service |
| `LLM_BASE_URL` | `http://llm-internal:8001` | Internal LLM API |
| `BACKEND_BASE_URL` | `http://backend-mock:8081` | AI Log collection |
| `PII_ENABLED` | `true` | Enable PII masking |

### 2. Mock RAGFlow Server (`mock_ragflow/`)

#### Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/search` | RAG document search |
| `GET` | `/health` | Health check |
| `GET` | `/stats` | Call statistics (for testing) |
| `POST` | `/stats/reset` | Reset statistics |

#### Search Logic
- Returns POLICY documents for queries containing: "annual leave", "vacation", "policy"
- Returns empty results for other queries (fallback test)
- Supports `dataset` parameter for domain filtering

#### Sample Response
```json
{
  "results": [
    {
      "doc_id": "HR-001",
      "title": "Annual Leave Management Policy",
      "page": 12,
      "score": 0.92,
      "snippet": "Annual leave carryover cannot exceed 10 days..."
    }
  ]
}
```

### 3. Mock LLM Server (`mock_llm/`)

#### Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat completion |
| `GET` | `/v1/models` | List available models |
| `GET` | `/health` | Health check |
| `GET` | `/stats` | Call statistics |
| `POST` | `/stats/reset` | Reset statistics |

#### Response Generation Logic
- Generates context-aware responses based on user query keywords
- Includes RAG context when provided in system message
- Returns deterministic responses for testing

### 4. Mock Backend Server (`mock_backend/`)

#### Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/ai-logs` | Receive AI log entries |
| `GET` | `/api/ai-logs` | Retrieve stored logs |
| `GET` | `/health` | Health check |
| `GET` | `/stats` | Call statistics |
| `POST` | `/stats/reset` | Reset statistics |

#### Log Storage
- Stores all received logs in memory
- Logs can be retrieved for test verification
- Warns if raw PII data is detected

### 5. Integration Tests (`tests/integration/test_docker_e2e.py`)

#### Test Scenarios

| # | Scenario | Validation |
|---|----------|------------|
| 1 | POLICY + RAG + LLM + PII + Log | Full happy path |
| 2 | LLM-only route (general question) | RAG skipped |
| 3 | POLICY with no RAG results | Fallback handling |
| 4 | Response schema completeness | All fields present |
| 5 | All services healthy | Health check validation |

#### Test Configuration
```python
# pytest.ini
markers =
    integration: Docker Compose integration tests

addopts = -m "not integration"  # Exclude by default
```

## Test Results

### Unit Tests (Excluding Integration)
```
$ pytest --tb=short -q
87 passed, 5 deselected in 3.56s
```

### Integration Tests (Requires Docker)
```
$ docker compose up -d
$ pytest -m integration -v

# Expected output (when Docker is running):
# 5 passed
```

## File Structure

```
ctrlf-ai/
├── docker-compose.yml          # Docker Compose configuration
├── Dockerfile                  # AI Gateway Dockerfile (existing)
├── mock_ragflow/
│   ├── Dockerfile             # Mock RAGFlow container
│   └── main.py                # FastAPI mock server
├── mock_llm/
│   ├── Dockerfile             # Mock LLM container
│   └── main.py                # OpenAI-compatible mock
├── mock_backend/
│   ├── Dockerfile             # Mock Backend container
│   └── main.py                # AI Log collection mock
├── tests/
│   └── integration/
│       ├── __init__.py
│       └── test_docker_e2e.py # Integration tests
└── pytest.ini                  # Updated with integration marker
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Docker Compose Network (ctrlf-net)                  │
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
                    │    Integration Tests        │
                    │    (pytest -m integration)  │
                    │                             │
                    │  - HTTP requests to :8000   │
                    │  - Verify mock stats        │
                    │  - Check PII masking        │
                    │  - Validate response schema │
                    └─────────────────────────────┘
```

## How to Run

### 1. Start Services
```bash
cd ctrlf-ai
docker compose up -d

# Check service status
docker compose ps

# View logs
docker compose logs -f
```

### 2. Run Integration Tests
```bash
# Run only integration tests
pytest -m integration -v

# Run all tests (including integration)
pytest --ignore-glob='**/integration/*' -v && pytest -m integration -v
```

### 3. Stop Services
```bash
docker compose down
```

### 4. Manual API Testing
```bash
# Health checks
curl http://localhost:8000/health
curl http://localhost:8080/health
curl http://localhost:8001/health
curl http://localhost:8081/health

# Chat request
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "user_id": "emp-123",
    "user_role": "EMPLOYEE",
    "domain": "POLICY",
    "messages": [{"role": "user", "content": "Tell me about annual leave policy"}]
  }'

# Check mock server stats
curl http://localhost:8080/stats  # RAGFlow
curl http://localhost:8001/stats  # LLM
curl http://localhost:8081/stats  # Backend
```

## Next Steps (Phase 9 Candidates)

1. **Real Service Integration**: Replace mocks with actual RAGFlow/LLM services
2. **CI/CD Pipeline**: Add GitHub Actions workflow for integration tests
3. **Performance Testing**: Load testing with locust or k6
4. **Multi-Domain Testing**: INCIDENT, EDUCATION domain scenarios
5. **Streaming Response**: SSE-based streaming integration test

---

**Created**: 2025-12-09
**Author**: Claude Opus 4.5 (AI Assistant)
