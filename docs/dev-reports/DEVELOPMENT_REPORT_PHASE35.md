# Phase 35: 영상 산출물 S3 저장 - Backend Presigned URL 방식

**작성일**: 2025-12-19
**Phase**: 35
**상태**: 완료
**테스트 결과**: 45 passed (Phase 34 + Phase 35 통합)

---

## 1. 개요

Phase 35에서는 AI 서버가 AWS 자격증명 없이 영상 산출물(mp4/srt/jpg)을 S3에 저장하는 기능을 구현했습니다.

### 1.1 왜 Presigned URL 방식인가? (최소권한 원칙)

```
┌─────────────────────────────────────────────────────────────────────┐
│  기존 방식 (Phase 34 S3Provider)                                     │
│  ┌─────────┐     AWS Keys      ┌─────────┐                          │
│  │ AI 서버 │ ──────────────────▶│   S3    │                          │
│  └─────────┘  (직접 업로드)     └─────────┘                          │
│      ↓                                                               │
│  ❌ AI 서버가 S3 전체 접근 권한 보유                                 │
│  ❌ 자격증명 유출 시 전체 버킷 노출                                  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Phase 35 방식 (BackendPresignedStorageProvider)                     │
│  ┌─────────┐   1. Presign 요청    ┌─────────┐   (AWS Keys)  ┌─────┐ │
│  │ AI 서버 │ ─────────────────────▶│ Backend │ ─────────────▶│ S3  │ │
│  └─────────┘                       └─────────┘               └─────┘ │
│       │                                  │                           │
│       │        2. upload_url 반환        │                           │
│       │◀─────────────────────────────────│                           │
│       │                                                              │
│       │        3. PUT (Presigned URL)                                │
│       │─────────────────────────────────────────────────────▶│ S3  │ │
│       │                                                      └─────┘ │
│       │        4. Complete 콜백                                      │
│       │─────────────────────────────────▶│ Backend │                 │
│       ▼                                  └─────────┘                 │
│  ✅ AI 서버는 AWS 자격증명 미보유                                    │
│  ✅ 각 업로드에 대해 시간 제한된 URL만 사용                          │
│  ✅ Backend가 S3 권한 관리 (중앙 집중화)                             │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 목표

- **A) BackendPresignedStorageProvider**: AWS 자격증명 없이 Presigned URL로 S3 업로드
- **B) Internal API 계약**: presign-put / complete 엔드포인트 정의
- **C) 용량 제한**: VIDEO_MAX_UPLOAD_BYTES 초과 시 명확한 에러
- **D) Phase 34 호환성**: object_key 규칙, STORAGE_UPLOAD_FAILED 에러 처리 유지

---

## 2. 설계 결정

### 2.1 저장소 책임 분리

| 역할 | AI 서버 | Backend (Spring) |
|------|---------|------------------|
| S3 자격증명 | ❌ 없음 | ✅ 보유 |
| Presigned URL 발급 | ❌ 불가 | ✅ 가능 |
| S3 업로드 | ✅ Presigned URL 사용 | - |
| 메타데이터 저장 | ❌ 불가 | ✅ Complete 콜백으로 저장 |

### 2.2 object_key 규칙 (Phase 34 유지)

```
videos/{video_id}/{script_id}/{job_id}/video.mp4
videos/{video_id}/{script_id}/{job_id}/subtitles.srt
videos/{video_id}/{script_id}/{job_id}/thumb.jpg
```

---

## 3. 백엔드 Internal API 계약

### 3.1 Presign 발급 API

```
POST /internal/storage/presign-put
Authorization: Bearer {BACKEND_SERVICE_TOKEN}
Content-Type: application/json

Request:
{
  "object_key": "videos/V-001/S-001/JOB-123/video.mp4",
  "content_type": "video/mp4",
  "content_length": 73400320
}

Response (200 OK):
{
  "upload_url": "https://s3.ap-northeast-2.amazonaws.com/bucket/...?X-Amz-...",
  "public_url": "https://cdn.example.com/videos/V-001/S-001/JOB-123/video.mp4",
  "headers": {
    "Content-Type": "video/mp4"
  },
  "expires_sec": 600
}
```

### 3.2 업로드 완료 콜백 API

```
POST /internal/storage/complete
Authorization: Bearer {BACKEND_SERVICE_TOKEN}
Content-Type: application/json

Request:
{
  "object_key": "videos/V-001/S-001/JOB-123/video.mp4",
  "etag": "\"abcd1234...\"",
  "size_bytes": 73400320,
  "content_type": "video/mp4",
  "public_url": "https://cdn.example.com/videos/.../video.mp4"
}

Response (200 OK):
{
  "status": "ok"
}
```

### 3.3 삭제 API (선택)

```
POST /internal/storage/delete
Authorization: Bearer {BACKEND_SERVICE_TOKEN}
Content-Type: application/json

Request:
{
  "object_key": "videos/V-001/S-001/JOB-123/video.mp4"
}

Response (200 OK):
{
  "status": "ok"
}
```

---

## 4. 구현 상세

### 4.1 Settings 추가

**파일**: `app/core/config.py`

```python
# Phase 35: Backend Presigned Storage 설정
BACKEND_SERVICE_TOKEN: Optional[str] = None
BACKEND_STORAGE_PRESIGN_PATH: str = "/internal/storage/presign-put"
BACKEND_STORAGE_COMPLETE_PATH: str = "/internal/storage/complete"
VIDEO_MAX_UPLOAD_BYTES: int = 104857600  # 100MB
```

### 4.2 StorageProvider Enum 확장

**파일**: `app/clients/storage_adapter.py`

```python
class StorageProvider(str, Enum):
    LOCAL = "local"
    S3 = "s3"
    MINIO = "minio"
    BACKEND_PRESIGNED = "backend_presigned"  # Phase 35
```

### 4.3 BackendPresignedStorageProvider

```python
class BackendPresignedStorageProvider(BaseStorageProvider):
    """Backend Presigned URL 방식 Storage Provider."""

    async def put_object(self, data, key, content_type=None) -> StorageResult:
        # 1. 용량 검증
        if size_bytes > self._max_upload_bytes:
            raise StorageUploadError(f"File size exceeds limit", key)

        # 2. Presign 요청
        presign_response = await self._request_presign(
            object_key=key,
            content_type=content_type,
            content_length=size_bytes,
        )

        # 3. Presigned URL로 PUT 업로드
        etag = await self._upload_to_presigned_url(
            upload_url=presign_response["upload_url"],
            data=file_bytes,
            headers=presign_response.get("headers", {}),
        )

        # 4. Complete 콜백
        await self._notify_complete(
            object_key=key,
            etag=etag,
            size_bytes=size_bytes,
            content_type=content_type,
            public_url=presign_response["public_url"],
        )

        return StorageResult(
            key=key,
            url=presign_response["public_url"],
            size_bytes=size_bytes,
            content_type=content_type,
        )
```

---

## 5. 환경 변수

```env
# Phase 35: Backend Presigned Storage
STORAGE_PROVIDER=backend_presigned

# 백엔드 서비스 URL (AI_ENV=mock/real에 따라 자동 선택 가능)
BACKEND_BASE_URL=http://backend:8080

# 내부 API 인증 토큰
BACKEND_SERVICE_TOKEN=your-service-token-here

# API 경로 (기본값 사용 가능)
BACKEND_STORAGE_PRESIGN_PATH=/internal/storage/presign-put
BACKEND_STORAGE_COMPLETE_PATH=/internal/storage/complete

# 업로드 최대 용량 (bytes, 기본: 100MB)
VIDEO_MAX_UPLOAD_BYTES=104857600

# CDN URL (get_url에서 사용)
STORAGE_PUBLIC_BASE_URL=https://cdn.example.com
```

---

## 6. 에러 시나리오

### 6.1 용량 초과

```
StorageUploadError: Storage upload failed for 'videos/.../video.mp4':
    File size 150.5MB exceeds limit 100.0MB
```

→ RenderJobRunner에서 `STORAGE_UPLOAD_FAILED` 에러 코드로 처리

### 6.2 Presign 실패

```
StorageUploadError: Storage upload failed for 'videos/.../video.mp4':
    Presign request failed: HTTP 500
```

### 6.3 PUT 업로드 실패

```
StorageUploadError: Storage upload failed for 'https://s3...':
    Upload to presigned URL failed: HTTP 403
```

### 6.4 Complete 콜백 실패

```
StorageUploadError: Storage upload failed for 'videos/.../video.mp4':
    Complete notification failed: HTTP 500
```

---

## 7. 테스트

### 7.1 테스트 파일

| 파일 | 테스트 수 | 설명 |
|------|-----------|------|
| `test_phase35_storage_backend_presigned.py` | 18 | BackendPresignedStorageProvider 단위 테스트 |
| `test_phase34_storage_provider.py` | 19 | Phase 34 회귀 테스트 |
| `test_phase34_render_assets_persisted.py` | 8 | 에셋 영속화 회귀 테스트 |

### 7.2 주요 테스트 케이스

```python
# 전체 흐름 테스트
class TestBackendPresignedFullFlow:
    async def test_put_object_full_flow_success()  # Presign → PUT → Complete
    async def test_put_object_bytes_success()       # bytes 데이터 업로드

# 용량 제한 테스트
class TestSizeLimitValidation:
    async def test_file_exceeds_max_size_raises_error()
    async def test_bytes_exceeds_max_size_raises_error()

# 에러 처리 테스트
class TestPresignFailure:
    async def test_presign_http_error_raises_storage_error()
    async def test_presign_connection_error_raises_storage_error()

class TestPutUploadFailure:
    async def test_put_http_error_raises_storage_error()

class TestCompleteCallbackFailure:
    async def test_complete_http_error_raises_storage_error()
```

### 7.3 테스트 실행

```bash
# Phase 35 테스트만 실행
python -m pytest tests/test_phase35_storage_backend_presigned.py -v

# Phase 34 + 35 통합 테스트
python -m pytest tests/test_phase34_*.py tests/test_phase35_*.py -v
```

---

## 8. 변경된 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/core/config.py` | 수정 | Phase 35 환경변수 추가 |
| `app/clients/storage_adapter.py` | 수정 | BackendPresignedStorageProvider 추가 |
| `tests/test_phase35_storage_backend_presigned.py` | 신규 | Phase 35 테스트 (18개) |
| `docs/DEVELOPMENT_REPORT_PHASE35.md` | 신규 | 이 문서 |

---

## 9. 렌더 파이프라인 연동

### 9.1 환경변수 설정만으로 전환

```env
# 개발 환경 (로컬 저장)
STORAGE_PROVIDER=local

# 운영 환경 (Backend Presigned)
STORAGE_PROVIDER=backend_presigned
```

### 9.2 video_renderer_real.py (변경 없음)

```python
async def _upload_assets(self, ctx: RealRenderJobContext) -> None:
    storage = get_storage_provider()  # 자동으로 적절한 Provider 반환

    base_key = f"videos/{ctx.video_id}/{ctx.script_id}/{ctx.job_id}"

    # Provider에 관계없이 동일한 인터페이스 사용
    result = await storage.put_file(ctx.video_path, f"{base_key}/video.mp4")
    ctx.video_url = result.url
```

---

## 10. 다음 단계 (Phase 36+)

- [ ] Backend에 실제 presign-put / complete API 구현
- [ ] CDN 연동 (CloudFront)
- [ ] Multipart Upload 지원 (대용량 파일)
- [ ] 업로드 재시도 로직 추가
- [ ] 업로드 진행률 WebSocket 전송

---

## 11. 참고: 백엔드 구현 가이드

### 11.1 Spring Boot Presign 발급 예시

```java
@PostMapping("/internal/storage/presign-put")
public PresignResponse presignPut(@RequestBody PresignRequest request) {
    // Presigned URL 생성
    PutObjectRequest putRequest = PutObjectRequest.builder()
        .bucket(bucketName)
        .key(request.getObjectKey())
        .contentType(request.getContentType())
        .contentLength(request.getContentLength())
        .build();

    PutObjectPresignRequest presignRequest = PutObjectPresignRequest.builder()
        .putObjectRequest(putRequest)
        .signatureDuration(Duration.ofMinutes(10))
        .build();

    PresignedPutObjectRequest presignedRequest =
        s3Presigner.presignPutObject(presignRequest);

    return PresignResponse.builder()
        .uploadUrl(presignedRequest.url().toString())
        .publicUrl(cdnBaseUrl + "/" + request.getObjectKey())
        .headers(Map.of("Content-Type", request.getContentType()))
        .expiresSec(600)
        .build();
}
```

### 11.2 Complete 콜백 예시

```java
@PostMapping("/internal/storage/complete")
public void complete(@RequestBody CompleteRequest request) {
    // 메타데이터 DB 저장
    videoAssetRepository.save(VideoAsset.builder()
        .objectKey(request.getObjectKey())
        .etag(request.getEtag())
        .sizeBytes(request.getSizeBytes())
        .contentType(request.getContentType())
        .publicUrl(request.getPublicUrl())
        .createdAt(Instant.now())
        .build());
}
```
