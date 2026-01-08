# Phase 36: Presigned 업로드 안정화 + 업로드 진행상태 통지

**작성일**: 2025-12-19
**Phase**: 36
**상태**: 완료
**테스트 결과**: 33 passed (Phase 35 18개 + Phase 36 15개)

---

## 1. 개요

Phase 36에서는 BackendPresignedStorageProvider의 업로드 로직을 운영 환경에 맞게 안정화했습니다.

### 1.1 목표

- **A) 재시도 정책**: 5xx/네트워크 오류는 재시도, 4xx는 즉시 실패
- **B) 스트리밍 업로드**: 파일을 메모리에 전체 로드하지 않음
- **C) ETag 검증 강화**: 기본적으로 ETag 없으면 실패 (옵션으로 완화 가능)
- **D) 진행상태 통지**: UPLOAD_STARTED, UPLOAD_DONE, UPLOAD_FAILED 콜백

---

## 2. 재시도 정책 (Retry Policy)

### 2.1 재시도 대상

```
┌─────────────────────────────────────────────────────────────────────┐
│  재시도 대상 (Retryable)                                            │
│                                                                     │
│  • 5xx 서버 오류 (500, 502, 503, 504 등)                           │
│  • 네트워크 오류 (ConnectError, TimeoutException, NetworkError)     │
│                                                                     │
│  → 최대 3회 재시도 (총 4번 시도)                                    │
│  → Exponential backoff + jitter                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  재시도 금지 (Non-retryable) - 즉시 실패                            │
│                                                                     │
│  • 4xx 클라이언트 오류:                                             │
│    - 401 Unauthorized: 인증 실패                                    │
│    - 403 Forbidden: 권한 없음                                       │
│    - 404 Not Found: 리소스 없음                                     │
│    - 422 Unprocessable Entity: 요청 데이터 오류                     │
│                                                                     │
│  → 재시도해도 동일한 결과, 즉시 실패 처리                           │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Exponential Backoff with Jitter

```python
# 재시도 대기시간 계산
backoff = base_sec * (2 ** attempt)  # 1s, 2s, 4s, ...
jitter = random.uniform(0, backoff)   # 무작위 지연
wait_time = backoff + jitter          # 실제 대기시간
```

예시 (base_sec=1.0):
- 1차 재시도: 1~2초 대기
- 2차 재시도: 2~4초 대기
- 3차 재시도: 4~8초 대기

### 2.3 환경변수

```env
# 재시도 설정
STORAGE_UPLOAD_RETRY_MAX=3           # 최대 재시도 횟수 (기본: 3)
STORAGE_UPLOAD_RETRY_BASE_SEC=1.0    # Backoff 기본 시간 (기본: 1.0초)
```

---

## 3. 스트리밍 업로드

### 3.1 변경 전 (bytes 전체 로드)

```python
# Phase 35: bytes 전체를 메모리에 로드
data = file_path.read_bytes()  # 100MB 파일 → 100MB 메모리 사용
response = await client.put(url, content=data, headers=headers)
```

### 3.2 변경 후 (파일 핸들 스트리밍)

```python
# Phase 36: 파일 핸들을 스트리밍으로 전송
with open(file_path, "rb") as f:
    response = await client.put(url, content=f, headers=headers)
# → 청크 단위로 전송, 메모리 효율적
```

### 3.3 Content-Type 매핑

```python
# 최소한의 타입 매핑 (확장자 기반 자동 추론)
.mp4  → video/mp4
.srt  → application/x-subrip
.vtt  → text/vtt
.jpg  → image/jpeg
.png  → image/png
```

---

## 4. ETag 검증

### 4.1 검증 정책

```
┌─────────────────────────────────────────────────────────────────────┐
│  기본 정책 (STORAGE_ETAG_OPTIONAL=False)                            │
│                                                                     │
│  S3 PUT 응답에서 ETag 헤더가:                                       │
│  • 없거나 빈 문자열 → StorageUploadError 발생                       │
│  • 정상 값 있음 → Complete 콜백에 포함하여 전송                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  DEV 환경 정책 (STORAGE_ETAG_OPTIONAL=True)                         │
│                                                                     │
│  S3 PUT 응답에서 ETag 헤더가:                                       │
│  • 없거나 빈 문자열 → 경고 로그만 남기고 진행                       │
│  • Complete 콜백에 빈 ETag 전송                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 환경변수

```env
# ETag 검증 정책 (기본: False - 엄격 모드)
STORAGE_ETAG_OPTIONAL=False

# DEV 환경에서 완화 시
STORAGE_ETAG_OPTIONAL=True
```

---

## 5. 업로드 진행상태 통지

### 5.1 UploadProgress 데이터클래스

```python
@dataclass
class UploadProgress:
    """업로드 진행상태 콜백 정보."""
    stage: str  # UPLOAD_STARTED, UPLOAD_DONE, UPLOAD_FAILED
    key: str
    message: str = ""
    error: Optional[str] = None
```

### 5.2 진행상태 이벤트

| 이벤트 | 시점 | 설명 |
|--------|------|------|
| `UPLOAD_STARTED` | put_object 시작 시 | 업로드 시작 알림 |
| `UPLOAD_DONE` | 업로드 성공 시 | 업로드 완료 알림 |
| `UPLOAD_FAILED` | 업로드 실패 시 | 에러 메시지 포함 |

### 5.3 사용 예시

```python
def on_progress(progress: UploadProgress):
    if progress.stage == "UPLOAD_STARTED":
        print(f"업로드 시작: {progress.key}")
    elif progress.stage == "UPLOAD_DONE":
        print(f"업로드 완료: {progress.key}")
    elif progress.stage == "UPLOAD_FAILED":
        print(f"업로드 실패: {progress.key}, 에러: {progress.error}")

result = await provider.put_object(
    file_path,
    key,
    "video/mp4",
    progress_callback=on_progress
)
```

---

## 6. 에러 전파 구조

### 6.1 에러 흐름

```
┌─────────────────────────────────────────────────────────────────────┐
│  에러 발생 지점 → StorageUploadError → RenderJobRunner             │
│                                                                     │
│  Presign 실패:                                                      │
│  └─ "Presign request failed after 4 attempts"                       │
│                                                                     │
│  Upload 실패 (4xx):                                                 │
│  └─ "Presigned PUT upload failed: HTTP 403"                         │
│                                                                     │
│  Upload 실패 (5xx/네트워크):                                        │
│  └─ "Presigned PUT upload failed after 4 attempts"                  │
│                                                                     │
│  Complete 실패:                                                     │
│  └─ "Complete notification failed after 4 attempts"                 │
│                                                                     │
│  ETag 누락 (엄격 모드):                                             │
│  └─ "ETag missing in S3 response"                                   │
│                                                                     │
│  용량 초과:                                                         │
│  └─ "File size 150.5MB exceeds limit 100.0MB"                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 6.2 StorageUploadError 구조

```python
class StorageUploadError(Exception):
    def __init__(
        self,
        message: str,
        key: str,
        original_error: Optional[Exception] = None
    ):
        self.message = message
        self.key = key
        self.original_error = original_error
```

---

## 7. 테스트

### 7.1 테스트 파일

| 파일 | 테스트 수 | 설명 |
|------|-----------|------|
| `test_phase35_storage_backend_presigned.py` | 18 | Phase 35 기능 테스트 (Phase 36 호환) |
| `test_phase36_presigned_upload_retry.py` | 15 | Phase 36 재시도/ETag/진행상태 테스트 |

### 7.2 주요 테스트 케이스

```python
# Phase 36 필수 테스트 케이스
class TestPresign5xxRetrySuccess:
    async def test_presign_5xx_retry_then_success()          # presign 5xx → 재시도 후 성공
    async def test_presign_5xx_max_retries_exceeded_fails()  # presign 5xx → 최대 재시도 후 실패

class TestUploadNetworkErrorRetrySuccess:
    async def test_upload_connect_error_retry_then_success() # upload 네트워크 오류 → 재시도 후 성공
    async def test_upload_timeout_error_retry_then_success() # upload 타임아웃 → 재시도 후 성공

class TestComplete5xxRetrySuccess:
    async def test_complete_5xx_retry_then_success()         # complete 5xx → 재시도 후 성공

class TestUpload403NoRetry:
    async def test_upload_403_no_retry_immediate_failure()   # upload 403 → 재시도 없이 즉시 실패
    async def test_upload_401_no_retry_immediate_failure()   # upload 401 → 재시도 없이 즉시 실패
    async def test_presign_404_no_retry_immediate_failure()  # presign 404 → 재시도 없이 즉시 실패

class TestETagMissingFailure:
    async def test_etag_missing_fails_by_default()           # ETag 누락 → 기본 정책: 실패
    async def test_etag_optional_allows_missing_etag()       # ETAG_OPTIONAL=True → 성공

class TestUploadProgressCallback:
    async def test_progress_callback_success_flow()          # 성공: STARTED → DONE
    async def test_progress_callback_failure_flow()          # 실패: STARTED → FAILED
```

### 7.3 테스트 실행

```bash
# Phase 36 테스트만 실행
python -m pytest tests/test_phase36_presigned_upload_retry.py -v

# Phase 35 + 36 통합 테스트
python -m pytest tests/test_phase35_*.py tests/test_phase36_*.py -v

# 전체 실행 결과: 33 passed
```

---

## 8. 변경된 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/core/config.py` | 수정 | Phase 36 환경변수 추가 |
| `app/clients/storage_adapter.py` | 수정 | 재시도/스트리밍/ETag/진행상태 구현 |
| `tests/test_phase35_storage_backend_presigned.py` | 수정 | Phase 36 호환성 업데이트 |
| `tests/test_phase36_presigned_upload_retry.py` | 신규 | Phase 36 테스트 (15개) |
| `docs/DEVELOPMENT_REPORT_PHASE36.md` | 신규 | 이 문서 |

---

## 9. 환경변수 전체 목록

```env
# Phase 35 (기존)
STORAGE_PROVIDER=backend_presigned
BACKEND_BASE_URL=http://backend:8080
BACKEND_SERVICE_TOKEN=your-service-token-here
BACKEND_STORAGE_PRESIGN_PATH=/internal/storage/presign-put
BACKEND_STORAGE_COMPLETE_PATH=/internal/storage/complete
VIDEO_MAX_UPLOAD_BYTES=104857600
STORAGE_PUBLIC_BASE_URL=https://cdn.example.com

# Phase 36 (신규)
STORAGE_UPLOAD_RETRY_MAX=3              # 최대 재시도 횟수 (기본: 3)
STORAGE_UPLOAD_RETRY_BASE_SEC=1.0       # Backoff 기본 시간 (기본: 1.0초)
STORAGE_ETAG_OPTIONAL=False             # ETag 검증 완화 (기본: False)
```

---

## 10. 다음 단계 (Phase 37+)

- [ ] 바이트 단위 업로드 진행률 (현재는 단계별만 제공)
- [ ] Multipart Upload 지원 (파일 > 100MB 정책 확정 후)
- [ ] CloudFront 연동 최적화
- [ ] 업로드 진행상태 WebSocket 통합 (Phase 33 WS 연동)

---

## 11. 참고: 재시도 로직 코드

```python
async def _with_retry(self, operation_name, operation, key, *args, **kwargs):
    """재시도 로직 래퍼."""
    for attempt in range(self._retry_max + 1):
        try:
            return await operation(*args, **kwargs)
        except (httpx.HTTPStatusError, ...) as e:
            if not self._should_retry(e):
                # 4xx는 재시도하지 않고 즉시 실패
                raise StorageUploadError(
                    f"{operation_name} failed: HTTP {status_code}",
                    key, e
                )
            if attempt == self._retry_max:
                # 최대 재시도 후 실패
                raise StorageUploadError(
                    f"{operation_name} failed after {self._retry_max + 1} attempts",
                    key, e
                )
            # Exponential backoff with jitter
            backoff = self._retry_base_sec * (2 ** attempt)
            jitter = random.uniform(0, backoff)
            await asyncio.sleep(backoff + jitter)

def _should_retry(self, error):
    """재시도 여부 판단."""
    if isinstance(error, (ConnectError, TimeoutException)):
        return True  # 네트워크 오류는 재시도
    if isinstance(error, HTTPStatusError):
        status = error.response.status_code
        return 500 <= status < 600  # 5xx만 재시도
    return False
```
