# Phase 22 Gap Analysis: 설계 정합성 검토

**작성일**: 2025-12-18
**대상**: 교육영상 진행률/완료/퀴즈 언락 + RouterOrchestrator 통합

---

## 1. Last Segment 정의 (2-1)

### 현재 구현
```python
# video_progress_service.py
FINAL_SEGMENT_RATIO = 0.05  # 마지막 5% 구간
final_segment_start = int(record.total_duration * (1 - self._final_segment_ratio))
```
- 마지막 구간 = 총 시간의 5%
- 600초 영상 → 마지막 30초
- 60초 영상 → 마지막 3초 (너무 짧음)

### 문제점
- 설계: `last_segment_seconds = max(total_duration * 0.05, 30.0)`
- 현재: 5%만 적용하여 짧은 영상에서 마지막 구간이 너무 짧아짐

### 수정안
```python
# 마지막 구간 초 = max(총 시간의 5%, 30초)
last_segment_seconds = max(total_duration * 0.05, 30.0)
# 완료 판정: final_position >= total_duration - last_segment_seconds
```

### 영향
- `video_progress_service.py`: `_check_final_segment()` 로직 수정
- 기존 테스트 중 짧은 영상 케이스 수정 필요

---

## 2. 상태 값 명칭 (2-2)

### 현재 구현
```python
class VideoProgressState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    PLAYING = "PLAYING"        # ← 설계와 불일치
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
```

### 문제점
- 설계 명칭: `NOT_STARTED → IN_PROGRESS → COMPLETED`
- 현재: `PLAYING` 사용

### 수정안
```python
class VideoProgressState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"  # PLAYING → IN_PROGRESS
    # PAUSED 제거 (설계에 없음)
    COMPLETED = "COMPLETED"
```

### 영향
- `video_progress.py`: Enum 값 변경
- `video_progress_service.py`: 상태 참조 변경
- 테스트 코드의 `PLAYING` → `IN_PROGRESS` 변경
- API 응답은 그대로 유지 (호환성)

---

## 3. 클라이언트 입력 신뢰 문제 (2-3)

### 현재 구현
- `user_id`: 요청 바디에서 그대로 사용
- `total_duration`: 클라이언트가 보내는 값 그대로 저장
- `is_mandatory_edu`: 클라이언트가 보내는 값 그대로 저장

### 문제점
- 클라이언트가 조작 가능한 값들을 신뢰
- `total_duration`을 짧게 보내면 완료 조건 쉽게 통과
- `is_mandatory_edu=false`로 보내면 퀴즈 언락 우회

### 수정안

#### 3-1. user_id 처리
```python
# app/api/v1/dependencies.py
async def get_actor_user_id(
    request: Request,
    body_user_id: Optional[str] = None,
) -> str:
    """
    우선순위:
    1. JWT claim의 user_id (있으면)
    2. body의 user_id (JWT 없으면, dev only 경고)

    JWT와 body 둘 다 있고 다르면 403
    """
    jwt_user_id = request.state.user_id if hasattr(request.state, 'user_id') else None

    if jwt_user_id and body_user_id and jwt_user_id != body_user_id:
        raise HTTPException(403, "User ID mismatch")

    if jwt_user_id:
        return jwt_user_id

    if body_user_id:
        logger.warning("Using body user_id without JWT (dev only)")
        return body_user_id

    raise HTTPException(401, "User ID required")
```

#### 3-2. total_duration 처리
```python
# app/services/video_catalog_service.py (신규)
class VideoCatalogService:
    """영상 메타데이터 서버 신뢰 소스 (stub)."""

    def __init__(self):
        self._catalog: Dict[str, int] = {}  # video_id → duration_seconds

    def get_duration(self, video_id: str) -> Optional[int]:
        """서버 신뢰 duration 반환."""
        return self._catalog.get(video_id)

    def register(self, video_id: str, duration: int) -> None:
        """카탈로그에 영상 등록 (테스트/초기화용)."""
        self._catalog[video_id] = duration
```

#### 3-3. is_mandatory 처리
```python
# app/services/education_catalog_service.py (신규)
class EducationCatalogService:
    """교육 메타데이터 서버 신뢰 소스 (stub)."""

    # 4대교육 ID 패턴 또는 목록
    MANDATORY_4TYPE_PREFIXES = ("EDU-4TYPE-", "4EDU-")

    def is_mandatory_4type(self, education_id: str) -> bool:
        """4대교육 여부 서버 측 판정."""
        return any(education_id.startswith(p) for p in self.MANDATORY_4TYPE_PREFIXES)
```

### 영향
- 새 파일: `app/api/v1/dependencies.py`
- 새 파일: `app/services/video_catalog_service.py`
- 새 파일: `app/services/education_catalog_service.py`
- API 엔드포인트에서 의존성 주입 적용

---

## 4. 급상승(surge) 검증의 과도함 (2-4)

### 현재 구현
```python
# 10초 내 30% 이상 증가 시 거부
SURGE_TIME_WINDOW_SECONDS = 10.0
SURGE_MAX_INCREASE_PERCENT = 30.0

def _check_progress_surge(...):
    if time_diff <= self._surge_time_window:
        if progress_diff > self._surge_max_increase:
            return True, "surge detected"
```

### 문제점
- 클라이언트 업데이트 주기가 길면 정상 요청도 거부됨
- 예: 20초 후 한 번에 60% 업데이트 → 정상인데 잠재적 오탐

### 수정안
```python
def _check_progress_surge(
    self,
    old_position_seconds: int,
    new_position_seconds: int,
    elapsed_wall_clock_seconds: float,
    grace_seconds: float = 5.0,
) -> Tuple[bool, str]:
    """
    시간-위치 기반 surge 검증.

    delta_position <= elapsed_wall_clock + grace 이면 허용
    seek 불가 환경에서 이 검증만으로 충분
    """
    delta_position = new_position_seconds - old_position_seconds
    allowed_delta = elapsed_wall_clock_seconds + grace_seconds

    if delta_position > allowed_delta:
        return True, f"Position advanced {delta_position}s but only {elapsed_wall_clock_seconds}s elapsed"

    return False, ""
```

### 영향
- `video_progress_service.py`: surge 검증 로직 변경
- 테스트: 시간-위치 기반 케이스 추가

---

## 5. COMPLETED 이후 업데이트 처리 (2-5)

### 현재 구현
```python
# update_progress()에서
if record.state == VideoProgressState.COMPLETED:
    return VideoProgressUpdateResponse(
        ...
        accepted=True,  # ← 설계와 일치 (no-op, accepted)
        rejection_reason=None,
    )
```

### 문제점
- 현재 구현이 이미 설계와 일치: `accepted=True` 반환, 내부 상태 변경 없음
- 메시지만 추가 필요

### 수정안
```python
# 메시지 추가
if record.state == VideoProgressState.COMPLETED:
    return VideoProgressUpdateResponse(
        ...
        accepted=True,
        rejection_reason=None,
        message="이미 완료된 영상입니다."  # 추가
    )
```

### 영향
- 응답 모델에 `message` 필드 추가 (Optional)
- 이미 no-op 구현이므로 최소 변경

---

## 6. 퀴즈 언락 게이트의 정확성 (2-6)

### 현재 구현
```python
def can_start_quiz(self, user_id: str, training_id: str) -> Tuple[bool, str]:
    record = self._store.get(user_id, training_id)
    if not record:
        return True, "No video progress record (non-mandatory edu assumed)"

    if not record.is_mandatory_edu:  # ← 클라이언트가 보낸 값!
        return True, "Not mandatory education"

    if record.quiz_unlocked:
        return True, "Quiz unlocked after video completion"
```

### 문제점
- `is_mandatory_edu`가 클라이언트가 보낸 값
- 4대교육인지 서버 측 판정 없음

### 수정안
```python
def can_start_quiz(
    self,
    user_id: str,
    training_id: str,
    education_catalog: EducationCatalogService,
) -> Tuple[bool, str]:
    # 서버 측에서 4대교육 여부 판정
    is_mandatory_4type = education_catalog.is_mandatory_4type(training_id)

    if not is_mandatory_4type:
        return True, "Not 4-type mandatory education"

    # 4대교육인 경우 COMPLETED 확인
    record = self._store.get(user_id, training_id)
    if not record:
        return False, "No video progress record for mandatory education"

    if record.state != VideoProgressState.COMPLETED:
        return False, f"Video not completed (progress={record.progress_percent:.1f}%)"

    return True, "Quiz unlocked after video completion"
```

### 영향
- `video_progress_service.py`: `can_start_quiz()` 시그니처 및 로직 변경
- API 엔드포인트에서 `EducationCatalogService` 의존성 주입

---

## 7. RouterOrchestrator 활성 조건 (2-7)

### 현재 구현
```python
# chat_service.py
use_router_orchestrator = bool(settings.llm_base_url)
```

### 문제점
- `llm_base_url`이 설정되면 자동 활성화
- 의도가 불명확하고 테스트에서 제어 어려움

### 수정안
```python
# config.py
ROUTER_ORCHESTRATOR_ENABLED: bool = False  # 명시적 플래그

# chat_service.py
use_router_orchestrator = settings.ROUTER_ORCHESTRATOR_ENABLED
```

### 영향
- `config.py`: 새 설정 추가
- `chat_service.py`: 조건 변경
- 테스트에서 settings mock 수정

---

## 8. 변경 파일 요약

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/models/video_progress.py` | 수정 | PLAYING→IN_PROGRESS, message 필드 추가 |
| `app/services/video_progress_service.py` | 수정 | last segment, surge, can_start_quiz 로직 |
| `app/services/video_catalog_service.py` | **신규** | 영상 duration 서버 신뢰 소스 |
| `app/services/education_catalog_service.py` | **신규** | 4대교육 판정 서버 신뢰 소스 |
| `app/api/v1/dependencies.py` | **신규** | get_actor_user_id 의존성 |
| `app/api/v1/video.py` | 수정 | 의존성 주입, 상태명 변경 |
| `app/core/config.py` | 수정 | ROUTER_ORCHESTRATOR_ENABLED 추가 |
| `app/services/chat_service.py` | 수정 | 활성 조건 변경 |
| `tests/test_phase22_video_progress.py` | 수정 | 상태명, 새 테스트 케이스 |

---

## 9. 신규 테스트 요구사항

1. **last segment가 30초가 5%보다 큰 경우** - 60초 영상에서 30초 적용 확인
2. **COMPLETED 이후 progress 업데이트 no-op** - accepted=True, 상태 불변
3. **JWT user_id와 body user_id 불일치 시 거부** - 403 반환
4. **(선택) JWT 없을 때 body fallback 허용** - 경고 로그 + 허용
