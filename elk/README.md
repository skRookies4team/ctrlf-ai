# ELK Stack for ctrlf-ai

ctrlf-ai(AI Gateway) 로그 중앙화를 위한 ELK 스택 설정.
운영 환경에서 로그 추적/장애 분석을 위해 사용합니다.

## 구성 요소

| 서비스 | 역할 | 포트 |
|--------|------|------|
| **Elasticsearch** | 로그 저장/검색 엔진 | 9200 |
| **Kibana** | 로그 시각화/조회 UI | 5601 |
| **Fluent Bit** | 로그 수집기 (Docker → ES) | - |

## 배포 프로필 (mock vs real)

ctrlf-ai는 두 가지 프로필을 지원합니다:

| 프로필 | 용도 | 연결 대상 |
|--------|------|-----------|
| `mock` | 로컬 개발/테스트 | Mock RAGFlow, Mock LLM, Mock Backend |
| `real` | 프로덕션/스테이징 | 실제 RAGFlow, vLLM, Spring Backend |

```bash
# 로컬 개발 (Mock 서비스 사용)
docker compose -f docker-compose.yml -f elk/docker-compose.elk.yml --profile mock up -d

# 프로덕션 (실제 서비스 연결)
docker compose -f docker-compose.yml -f elk/docker-compose.elk.yml --profile real up -d
```

## 사전 요구사항

- **Docker Desktop** 실행 중 (Windows/macOS) 또는 Docker Engine (Linux)
- **RAM**: 최소 4GB
- **Ports**: 9200, 5601 사용 가능

```bash
# Docker 실행 확인
docker info
```

## Quick Start (로컬 개발)

```bash
# 1. 네트워크 생성 (최초 1회)
docker network create ctrlf-network

# 2. ELK + AI Gateway 시작 (Mock 모드)
docker compose -f docker-compose.yml -f elk/docker-compose.elk.yml --profile mock up -d

# 3. ES 초기 설정 (템플릿 + ILM, 최초 1회)
./elk/setup-elasticsearch.sh

# 4. Kibana 접속
open http://localhost:5601
```

## 프로덕션 배포 (EC2)

```bash
# 환경변수 설정
export RAGFLOW_BASE_URL_REAL=http://ragflow-server:8080
export LLM_BASE_URL_REAL=http://vllm-server:8001
export BACKEND_BASE_URL_REAL=http://spring-backend:8080

# ELK + AI Gateway 시작 (Real 모드)
docker compose -f docker-compose.yml -f elk/docker-compose.elk.yml --profile real up -d

# ES 초기 설정 (최초 1회)
./elk/setup-elasticsearch.sh
```

## 아키텍처

```
┌─────────────────┐     stdout      ┌─────────────┐
│  AI Gateway     │ ──────────────► │  Docker     │
│  (JSON 1-line)  │                 │  Log Driver │
└─────────────────┘                 └──────┬──────┘
                                           │
                                           ▼
                                    ┌─────────────┐
                                    │  Fluent Bit │
                                    │  (Parser)   │
                                    └──────┬──────┘
                                           │
                                           ▼
                                    ┌─────────────┐
                                    │Elasticsearch│
                                    │ctrlf-ai-*   │
                                    └──────┬──────┘
                                           │
                                           ▼
                                    ┌─────────────┐
                                    │   Kibana    │
                                    │   :5601     │
                                    └─────────────┘
```

## 로그 필드 (ES 매핑)

| 필드 | 타입 | 설명 |
|------|------|------|
| `@timestamp` | date | 로그 발생 시간 |
| `level` | keyword | INFO, WARNING, ERROR |
| `logger` | keyword | app.services.chat, app.clients.llm_client 등 |
| `message` | text | 로그 메시지 |
| `trace_id` | keyword | 요청 추적 ID (Kibana 필터용) |
| `user_id` | keyword | 사용자 ID |
| `conversation_id` | keyword | 대화 ID |
| `turn_id` | long | 대화 턴 번호 |

## Kibana 검색 예시 (KQL)

```bash
# 1. trace_id로 요청 흐름 추적
trace_id: "abc123-def456"

# 2. ERROR 로그만 조회
level: "ERROR"

# 3. 특정 사용자 로그
user_id: "emp12345"

# 4. 채팅 서비스 에러
level: "ERROR" AND logger: "app.services.chat*"

# 5. 특정 대화 전체 로그
conversation_id: "conv-abc123"
```

## Data View 설정 (Kibana)

1. Kibana 접속: http://localhost:5601
2. **Stack Management** → **Data Views**
3. **Create data view**
   - Name: `ctrlf-ai`
   - Index pattern: `ctrlf-ai-*`
   - Timestamp field: `@timestamp`
4. **Discover**에서 로그 조회

## 로그 보존 정책 (ILM)

| Phase | 기간 | 동작 |
|-------|------|------|
| Hot | 1일 또는 10GB | 활성 쓰기 |
| Warm | 7일 후 | 샤드 축소 + 병합 |
| Delete | 30일 후 | 자동 삭제 |

## 운영 명령어

```bash
# 컨테이너 상태 확인
docker ps --filter "name=ctrlf"

# ES 클러스터 상태
curl http://localhost:9200/_cluster/health?pretty

# 인덱스 목록
curl http://localhost:9200/_cat/indices/ctrlf-ai-*?v

# Fluent Bit 로그 (수집 문제 확인)
docker logs ctrlf-fluent-bit

# 전체 중지
docker compose -f docker-compose.yml -f elk/docker-compose.elk.yml --profile mock down
```

## 트러블슈팅

### 로그가 안 쌓일 때

```bash
# 1. Fluent Bit 상태 확인
docker logs ctrlf-fluent-bit

# 2. ES 연결 확인
docker exec ctrlf-fluent-bit curl -s elasticsearch:9200/_cluster/health

# 3. AI Gateway가 Docker로 실행 중인지 확인
docker ps | grep ctrlf-ai-gateway
```

### Docker 관련 오류 (Windows)

**Docker daemon 미실행:**
```
error during connect: ... open //./pipe/docker_engine: The system cannot find the file specified.
```
→ Docker Desktop 실행 후 1-2분 대기

**WSL2 오류:**
```
WSL 2 installation is incomplete
```
→ PowerShell(관리자)에서 `wsl --install` 실행 후 재시작

## 파일 구조

```
elk/
├── docker-compose.elk.yml      # ES + Kibana + Fluent Bit
├── setup-elasticsearch.sh      # 초기 설정 스크립트 (ILM + 템플릿)
├── fluent-bit/
│   ├── fluent-bit.conf         # 로그 수집 설정
│   └── parsers.conf            # JSON 파서
└── elasticsearch/
    ├── index-template.json     # 필드 매핑
    └── ilm-policy.json         # 보존 정책
```
