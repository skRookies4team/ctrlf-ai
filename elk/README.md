# ELK Stack for ctrlf-ai

ctrlf-ai 로그 중앙화를 위한 ELK (Elasticsearch + Kibana + Fluent Bit) 설정.

## 사전 요구사항

### Docker Desktop 설치 및 실행

ELK 스택을 실행하려면 Docker Desktop이 **실행 중**이어야 합니다.

**Windows:**
1. [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) 설치
2. 시작 메뉴에서 "Docker Desktop" 검색 후 실행
3. 트레이 아이콘이 "Docker Desktop is running" 상태가 될 때까지 대기 (1-2분)
4. 실행 확인:
   ```bash
   docker info
   ```

**macOS:**
1. [Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) 설치
2. Applications에서 Docker 실행
3. 메뉴바 아이콘이 안정화될 때까지 대기

**Linux:**
```bash
# Docker Engine 설치 후 서비스 시작
sudo systemctl start docker
sudo systemctl enable docker
```

### 시스템 요구사항

- **RAM**: 최소 4GB (ES 512MB + Kibana + Fluent Bit)
- **Disk**: 최소 10GB (ES 데이터 + Docker 이미지)
- **Ports**: 9200 (ES), 5601 (Kibana) 사용 가능해야 함

## Quick Start

```bash
# 1. 네트워크 생성 (최초 1회)
docker network create ctrlf-network

# 2. ELK 스택 시작
docker compose -f elk/docker-compose.elk.yml up -d

# 3. ES 초기 설정 (템플릿 + ILM)
chmod +x elk/setup-elasticsearch.sh
./elk/setup-elasticsearch.sh

# 4. AI Gateway와 함께 실행
docker compose -f docker-compose.yml --profile mock up -d

# 5. Kibana 접속
open http://localhost:5601
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

## 필드 매핑

| 필드 | ES 타입 | 설명 |
|------|---------|------|
| `@timestamp` | `date` | 로그 발생 시간 |
| `level` | `keyword` | 로그 레벨 (INFO, ERROR 등) |
| `logger` | `keyword` | 로거 이름 (app.services.chat 등) |
| `message` | `text` | 로그 메시지 |
| `trace_id` | `keyword` | 요청 추적 ID (exact match) |
| `user_id` | `keyword` | 사용자 ID |
| `dept_id` | `keyword` | 부서 ID |
| `conversation_id` | `keyword` | 대화 ID |
| `turn_id` | `long` | 대화 턴 번호 |
| `exception_type` | `keyword` | 예외 타입 |
| `stacktrace` | `text` | 스택트레이스 (인덱싱 안됨) |

## 검증 시나리오

### 시나리오 1: trace_id로 요청 흐름 추적

특정 요청의 전체 로그를 한 번에 조회:

**Kibana Query (KQL):**
```
trace_id: "abc123-def456-..."
```

**검증 포인트:**
- access 로그 + app 로그가 같이 보이는지
- 요청 시작부터 끝까지 순서대로 나오는지

### 시나리오 2: ERROR 로그 모아보기

에러만 필터링해서 원인 파악:

**Kibana Query (KQL):**
```
level: "ERROR"
```

**상세 조건:**
```
level: "ERROR" AND logger: "app.services.*"
```

**검증 포인트:**
- `stacktrace` 필드가 1라인으로 잘 들어오는지
- `exception_type`으로 에러 유형 분류 가능한지

### 시나리오 3: 특정 사용자/대화 필터링

**사용자별 조회:**
```
user_id: "user123"
```

**대화별 조회:**
```
conversation_id: "conv-abc123"
```

**복합 조건:**
```
user_id: "user123" AND level: "ERROR" AND @timestamp >= "2025-01-01"
```

### 시나리오 4: 특정 서비스 로거 조회

**채팅 서비스만:**
```
logger: "app.services.chat*"
```

**LLM 클라이언트만:**
```
logger: "app.clients.llm_client"
```

## 대시보드 저장 쿼리 (Saved Searches)

Kibana에서 자주 쓰는 쿼리를 저장해두면 편리함:

1. **[ERROR] 최근 1시간 에러**
   - Query: `level: "ERROR"`
   - Time: Last 1 hour

2. **[TRACE] 요청 추적**
   - Query: `trace_id: *` (trace_id 입력용 템플릿)

3. **[USER] 사용자별 로그**
   - Query: `user_id: *`

## 운영 가이드

### 로그 보존 정책 (ILM)

- **Hot**: 1일 또는 10GB까지
- **Warm**: 7일 후 (샤드 축소 + 병합)
- **Delete**: 30일 후 삭제

### 인덱스 관리

```bash
# 인덱스 목록 확인
curl 'localhost:9200/_cat/indices/ctrlf-ai-*?v'

# 인덱스 삭제 (주의!)
curl -X DELETE 'localhost:9200/ctrlf-ai-2025.01.01'

# 템플릿 확인
curl 'localhost:9200/_index_template/ctrlf-ai-template?pretty'
```

### 트러블슈팅

**로그가 안 쌓일 때:**
```bash
# Fluent Bit 로그 확인
docker logs ctrlf-fluent-bit

# ES 연결 확인
docker exec ctrlf-fluent-bit curl -s elasticsearch:9200/_cluster/health
```

**필드가 text로 잡혔을 때:**
- 인덱스 삭제 후 템플릿 재적용 필요
- 또는 reindex API 사용

## Windows 환경

Windows에서는 Docker Desktop + WSL2 사용 권장.

### 자주 발생하는 오류

**1. Docker daemon이 실행되지 않음**
```
error during connect: ... open //./pipe/docker_engine: The system cannot find the file specified.
```

해결:
1. Docker Desktop 실행 (시작 메뉴 → "Docker Desktop")
2. 트레이 아이콘이 안정화될 때까지 1-2분 대기
3. `docker info` 명령으로 확인 후 재시도

**2. `version` is obsolete 경고**
```
level=warning msg="...: `version` is obsolete"
```

무시해도 됨. Docker Compose V2에서는 `version` 필드가 선택사항이지만, 있어도 정상 동작함.

**3. WSL2 관련 오류**
```
WSL 2 installation is incomplete
```

해결:
1. PowerShell (관리자) 실행
2. `wsl --install` 또는 `wsl --update` 실행
3. PC 재시작

### Fluent Bit 볼륨 마운트

Windows Docker Desktop (WSL2)에서는 경로가 다를 수 있음:
```yaml
volumes:
  - //var/run/docker.sock:/var/run/docker.sock:ro
```

### PowerShell에서 실행

```powershell
# Git Bash 대신 PowerShell 사용 시
docker compose -f elk/docker-compose.elk.yml up -d

# curl 대신 Invoke-WebRequest 사용
Invoke-WebRequest -Uri "http://localhost:9200/_cluster/health" | Select-Object -ExpandProperty Content
```
