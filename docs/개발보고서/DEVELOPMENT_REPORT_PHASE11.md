# Phase 11 개발 보고서: 백엔드 연동 라우트 구현

## 개요

Phase 11에서는 BACKEND_API 및 MIXED_BACKEND_RAG 라우트의 실제 처리 로직을 구현했습니다. 이를 통해 ctrlf-back 스프링 백엔드에서 업무 데이터를 조회하고, 이를 LLM 컨텍스트로 변환하여 응답을 생성할 수 있게 되었습니다.

## 구현 목표

| 항목 | 상태 |
|------|------|
| BackendDataClient 생성 | ✅ 완료 |
| BackendContextFormatter 생성 | ✅ 완료 |
| BACKEND_API 라우트 처리 | ✅ 완료 |
| MIXED_BACKEND_RAG 라우트 처리 | ✅ 완료 |
| Role × Domain × Intent 매핑 | ✅ 완료 |
| 테스트 추가 | ✅ 완료 (25개) |
| 전체 테스트 통과 | ✅ 170개 통과 |

## 신규 생성 파일

### 1. `app/clients/backend_data_client.py`

ctrlf-back 스프링 백엔드에서 업무 데이터를 조회하는 HTTP 클라이언트입니다.

**주요 기능:**
- `get_employee_edu_status(user_id)`: 직원 교육 현황 조회
- `get_department_edu_stats(department_id)`: 부서 교육 통계 조회
- `get_incident_overview(quarter)`: 사고 현황 요약 조회
- `get_incident_detail(incident_id)`: 사건 상세 조회
- `get_report_guide()`: 신고 안내 조회

**특징:**
- `BACKEND_BASE_URL` 미설정 시 Mock 데이터 반환 (개발 편의성)
- `BackendDataResponse` 데이터클래스로 응답 표준화
- 엔드포인트 경로 상수화 (백엔드 API 확정 후 조정 용이)

```python
# 엔드포인트 상수 (TODO: 백엔드 API 확정 후 조정)
BACKEND_EDU_STATUS_PATH = "/api/edu/status"
BACKEND_EDU_STATS_PATH = "/api/edu/stats"
BACKEND_INCIDENT_OVERVIEW_PATH = "/api/incidents/overview"
BACKEND_INCIDENT_DETAIL_PATH = "/api/incidents/{incident_id}"
BACKEND_REPORT_GUIDE_PATH = "/api/incidents/report-guide"
```

### 2. `app/services/backend_context_formatter.py`

백엔드 JSON 데이터를 LLM이 이해할 수 있는 구조화된 텍스트로 변환합니다.

**포맷팅 메서드:**
- `format_edu_status_for_llm()`: 직원 교육 현황 → 텍스트
- `format_edu_stats_for_llm()`: 부서 교육 통계 → 텍스트
- `format_incident_overview_for_llm()`: 사고 현황 요약 → 텍스트
- `format_incident_detail_for_llm()`: 사건 상세 → 텍스트
- `format_report_guide_for_llm()`: 신고 안내 → 텍스트
- `format_mixed_context()`: RAG + Backend 통합 컨텍스트

**출력 예시:**
```
[교육 수료 현황]
- 총 필수 교육: 4개
- 수료 완료: 3개
- 미수료: 1개
- 다음 마감일: 2025-12-31

[수료 완료 교육]
- 정보보호교육 (2025-03-15 수료)
- 개인정보보호교육 (2025-04-20 수료)

[미수료 교육]
- 산업안전보건 (마감: 2025-12-31)
```

## 수정 파일

### `app/services/chat_service.py`

**주요 변경사항:**

1. **의존성 주입 추가:**
   ```python
   def __init__(self, ...):
       self._backend_data_client = BackendDataClient()
       self._context_formatter = BackendContextFormatter()
   ```

2. **라우트별 분기 처리:**
   ```python
   # Route 분류
   rag_only_routes = {RouteType.RAG_INTERNAL}
   mixed_routes = {RouteType.MIXED_BACKEND_RAG}
   backend_api_routes = {RouteType.BACKEND_API}

   if route_type in rag_only_routes:
       # 기존 RAG 검색 로직
   elif route_type in mixed_routes:
       # RAG + Backend 병렬 조회
   elif route_type in backend_api_routes:
       # Backend API만 조회
   ```

3. **MIXED_BACKEND_RAG 병렬 처리:**
   ```python
   async def _handle_mixed_backend_rag(self, ...):
       # RAG 검색과 백엔드 조회를 병렬로 실행
       rag_task = self._perform_rag_search(...)
       backend_task = self._fetch_backend_data_for_mixed(...)

       rag_context, backend_result = await asyncio.gather(rag_task, backend_task)

       # 통합 컨텍스트 생성
       combined_context = self._context_formatter.format_mixed_context(
           rag_context, backend_context, domain
       )
   ```

4. **Role × Domain × Intent 매핑:**
   ```python
   async def _fetch_backend_data_for_api(self, role, domain, intent_type, user_id):
       # EMPLOYEE + EDU + MY_EDU_STATUS → get_employee_edu_status
       # ADMIN + EDU + DEPT_EDU_STATS → get_department_edu_stats
       # ADMIN + INCIDENT + INCIDENT_DETAIL → get_incident_overview
       # INCIDENT_MANAGER + INCIDENT + * → get_incident_overview
       # * + * + REPORT_VIOLATION → get_report_guide
   ```

5. **새 시스템 프롬프트:**
   - `SYSTEM_PROMPT_MIXED_BACKEND_RAG`: RAG + 백엔드 통합 응답용
   - `SYSTEM_PROMPT_BACKEND_API`: 백엔드 데이터 기반 응답용

## 테스트 현황

### `tests/test_phase11_backend_integration.py` (25개 테스트)

| 카테고리 | 테스트 수 | 설명 |
|----------|----------|------|
| BackendDataClient 단위 테스트 | 8개 | Mock 데이터, HTTP 연동 |
| BackendContextFormatter 단위 테스트 | 6개 | 각 포맷터 메서드 |
| ChatService BACKEND_API 테스트 | 2개 | 라우트 처리 검증 |
| ChatService MIXED 테스트 | 2개 | 병렬 처리 검증 |
| Role×Domain×Intent 매핑 테스트 | 4개 | 매핑 로직 검증 |
| LLM 메시지 빌더 테스트 | 2개 | 프롬프트 구성 검증 |
| 엔드포인트 상수 테스트 | 1개 | 상수 정의 확인 |

### 전체 테스트 결과

```
============================= 170 passed in 2.45s =============================
```

- Phase 10까지: 145개
- Phase 11 신규: 25개
- **총 170개 테스트 통과**

## 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                        ChatService                               │
├─────────────────────────────────────────────────────────────────┤
│  handle_chat()                                                   │
│      │                                                           │
│      ├── RouteType.RAG_INTERNAL                                  │
│      │       └── _perform_rag_search()                           │
│      │                                                           │
│      ├── RouteType.BACKEND_API                                   │
│      │       └── _fetch_backend_data_for_api()                   │
│      │               │                                           │
│      │               └── BackendDataClient                       │
│      │                       ├── get_employee_edu_status()       │
│      │                       ├── get_department_edu_stats()      │
│      │                       ├── get_incident_overview()         │
│      │                       └── get_report_guide()              │
│      │                                                           │
│      └── RouteType.MIXED_BACKEND_RAG                             │
│              │                                                   │
│              ├── asyncio.gather() ─────────────────────┐         │
│              │       │                                 │         │
│              │       ▼                                 ▼         │
│              │   RAG Search              Backend Fetch           │
│              │                                                   │
│              └── BackendContextFormatter                         │
│                      └── format_mixed_context()                  │
└─────────────────────────────────────────────────────────────────┘
```

## 설정 항목

### `app/core/config.py`

```python
# Phase 11: Backend Data Client
BACKEND_BASE_URL: str = ""  # ctrlf-back Spring 백엔드 URL
```

**환경변수:**
- `BACKEND_BASE_URL`: 백엔드 서버 URL (예: `http://localhost:8080`)
- 미설정 시 Mock 데이터 반환

## 향후 작업

### 백엔드 API 확정 후

1. **엔드포인트 경로 조정:**
   - `backend_data_client.py`의 `BACKEND_*_PATH` 상수 수정
   - 실제 API 스펙에 맞게 파라미터 조정

2. **응답 필드 매핑:**
   - `BackendContextFormatter` 필드명 조정
   - 실제 JSON 구조에 맞게 파싱 로직 수정

3. **인증/권한:**
   - JWT 토큰 전달 로직 추가
   - 사용자별 권한 검증 연동

### 통합 테스트

```bash
# Docker Compose로 실제 백엔드와 연동 테스트
docker-compose up -d
BACKEND_BASE_URL=http://localhost:8080 python -m pytest tests/integration/
```

## 요약

Phase 11에서는 BACKEND_API와 MIXED_BACKEND_RAG 라우트의 실제 처리 로직을 구현했습니다:

1. **BackendDataClient**: ctrlf-back에서 교육/사고 데이터 조회
2. **BackendContextFormatter**: JSON → LLM 컨텍스트 텍스트 변환
3. **ChatService 통합**: 라우트별 분기, 병렬 처리, Role×Domain×Intent 매핑
4. **25개 테스트**: 전체 170개 테스트 통과

백엔드 API 스펙이 확정되면 엔드포인트 경로와 필드 매핑만 조정하면 실제 연동이 가능합니다.
