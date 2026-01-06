# Phase 22 개발 보고서: ChatService 통합 + 교육영상 상태전이 서버 검증

## 개요

Phase 22에서는 두 가지 주요 기능을 구현했습니다:
1. **ChatService와 RouterOrchestrator 통합**: Phase 20-21에서 구현한 RouterOrchestrator를 실제 /chat 엔드포인트에 통합
2. **교육영상 상태전이 서버 검증**: 영상 시청 무결성을 위한 서버 측 검증 로직

---

## 1. 변경/추가된 파일 목록

### 신규 파일
| 파일 경로 | 설명 |
|-----------|------|
| `app/models/video_progress.py` | 영상 진행 관련 데이터 모델 |
| `app/services/video_progress_service.py` | 영상 상태전이 서버 검증 서비스 |
| `app/api/v1/video.py` | 영상 진행 REST API 엔드포인트 |
| `tests/test_phase22_video_progress.py` | 영상 진행 서비스 테스트 (15개) |
| `tests/test_phase22_chat_router_integration.py` | ChatService + RouterOrchestrator 통합 테스트 (7개) |

### 수정된 파일
| 파일 경로 | 변경 내용 |
|-----------|-----------|
| `app/services/chat_service.py` | RouterOrchestrator 통합, clarify/confirmation 처리 |
| `app/main.py` | video router 등록 |
| `tests/test_chat_rag_integration.py` | RouterOrchestrator 비활성화를 위한 settings mock 추가 |

---

## 2. 핵심 코드

### 2.1 VideoProgressService (`app/services/video_progress_service.py`)

```python
class VideoProgressService:
    """교육영상 상태전이 서버 검증 서비스."""

    # 검증 상수
    COMPLETION_THRESHOLD_PERCENT = 100.0  # 완료 인정 임계값 (100%면 완료)
    SURGE_GRACE_SECONDS = 5.0             # 급상승 감지 grace 허용치

    async def start_video(self, request: VideoPlayStartRequest) -> VideoPlayStartResponse:
        """영상 재생 시작 - 세션 생성 및 초기 상태 설정."""

    async def update_progress(self, request: VideoProgressUpdateRequest) -> VideoProgressUpdateResponse:
        """진행률 업데이트 - 회귀/급상승 검증."""

    async def complete_video(self, request: VideoCompleteRequest) -> VideoCompleteResponse:
        """완료 요청 - 100% 시청 필요."""

    async def can_start_quiz(self, session_id: str, user_id: str) -> VideoQuizCheckResponse:
        """퀴즈 시작 가능 여부 확인 (4대교육 필수 이수)."""
```

**주요 검증 로직:**
- **회귀 방지**: `current_percent < record.last_percent` → 거부
- **급상승 감지**: 시간-위치 기반 검증 (delta_position <= elapsed + grace)
- **완료 조건**: 100% 시청 필요 (100%면 완료)
- **Seek 제한**: 완료 전까지 `seek_allowed=false`

### 2.2 ChatService RouterOrchestrator 통합 (`app/services/chat_service.py`)

```python
async def handle_chat(self, request: ChatRequest) -> ChatResponse:
    """채팅 요청 처리 - RouterOrchestrator 통합."""

    # Phase 22: RouterOrchestrator 사용 여부 결정
    use_router_orchestrator = bool(settings.llm_base_url)

    if use_router_orchestrator:
        # RouterOrchestrator를 통한 라우팅
        orchestration_result = await self._router_orchestrator.route(
            user_message=safe_message,
            session_id=request.session_id,
            user_id=request.user_id,
            user_role=request.user_role,
            department=request.department,
        )

        # needs_clarify=true → 되묻기 응답
        if orchestration_result.needs_user_response and orchestration_result.response_message:
            return self._create_router_response(...)

        # SYSTEM_HELP → 시스템 도움말
        if router_result.route_type == RouterRouteType.ROUTE_SYSTEM_HELP:
            return self._create_system_help_response(...)

        # UNKNOWN → 범위 밖 안내
        if router_result.route_type == RouterRouteType.ROUTE_UNKNOWN:
            return self._create_unknown_route_response(...)
```

**주요 매핑 함수:**
```python
def _map_tier0_to_intent(self, tier0: Tier0Intent) -> IntentType:
    """Tier0Intent → IntentType 변환."""
    mapping = {
        Tier0Intent.POLICY_QA: IntentType.POLICY_QA,
        Tier0Intent.EDUCATION_QA: IntentType.EDUCATION_QA,
        Tier0Intent.BACKEND_STATUS: IntentType.BACKEND_STATUS,
        # ...
    }
    return mapping.get(tier0, IntentType.UNKNOWN)

def _map_router_route_to_route_type(self, router_route: RouterRouteType) -> RouteType:
    """RouterRouteType → RouteType 변환."""
    mapping = {
        RouterRouteType.RAG_INTERNAL: RouteType.RAG_INTERNAL,
        RouterRouteType.BACKEND_API: RouteType.BACKEND_API,
        RouterRouteType.LLM_ONLY: RouteType.LLM_ONLY,
        # ...
    }
    return mapping.get(router_route, RouteType.FALLBACK)
```

### 2.3 Video API 엔드포인트 (`app/api/v1/video.py`)

```python
router = APIRouter(prefix="/api/video")

@router.post("/play/start")      # 영상 재생 시작
@router.post("/progress")        # 진행률 업데이트
@router.post("/complete")        # 완료 요청
@router.get("/status")           # 상태 조회
@router.get("/quiz/check")       # 퀴즈 시작 가능 여부
```

---

## 3. 테스트 실행 방법

### 전체 테스트 실행
```bash
python -m pytest tests/ -v
```

### Phase 22 테스트만 실행
```bash
# 영상 진행 서비스 테스트
python -m pytest tests/test_phase22_video_progress.py -v

# ChatService + RouterOrchestrator 통합 테스트
python -m pytest tests/test_phase22_chat_router_integration.py -v
```

### 테스트 결과
```
578 passed, 12 deselected in 46.87s
```

**Phase 22 신규 테스트 (30개):**
- `test_phase22_video_progress.py`: 23개 테스트 (기존 15개 + 배속 제어 8개)
- `test_phase22_chat_router_integration.py`: 7개 테스트

---

## 4. 대표 시나리오 요청/응답 예시

### 시나리오 1: 영상 재생 시작

**요청 (POST /api/video/play/start)**
```json
{
  "user_id": "user-123",
  "training_id": "EDU-4TYPE-001",
  "total_duration": 600,
  "is_mandatory_edu": true,
  "playback_rate": 1.0
}
```

**응답**
```json
{
  "session_id": "video-session-001",
  "user_id": "user-123",
  "training_id": "EDU-4TYPE-001",
  "state": "IN_PROGRESS",
  "seek_allowed": false,
  "created_at": "2024-12-16T10:00:00Z",
  "first_watch": true,
  "max_playback_rate": 1.0,
  "playback_rate_reason": null
}
```

### 시나리오 1-1: 4대교육 최초 시청 배속 거부

**요청 (POST /api/video/play/start)**
```json
{
  "user_id": "user-123",
  "training_id": "EDU-4TYPE-001",
  "total_duration": 600,
  "is_mandatory_edu": true,
  "playback_rate": 2.0
}
```

**응답 (400 Bad Request)**
```json
{
  "error": "PLAYBACK_RATE_NOT_ALLOWED",
  "message": "4대교육 최초 시청 시 배속(1.0x 초과)은 허용되지 않습니다.",
  "max_playback_rate": 1.0,
  "reason": "MANDATORY_FIRST_WATCH"
}
```

### 시나리오 2: 진행률 업데이트 (급상승 감지)

**요청 (POST /api/video/progress)**
```json
{
  "user_id": "user-123",
  "training_id": "EDU-4TYPE-001",
  "current_position": 300,
  "watched_seconds": 300,
  "playback_rate": 1.0
}
```

**응답 (급상승 거부 - 시간-위치 기반)**
```json
{
  "user_id": "user-123",
  "training_id": "EDU-4TYPE-001",
  "progress_percent": 10.0,
  "watched_seconds": 60,
  "last_position": 60,
  "seek_allowed": false,
  "state": "IN_PROGRESS",
  "updated_at": "2024-12-16T10:01:00Z",
  "accepted": false,
  "rejection_reason": "PROGRESS_SURGE",
  "max_playback_rate": 1.0,
  "playback_rate_enforced": false
}
```

### 시나리오 2-1: 진행률 업데이트 (배속 거부)

**요청 (POST /api/video/progress)**
```json
{
  "user_id": "user-123",
  "training_id": "EDU-4TYPE-001",
  "current_position": 60,
  "watched_seconds": 60,
  "playback_rate": 2.0
}
```

**응답 (4대교육 최초 시청 배속 거부)**
```json
{
  "user_id": "user-123",
  "training_id": "EDU-4TYPE-001",
  "progress_percent": 0.0,
  "watched_seconds": 0,
  "last_position": 0,
  "seek_allowed": false,
  "state": "IN_PROGRESS",
  "updated_at": "2024-12-16T10:00:00Z",
  "accepted": false,
  "rejection_reason": "PLAYBACK_RATE_NOT_ALLOWED",
  "message": "4대교육 최초 시청 시 배속(1.0x 초과)은 허용되지 않습니다.",
  "max_playback_rate": 1.0,
  "playback_rate_enforced": false
}
```

### 시나리오 3: ChatService 되묻기 응답

**요청 (POST /ai/chat/messages)**
```json
{
  "session_id": "chat-session-001",
  "user_id": "user-123",
  "user_role": "EMPLOYEE",
  "messages": [
    {"role": "user", "content": "교육 알려줘"}
  ]
}
```

**응답 (needs_clarify=true)**
```json
{
  "answer": "교육 내용 설명이 필요하신가요, 아니면 내 이수현황/진도 조회가 필요하신가요?",
  "sources": [],
  "meta": {
    "intent": "EDUCATION_QA",
    "route": "CLARIFY",
    "domain": "EDU",
    "confidence": 0.5,
    "needs_clarify": true
  }
}
```

### 시나리오 4: 영상 완료 및 퀴즈 잠금 해제

**요청 (POST /api/video/complete)**
```json
{
  "session_id": "video-session-001",
  "user_id": "user-123",
  "final_percent": 98.5,
  "final_position_seconds": 591.0
}
```

**응답**
```json
{
  "session_id": "video-session-001",
  "completed": true,
  "quiz_unlocked": true,
  "message": "영상 시청이 완료되었습니다. 퀴즈를 진행할 수 있습니다.",
  "completion_time": "2024-12-16T10:30:00Z"
}
```

---

## 5. 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                        ChatService                               │
├─────────────────────────────────────────────────────────────────┤
│  handle_chat()                                                   │
│    │                                                             │
│    ├─► PiiService.detect_and_mask()                             │
│    │                                                             │
│    ├─► RouterOrchestrator.route()  ◄── Phase 22 통합            │
│    │     │                                                       │
│    │     ├─► needs_clarify? → 되묻기 응답 반환                  │
│    │     ├─► requires_confirmation? → 확인 프롬프트 반환        │
│    │     ├─► SYSTEM_HELP? → 도움말 응답                         │
│    │     └─► UNKNOWN? → 범위 밖 안내                            │
│    │                                                             │
│    ├─► route_type에 따른 분기 처리                              │
│    │     ├─► RAG_INTERNAL → RAGFlowClient.search_as_sources()   │
│    │     ├─► BACKEND_API → BackendDataService                   │
│    │     └─► LLM_ONLY → LLMClient 직접 호출                     │
│    │                                                             │
│    └─► LLMClient.generate_chat_completion()                     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    VideoProgressService                          │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐     │
│  │ NOT_STARTED │───►│   PLAYING    │───►│    COMPLETED    │     │
│  └─────────────┘    └──────────────┘    └─────────────────┘     │
│        │                   │                     │               │
│   start_video()      update_progress()     complete_video()     │
│                            │                                     │
│                      ┌─────┴─────┐                              │
│                      │ 검증 로직  │                              │
│                      ├───────────┤                              │
│                      │ 회귀 방지  │                              │
│                      │ 급상승 감지│                              │
│                      │ 완료 조건  │                              │
│                      └───────────┘                              │
│                                                                  │
│  Quiz Unlock: is_mandatory=true + COMPLETED → quiz_unlocked=true│
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. 주요 설계 결정

### 6.1 RouterOrchestrator 조건부 사용
```python
use_router_orchestrator = bool(settings.llm_base_url)
```
- LLM이 설정되지 않은 환경(테스트 등)에서는 기존 IntentService 기반 분류 사용
- 하위 호환성 유지

### 6.2 영상 진행 검증 상수
| 상수 | 값 | 설명 |
|------|-----|------|
| COMPLETION_THRESHOLD_PERCENT | 100.0% | 완료로 인정되는 시청률 (100%면 완료) |
| SURGE_GRACE_SECONDS | 5초 | 급상승 감지 grace 허용치 |
| DEFAULT_PLAYBACK_RATE | 1.0 | 기본 재생 속도 |
| MAX_PLAYBACK_RATE_ALLOWED | 2.0 | 일반 교육/재시청 최대 허용 배속 |
| MAX_PLAYBACK_RATE_MANDATORY_FIRST_WATCH | 1.0 | 4대교육 최초 시청 최대 허용 배속 |

### 6.3 배속 제어 정책 (Playback Rate Control)

**정책 요약:**
- 배속은 기본적으로 지원 (0.5x ~ 2.0x)
- **4대교육(필수/mandatory) 영상**은 **최초 시청(first_watch=true)** 시 **1.0배속만 허용**
- 최초 시청이 아닌 경우(재시청, first_watch=false)는 배속 허용
- 일반 교육은 항상 배속 허용

**서버 검증:**
- `is_mandatory_edu=true AND first_watch=true AND playback_rate > 1.0` → 400 에러 반환
- 응답에 `max_playback_rate` 필드로 프론트엔드가 UI 제어 가능

**요청/응답 필드:**
| 필드 | 위치 | 타입 | 설명 |
|------|------|------|------|
| `playback_rate` | Request | float | 재생 속도 (0.5 ~ 2.0, 기본 1.0) |
| `first_watch` | Response | bool | 최초 시청 여부 |
| `max_playback_rate` | Response | float | 허용 최대 배속 (1.0 또는 2.0) |
| `playback_rate_reason` | Response | string | 배속 제한 사유 (MANDATORY_FIRST_WATCH) |
| `playback_rate_enforced` | Response | bool | 배속 강제 조정 여부 |

**시나리오별 동작:**
| 시나리오 | playback_rate 요청 | 결과 |
|----------|-------------------|------|
| 4대교육 + 최초 시청 + 2.0x | 거부 (400 에러) | `PLAYBACK_RATE_NOT_ALLOWED` |
| 4대교육 + 최초 시청 + 1.0x | 허용 | `max_playback_rate=1.0` |
| 4대교육 + 재시청 + 2.0x | 허용 | `max_playback_rate=2.0` |
| 일반 교육 + 2.0x | 허용 | `max_playback_rate=2.0` |

### 6.4 메모리 기반 세션 저장소
- 현재 구현: `Dict[str, VideoProgressRecord]` 인메모리 저장
- 향후 개선: Redis 또는 DB 기반 영속화 필요

---

## 7. 향후 개선 사항

1. **영상 세션 영속화**: Redis/DB로 세션 데이터 영속화
2. **다중 인스턴스 지원**: 분산 환경에서의 세션 공유
3. **세션 만료 처리**: TTL 기반 자동 정리
4. **분석 데이터 수집**: 시청 패턴 분석을 위한 로깅 강화

---

**작성일**: 2024-12-16
**Phase**: 22
**테스트 결과**: 578 passed (22 new tests added)
