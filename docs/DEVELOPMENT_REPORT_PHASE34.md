# Phase 34: Render Assets Storage 영속화 + Published URL 안정화

**작성일**: 2025-12-19
**Phase**: 34
**상태**: 완료
**테스트 결과**: 52 passed (Phase 32 + Phase 34 통합)

---

## 1. 개요

Phase 34에서는 렌더 산출물(mp4/srt/jpg)의 영구 저장소(Storage) 연동과 Published URL 안정화를 구현했습니다.

### 1.1 목표

- **A) StorageProvider 어댑터 패턴**: 로컬/S3 스토리지 추상화
- **B) LocalStorageProvider**: FastAPI StaticFiles로 에셋 서빙
- **C) S3StorageProvider**: MinIO 호환 S3 엔드포인트 지원
- **D) object_key 규칙**: `videos/{video_id}/{script_id}/{job_id}/filename`
- **E) STORAGE_UPLOAD_FAILED 에러 처리**: 업로드 실패 시 명확한 에러 코드

---

## 2. 구현 상세

### 2.1 StorageProvider 인터페이스

**파일**: `app/clients/storage_adapter.py`

```python
class BaseStorageProvider(ABC):
    """스토리지 추상 인터페이스."""

    @abstractmethod
    async def put_object(
        self,
        data: bytes | Path | str,
        key: str,
        content_type: Optional[str] = None
    ) -> StorageResult:
        """오브젝트 업로드."""
        pass

    @abstractmethod
    async def get_url(self, key: str, expires: int = 3600) -> str:
        """오브젝트 URL 반환."""
        pass

    @abstractmethod
    async def delete_object(self, key: str) -> bool:
        """오브젝트 삭제."""
        pass

    async def upload_file(self, local_path: str, object_key: str) -> str:
        """파일 업로드 후 key 반환 (Phase 34 인터페이스)."""
        await self.put_object(local_path, object_key)
        return object_key
```

### 2.2 LocalStorageProvider

```python
class LocalStorageProvider(BaseStorageProvider):
    """로컬 파일시스템 스토리지."""

    def __init__(self, config: StorageConfig):
        self._base_path = Path(config.local_path)
        self._base_url = config.base_url  # "/assets"
        self._base_path.mkdir(parents=True, exist_ok=True)

    async def put_object(self, data, key, content_type=None):
        target = self._base_path / key
        target.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, bytes):
            target.write_bytes(data)
        elif isinstance(data, (Path, str)):
            source = Path(data)
            if not source.exists():
                raise StorageUploadError(f"File not found: {source}", key)
            shutil.copy2(source, target)

        return StorageResult(
            key=key,
            url=f"{self._base_url}/{key}",
            size_bytes=target.stat().st_size,
            content_type=content_type or mimetypes.guess_type(key)[0],
        )

    async def get_url(self, key, expires=3600):
        return f"{self._base_url}/{key}"
```

### 2.3 S3StorageProvider (MinIO 호환)

```python
class S3StorageProvider(BaseStorageProvider):
    """S3/MinIO 스토리지."""

    def __init__(self, config: StorageConfig):
        self._bucket = config.s3_bucket
        self._endpoint_url = config.s3_endpoint_url  # MinIO용
        self._public_base_url = config.s3_public_base_url

        # boto3 클라이언트 (endpoint_url로 MinIO 지원)
        self._client = boto3.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=config.s3_access_key,
            aws_secret_access_key=config.s3_secret_key,
        )
```

### 2.4 StorageUploadError 예외

**파일**: `app/clients/storage_adapter.py`

```python
class StorageUploadError(Exception):
    """스토리지 업로드 실패 예외."""

    def __init__(
        self,
        message: str,
        key: str,
        original_error: Optional[Exception] = None
    ):
        self.message = message
        self.key = key
        self.original_error = original_error
        super().__init__(f"Storage upload failed for '{key}': {message}")
```

### 2.5 object_key 규칙

**파일**: `app/services/video_renderer_real.py`

```python
async def _upload_assets(self, ctx: RealRenderJobContext) -> None:
    """Phase 34: object_key 규칙 적용."""
    storage = get_storage_provider()

    # 규칙: videos/{video_id}/{script_id}/{job_id}/filename
    base_key = f"videos/{ctx.video_id}/{ctx.script_id}/{ctx.job_id}"

    # video.mp4
    video_result = await storage.put_file(
        ctx.video_path,
        f"{base_key}/video.mp4",
        "video/mp4"
    )
    ctx.video_url = video_result.url

    # subtitles.srt
    subtitle_result = await storage.put_file(
        ctx.subtitle_path,
        f"{base_key}/subtitles.srt",
        "application/x-subrip"
    )
    ctx.subtitle_url = subtitle_result.url

    # thumb.jpg
    thumb_result = await storage.put_file(
        ctx.thumbnail_path,
        f"{base_key}/thumb.jpg",
        "image/jpeg"
    )
    ctx.thumbnail_url = thumb_result.url
```

### 2.6 RenderJobRunner 에러 처리

**파일**: `app/services/render_job_runner.py`

```python
async def _run_render(self, job_id: str, script: VideoScript):
    try:
        # 렌더링 실행...
        await renderer.execute_step(ctx, step)

    except StorageUploadError as e:
        # Phase 34: 스토리지 업로드 실패 시 명확한 에러 코드
        error_code = "STORAGE_UPLOAD_FAILED"
        error_message = f"Storage upload failed for key '{e.key}': {e.message}"
        self._repository.update_error(
            job_id=job_id,
            error_code=error_code,
            error_message=error_message[:500]
        )

    except Exception as e:
        error_code = type(e).__name__
        error_message = str(e)[:500]
        self._repository.update_error(...)
```

### 2.7 FastAPI StaticFiles 마운트

**파일**: `app/main.py`

```python
from fastapi.staticfiles import StaticFiles

# Phase 34: Static Files for rendered assets
_assets_dir = Path(settings.STORAGE_LOCAL_DIR)
_assets_dir.mkdir(parents=True, exist_ok=True)

app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")
```

---

## 3. 설정 (config.py)

**파일**: `app/core/config.py`

```python
class Settings(BaseSettings):
    # Phase 34: Storage Settings
    STORAGE_PROVIDER: str = "local"  # "local" | "s3"
    STORAGE_LOCAL_DIR: str = "./data/assets"

    # S3/MinIO 설정
    AWS_S3_BUCKET: Optional[str] = None
    AWS_S3_REGION: str = "ap-northeast-2"
    S3_ENDPOINT_URL: Optional[str] = None  # MinIO용: http://minio:9000
    S3_PUBLIC_BASE_URL: Optional[str] = None

    @property
    def storage_public_base_url(self) -> str:
        """스토리지 공개 URL 베이스."""
        if self.STORAGE_PROVIDER == "s3" and self.S3_PUBLIC_BASE_URL:
            return self.S3_PUBLIC_BASE_URL
        return "/assets"
```

---

## 4. 환경 변수

```env
# Phase 34: Storage Configuration
STORAGE_PROVIDER=local
STORAGE_LOCAL_DIR=./data/assets

# S3/MinIO (선택)
AWS_S3_BUCKET=ctrlf-assets
AWS_S3_REGION=ap-northeast-2
S3_ENDPOINT_URL=http://minio:9000
S3_PUBLIC_BASE_URL=http://localhost:9000/ctrlf-assets
```

---

## 5. URL 형식

### 5.1 로컬 스토리지

```
GET /assets/videos/{video_id}/{script_id}/{job_id}/video.mp4
GET /assets/videos/{video_id}/{script_id}/{job_id}/subtitles.srt
GET /assets/videos/{video_id}/{script_id}/{job_id}/thumb.jpg
```

### 5.2 S3/MinIO

```
https://bucket.s3.region.amazonaws.com/videos/{video_id}/{script_id}/{job_id}/video.mp4

# MinIO (self-hosted)
http://minio:9000/bucket/videos/{video_id}/{script_id}/{job_id}/video.mp4
```

---

## 6. 테스트

### 6.1 테스트 파일

| 파일 | 테스트 수 | 설명 |
|------|-----------|------|
| `test_phase34_storage_provider.py` | 19 | LocalStorageProvider 단위 테스트 |
| `test_phase34_render_assets_persisted.py` | 8 | 에셋 영속화 + 에러 처리 |
| `test_phase32_video_rendering.py` | 25 | Phase 32 회귀 테스트 (script_id 추가) |

### 6.2 주요 테스트 케이스

```python
# StorageProvider 테스트
class TestLocalStorageProvider:
    async def test_put_object_bytes()      # bytes 업로드
    async def test_put_object_path()       # Path 업로드
    async def test_put_object_str_path()   # 문자열 경로 업로드
    async def test_get_url()               # URL 생성
    async def test_delete_object()         # 삭제
    async def test_nested_directory_creation()  # 중첩 디렉토리

# object_key 규칙 테스트
class TestObjectKeyConvention:
    async def test_phase34_key_format()  # videos/{video_id}/{script_id}/{job_id}/...

# 에러 처리 테스트
class TestStorageUploadFailed:
    async def test_storage_upload_error_sets_correct_error_code()
    async def test_generic_error_uses_exception_name()
```

### 6.3 테스트 실행

```bash
# Phase 34 테스트만 실행
python -m pytest tests/test_phase34_*.py -v

# Phase 32 + 34 통합 테스트
python -m pytest tests/test_phase32_video_rendering.py tests/test_phase34_*.py -v
```

---

## 7. 변경된 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|-----------|------|
| `app/clients/storage_adapter.py` | 수정 | StorageUploadError, S3_ENDPOINT_URL, upload_file() |
| `app/services/video_renderer_real.py` | 수정 | script_id 컨텍스트, object_key 규칙 |
| `app/services/render_job_runner.py` | 수정 | StorageUploadError 처리 |
| `app/main.py` | 수정 | StaticFiles 마운트 |
| `app/core/config.py` | 기존 | STORAGE_LOCAL_DIR (Phase 33에서 추가) |
| `tests/test_phase34_storage_provider.py` | 신규 | 스토리지 프로바이더 테스트 |
| `tests/test_phase34_render_assets_persisted.py` | 신규 | 에셋 영속화 테스트 |
| `tests/test_phase32_video_rendering.py` | 수정 | script_id 추가 (호환성) |

---

## 8. API 응답 예시

### 8.1 렌더 완료 후 에셋 조회

```json
GET /api/v2/videos/{video_id}/assets/published

{
  "video_id": "video-abc123",
  "job_id": "job-xyz789",
  "assets": {
    "video_url": "/assets/videos/video-abc123/script-def456/job-xyz789/video.mp4",
    "subtitle_url": "/assets/videos/video-abc123/script-def456/job-xyz789/subtitles.srt",
    "thumbnail_url": "/assets/videos/video-abc123/script-def456/job-xyz789/thumb.jpg"
  },
  "status": "SUCCEEDED",
  "finished_at": "2025-12-19T15:30:00Z"
}
```

### 8.2 업로드 실패 시 에러

```json
GET /api/v2/videos/{video_id}/render-jobs/{job_id}

{
  "job_id": "job-xyz789",
  "status": "FAILED",
  "error_code": "STORAGE_UPLOAD_FAILED",
  "error_message": "Storage upload failed for key 'videos/.../video.mp4': disk full"
}
```

---

## 9. 다음 단계 (Phase 35+)

- [ ] S3StorageProvider 실제 구현 완성 (현재 stub)
- [ ] MinIO 통합 테스트 (docker-compose)
- [ ] Presigned URL 지원 (비공개 버킷용)
- [ ] CDN 연동 (CloudFront/Cloudflare)
- [ ] 에셋 정리 스케줄러 (오래된 렌더 결과 삭제)

---

## 10. 참고사항

### 10.1 MinIO 로컬 개발 환경

```yaml
# docker-compose.yml
services:
  minio:
    image: minio/minio
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    command: server /data --console-address ":9001"
```

### 10.2 환경변수 예시 (MinIO)

```env
STORAGE_PROVIDER=s3
AWS_S3_BUCKET=ctrlf-assets
S3_ENDPOINT_URL=http://localhost:9000
S3_PUBLIC_BASE_URL=http://localhost:9000/ctrlf-assets
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
```
