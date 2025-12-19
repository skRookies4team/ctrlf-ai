"""
Phase 34: Storage Adapter

파일 저장 어댑터 인터페이스 및 구현체.

지원 Provider:
- local: 로컬 파일 시스템 (개발용) - FastAPI StaticFiles로 서빙 (/assets)
- s3: AWS S3 (운영용) - S3_ENDPOINT_URL로 MinIO 호환 지원

환경변수 (Phase 34):
- STORAGE_PROVIDER: local | s3 (기본: local)
- STORAGE_LOCAL_DIR: 로컬 저장 경로 (기본: ./data/assets)
- STORAGE_PUBLIC_BASE_URL: 파일 접근 기본 URL (기본: /assets)
- AWS_S3_BUCKET: S3 버킷 이름
- AWS_S3_REGION: S3 리전 (기본: ap-northeast-2)
- S3_ENDPOINT_URL: MinIO 호환 S3 엔드포인트 (선택)
- AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY: 인증
"""

import asyncio
import mimetypes
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urljoin

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class StorageUploadError(Exception):
    """Storage 업로드 실패 예외 (Phase 34)."""

    def __init__(self, message: str, key: str, original_error: Optional[Exception] = None):
        self.message = message
        self.key = key
        self.original_error = original_error
        super().__init__(f"Storage upload failed for '{key}': {message}")


# =============================================================================
# Types and Enums
# =============================================================================


class StorageProvider(str, Enum):
    """Storage Provider 종류."""
    LOCAL = "local"
    S3 = "s3"
    MINIO = "minio"  # Deprecated: S3 with S3_ENDPOINT_URL 사용 권장
    BACKEND_PRESIGNED = "backend_presigned"  # Phase 35: 백엔드 Presigned URL 방식


@dataclass
class StorageResult:
    """저장 결과."""
    key: str  # 저장된 키/경로
    url: str  # 접근 URL
    size_bytes: int  # 파일 크기
    content_type: str  # MIME 타입


@dataclass
class StorageConfig:
    """Storage 설정 (Phase 34 업데이트)."""
    provider: StorageProvider = StorageProvider.LOCAL
    # Phase 34: 기본값 변경
    local_path: str = "./data/assets"  # STORAGE_LOCAL_DIR
    base_url: str = "/assets"  # StaticFiles mount path
    # S3 설정
    s3_bucket: Optional[str] = None
    s3_region: str = "ap-northeast-2"
    s3_prefix: str = ""  # Phase 34: object_key에 전체 경로 포함
    s3_public: bool = False  # True면 public-read ACL
    s3_endpoint_url: Optional[str] = None  # Phase 34: MinIO 호환
    s3_public_base_url: Optional[str] = None  # Phase 34: Public URL 기본 경로
    # MinIO 설정 (레거시, S3 + endpoint_url 권장)
    minio_endpoint: Optional[str] = None
    minio_access_key: Optional[str] = None
    minio_secret_key: Optional[str] = None
    minio_bucket: Optional[str] = None
    minio_secure: bool = False


# =============================================================================
# Abstract Base Provider
# =============================================================================


class BaseStorageProvider(ABC):
    """Storage Provider 기본 인터페이스.

    Phase 34 Interface:
    - upload_file(local_path, object_key) -> str (object_key 반환)
    - get_url(object_key) -> str (FE가 접근 가능한 URL)
    """

    @abstractmethod
    async def put_object(
        self,
        data: Union[bytes, str, Path],
        key: str,
        content_type: Optional[str] = None,
    ) -> StorageResult:
        """객체를 저장합니다.

        Args:
            data: 저장할 데이터 (bytes, 파일 경로, 또는 Path 객체)
            key: 저장 키/경로 (예: videos/video-001/script-001/job-xxx/video.mp4)
            content_type: MIME 타입 (None이면 자동 추론)

        Returns:
            StorageResult: 저장 결과

        Raises:
            StorageUploadError: 업로드 실패 시
        """
        pass

    @abstractmethod
    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """객체 접근 URL을 반환합니다.

        Args:
            key: 객체 키
            expires_in: URL 만료 시간 (초), S3 presigned URL용

        Returns:
            str: 접근 URL
        """
        pass

    @abstractmethod
    async def delete_object(self, key: str) -> bool:
        """객체를 삭제합니다.

        Args:
            key: 객체 키

        Returns:
            bool: 삭제 성공 여부
        """
        pass

    async def put_file(
        self,
        file_path: Union[str, Path],
        key: str,
        content_type: Optional[str] = None,
    ) -> StorageResult:
        """파일을 저장합니다.

        Args:
            file_path: 저장할 파일 경로
            key: 저장 키
            content_type: MIME 타입

        Returns:
            StorageResult: 저장 결과

        Raises:
            StorageUploadError: 업로드 실패 시
        """
        return await self.put_object(Path(file_path), key, content_type)

    async def upload_file(self, local_path: str, object_key: str) -> str:
        """Phase 34 인터페이스: 파일 업로드 후 object_key 반환.

        Args:
            local_path: 로컬 파일 경로
            object_key: 저장할 키 (예: videos/video-001/script-001/job-xxx/video.mp4)

        Returns:
            str: object_key (저장된 키)

        Raises:
            StorageUploadError: 업로드 실패 시
        """
        result = await self.put_file(local_path, object_key)
        return result.key


# =============================================================================
# Local Storage Provider
# =============================================================================


class LocalStorageProvider(BaseStorageProvider):
    """로컬 파일 시스템 Storage Provider (개발용).

    파일을 로컬 디렉토리에 저장하고 StaticFiles URL을 반환합니다.
    Phase 34: FastAPI StaticFiles(/assets)로 서빙.
    """

    def __init__(self, config: Optional[StorageConfig] = None):
        self.config = config or StorageConfig()
        self._base_path = Path(self.config.local_path)
        self._base_path.mkdir(parents=True, exist_ok=True)
        self._base_url = self.config.base_url.rstrip("/")

    async def put_object(
        self,
        data: Union[bytes, str, Path],
        key: str,
        content_type: Optional[str] = None,
    ) -> StorageResult:
        """객체를 로컬에 저장."""
        try:
            # 키에서 파일 경로 생성
            file_path = self._base_path / key
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # 데이터 저장
            if isinstance(data, Path):
                # 파일 복사
                shutil.copy2(data, file_path)
                size_bytes = data.stat().st_size
            elif isinstance(data, str):
                # 문자열 경로
                src_path = Path(data)
                shutil.copy2(src_path, file_path)
                size_bytes = src_path.stat().st_size
            else:
                # bytes
                file_path.write_bytes(data)
                size_bytes = len(data)

            # Content-Type 추론
            if content_type is None:
                content_type, _ = mimetypes.guess_type(str(file_path))
                content_type = content_type or "application/octet-stream"

            # URL 생성 (StaticFiles 경로)
            url = f"{self._base_url}/{key}"

            logger.info(f"Local storage: saved {key}, size={size_bytes}, url={url}")

            return StorageResult(
                key=key,
                url=url,
                size_bytes=size_bytes,
                content_type=content_type,
            )
        except Exception as e:
            logger.error(f"Local storage upload failed: {key}, error={e}")
            raise StorageUploadError(str(e), key, e)

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """로컬 파일 URL 반환 (StaticFiles 경로)."""
        return f"{self._base_url}/{key}"

    async def delete_object(self, key: str) -> bool:
        """로컬 파일 삭제."""
        file_path = self._base_path / key
        if file_path.exists():
            file_path.unlink()
            logger.info(f"Local storage: deleted {key}")
            return True
        return False


# =============================================================================
# S3 Storage Provider
# =============================================================================


class S3StorageProvider(BaseStorageProvider):
    """AWS S3 Storage Provider.

    Phase 34 업데이트:
    - S3_ENDPOINT_URL 지원 (MinIO 호환)
    - STORAGE_PUBLIC_BASE_URL 지원 (퍼블릭 URL 구성)

    환경변수:
    - AWS_S3_BUCKET: 버킷 이름
    - AWS_S3_REGION: 리전 (기본: ap-northeast-2)
    - S3_ENDPOINT_URL: MinIO 호환 엔드포인트 (선택)
    - STORAGE_PUBLIC_BASE_URL: 퍼블릭 URL 기본 경로 (선택)
    - AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY: 인증
    """

    def __init__(self, config: Optional[StorageConfig] = None):
        self.config = config or StorageConfig()
        self._client = None
        self._bucket = self.config.s3_bucket or os.getenv("AWS_S3_BUCKET")
        self._region = self.config.s3_region or os.getenv("AWS_S3_REGION", "ap-northeast-2")
        self._prefix = self.config.s3_prefix
        self._endpoint_url = self.config.s3_endpoint_url or os.getenv("S3_ENDPOINT_URL")
        self._public_base_url = self.config.s3_public_base_url or os.getenv("STORAGE_PUBLIC_BASE_URL")

        if not self._bucket:
            raise ValueError("S3 bucket not configured. Set AWS_S3_BUCKET.")

    def _get_client(self):
        """Boto3 S3 클라이언트 반환."""
        if self._client is None:
            try:
                import boto3
            except ImportError:
                raise RuntimeError("boto3 not installed. Run: pip install boto3")

            # Phase 34: endpoint_url 지원 (MinIO 호환)
            client_kwargs = {"region_name": self._region}
            if self._endpoint_url:
                client_kwargs["endpoint_url"] = self._endpoint_url
                logger.info(f"S3 using custom endpoint: {self._endpoint_url}")

            self._client = boto3.client("s3", **client_kwargs)
        return self._client

    async def put_object(
        self,
        data: Union[bytes, str, Path],
        key: str,
        content_type: Optional[str] = None,
    ) -> StorageResult:
        """객체를 S3에 저장."""
        try:
            client = self._get_client()
            full_key = f"{self._prefix}{key}" if self._prefix else key

            # 데이터 준비
            if isinstance(data, Path):
                body = data.read_bytes()
                size_bytes = len(body)
            elif isinstance(data, str):
                body = Path(data).read_bytes()
                size_bytes = len(body)
            else:
                body = data
                size_bytes = len(data)

            # Content-Type 추론
            if content_type is None:
                content_type, _ = mimetypes.guess_type(key)
                content_type = content_type or "application/octet-stream"

            loop = asyncio.get_event_loop()

            def _upload():
                extra_args = {"ContentType": content_type}
                if self.config.s3_public:
                    extra_args["ACL"] = "public-read"

                client.put_object(
                    Bucket=self._bucket,
                    Key=full_key,
                    Body=body,
                    **extra_args,
                )

            await loop.run_in_executor(None, _upload)

            # URL 생성
            url = await self.get_url(key)

            logger.info(f"S3 storage: uploaded {full_key}, size={size_bytes}")

            return StorageResult(
                key=full_key,
                url=url,
                size_bytes=size_bytes,
                content_type=content_type,
            )
        except Exception as e:
            logger.error(f"S3 storage upload failed: {key}, error={e}")
            raise StorageUploadError(str(e), key, e)

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """URL 생성.

        Phase 34: public_base_url이 설정되면 직접 URL 구성,
        아니면 presigned URL 생성.
        """
        full_key = f"{self._prefix}{key}" if self._prefix else key

        # Phase 34: public base URL이 있으면 직접 구성
        if self._public_base_url:
            base = self._public_base_url.rstrip("/")
            return f"{base}/{full_key}"

        # 기본: presigned URL
        client = self._get_client()
        loop = asyncio.get_event_loop()

        def _generate_url():
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": full_key},
                ExpiresIn=expires_in,
            )

        return await loop.run_in_executor(None, _generate_url)

    async def delete_object(self, key: str) -> bool:
        """S3 객체 삭제."""
        client = self._get_client()
        full_key = f"{self._prefix}{key}" if self._prefix else key

        loop = asyncio.get_event_loop()

        def _delete():
            client.delete_object(Bucket=self._bucket, Key=full_key)

        try:
            await loop.run_in_executor(None, _delete)
            logger.info(f"S3 storage: deleted {full_key}")
            return True
        except Exception as e:
            logger.error(f"S3 delete failed: {e}")
            return False


# =============================================================================
# MinIO Storage Provider (레거시, S3 + endpoint_url 권장)
# =============================================================================


class MinIOStorageProvider(BaseStorageProvider):
    """MinIO Storage Provider (S3 호환).

    Note: Phase 34부터는 S3StorageProvider + S3_ENDPOINT_URL 사용 권장.

    환경변수:
    - MINIO_ENDPOINT: MinIO 서버 엔드포인트
    - MINIO_ACCESS_KEY: Access Key
    - MINIO_SECRET_KEY: Secret Key
    - MINIO_BUCKET: 버킷 이름
    """

    def __init__(self, config: Optional[StorageConfig] = None):
        self.config = config or StorageConfig()
        self._client = None
        self._endpoint = self.config.minio_endpoint or os.getenv("MINIO_ENDPOINT")
        self._access_key = self.config.minio_access_key or os.getenv("MINIO_ACCESS_KEY")
        self._secret_key = self.config.minio_secret_key or os.getenv("MINIO_SECRET_KEY")
        self._bucket = self.config.minio_bucket or os.getenv("MINIO_BUCKET", "videos")
        self._secure = self.config.minio_secure

        if not self._endpoint:
            raise ValueError("MinIO endpoint not configured. Set MINIO_ENDPOINT.")

    def _get_client(self):
        """MinIO 클라이언트 반환."""
        if self._client is None:
            try:
                from minio import Minio
            except ImportError:
                raise RuntimeError("minio not installed. Run: pip install minio")

            self._client = Minio(
                self._endpoint,
                access_key=self._access_key,
                secret_key=self._secret_key,
                secure=self._secure,
            )

            # 버킷 생성 (없으면)
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info(f"MinIO: created bucket {self._bucket}")

        return self._client

    async def put_object(
        self,
        data: Union[bytes, str, Path],
        key: str,
        content_type: Optional[str] = None,
    ) -> StorageResult:
        """객체를 MinIO에 저장."""
        try:
            client = self._get_client()

            # Content-Type 추론
            if content_type is None:
                content_type, _ = mimetypes.guess_type(key)
                content_type = content_type or "application/octet-stream"

            loop = asyncio.get_event_loop()

            if isinstance(data, (Path, str)):
                file_path = Path(data)
                size_bytes = file_path.stat().st_size

                def _upload():
                    client.fput_object(
                        self._bucket,
                        key,
                        str(file_path),
                        content_type=content_type,
                    )

                await loop.run_in_executor(None, _upload)
            else:
                size_bytes = len(data)
                import io

                def _upload():
                    client.put_object(
                        self._bucket,
                        key,
                        io.BytesIO(data),
                        length=size_bytes,
                        content_type=content_type,
                    )

                await loop.run_in_executor(None, _upload)

            # URL 생성
            url = await self.get_url(key)

            logger.info(f"MinIO storage: uploaded {key}, size={size_bytes}")

            return StorageResult(
                key=key,
                url=url,
                size_bytes=size_bytes,
                content_type=content_type,
            )
        except Exception as e:
            logger.error(f"MinIO storage upload failed: {key}, error={e}")
            raise StorageUploadError(str(e), key, e)

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """Presigned URL 생성."""
        client = self._get_client()
        from datetime import timedelta

        loop = asyncio.get_event_loop()

        def _generate_url():
            return client.presigned_get_object(
                self._bucket,
                key,
                expires=timedelta(seconds=expires_in),
            )

        return await loop.run_in_executor(None, _generate_url)

    async def delete_object(self, key: str) -> bool:
        """MinIO 객체 삭제."""
        client = self._get_client()

        loop = asyncio.get_event_loop()

        def _delete():
            client.remove_object(self._bucket, key)

        try:
            await loop.run_in_executor(None, _delete)
            logger.info(f"MinIO storage: deleted {key}")
            return True
        except Exception as e:
            logger.error(f"MinIO delete failed: {e}")
            return False


# =============================================================================
# Backend Presigned Storage Provider (Phase 35)
# =============================================================================


class BackendPresignedStorageProvider(BaseStorageProvider):
    """Backend Presigned URL 방식 Storage Provider (Phase 35).

    AI 서버는 AWS 자격증명 없이 백엔드가 발급한 Presigned URL로 S3에 업로드.
    최소권한 원칙: AI 서버는 S3 직접 접근 불가, 백엔드 Internal API만 호출.

    흐름:
    1. put_object 호출 시 파일 크기 확인 (VIDEO_MAX_UPLOAD_BYTES 초과 시 에러)
    2. 백엔드 /internal/storage/presign-put 호출 → upload_url, public_url 수신
    3. httpx로 PUT upload_url 실행 (Presigned URL로 S3에 직접 업로드)
    4. 업로드 응답 헤더에서 ETag 수집
    5. 백엔드 /internal/storage/complete 호출 → 메타데이터 저장 완료
    6. StorageResult(url=public_url, key=key, ...) 반환

    환경변수:
    - BACKEND_BASE_URL: 백엔드 서비스 URL
    - BACKEND_SERVICE_TOKEN: 내부 API 인증 토큰
    - BACKEND_STORAGE_PRESIGN_PATH: Presign 발급 API 경로
    - BACKEND_STORAGE_COMPLETE_PATH: 완료 콜백 API 경로
    - VIDEO_MAX_UPLOAD_BYTES: 업로드 최대 용량 (기본 100MB)
    """

    def __init__(self):
        from app.core.config import get_settings
        settings = get_settings()

        self._backend_base_url = settings.backend_base_url
        self._service_token = settings.BACKEND_SERVICE_TOKEN
        self._presign_path = settings.BACKEND_STORAGE_PRESIGN_PATH
        self._complete_path = settings.BACKEND_STORAGE_COMPLETE_PATH
        self._max_upload_bytes = settings.VIDEO_MAX_UPLOAD_BYTES
        self._public_base_url = settings.storage_public_base_url

        if not self._backend_base_url:
            raise ValueError(
                "Backend base URL not configured. "
                "Set BACKEND_BASE_URL or BACKEND_BASE_URL_MOCK/REAL."
            )

    def _get_headers(self) -> dict:
        """Internal API 요청 헤더 반환."""
        headers = {"Content-Type": "application/json"}
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        return headers

    async def _request_presign(
        self,
        object_key: str,
        content_type: str,
        content_length: int,
    ) -> dict:
        """백엔드에 Presigned URL 발급 요청.

        Args:
            object_key: 저장할 객체 키
            content_type: MIME 타입
            content_length: 파일 크기 (bytes)

        Returns:
            dict: {upload_url, public_url, headers, expires_sec}

        Raises:
            StorageUploadError: Presign 발급 실패 시
        """
        import httpx

        url = f"{self._backend_base_url}{self._presign_path}"
        payload = {
            "object_key": object_key,
            "content_type": content_type,
            "content_length": content_length,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Presign request failed: {e.response.status_code} - {e.response.text}")
            raise StorageUploadError(
                f"Presign request failed: HTTP {e.response.status_code}",
                object_key,
                e,
            )
        except Exception as e:
            logger.error(f"Presign request error: {e}")
            raise StorageUploadError(f"Presign request error: {e}", object_key, e)

    async def _upload_to_presigned_url(
        self,
        upload_url: str,
        data: bytes,
        headers: dict,
    ) -> str:
        """Presigned URL로 파일 업로드.

        Args:
            upload_url: Presigned PUT URL
            data: 업로드할 데이터
            headers: 업로드 시 사용할 헤더 (Content-Type 등)

        Returns:
            str: ETag 값 (따옴표 포함)

        Raises:
            StorageUploadError: 업로드 실패 시
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.put(
                    upload_url,
                    content=data,
                    headers=headers,
                )
                response.raise_for_status()

                # ETag 추출 (S3 응답 헤더)
                etag = response.headers.get("ETag", "")
                logger.info(f"Upload to presigned URL succeeded, ETag: {etag}")
                return etag
        except httpx.HTTPStatusError as e:
            logger.error(f"Presigned upload failed: {e.response.status_code}")
            raise StorageUploadError(
                f"Upload to presigned URL failed: HTTP {e.response.status_code}",
                upload_url,
                e,
            )
        except Exception as e:
            logger.error(f"Presigned upload error: {e}")
            raise StorageUploadError(f"Upload error: {e}", upload_url, e)

    async def _notify_complete(
        self,
        object_key: str,
        etag: str,
        size_bytes: int,
        content_type: str,
        public_url: str,
    ) -> None:
        """백엔드에 업로드 완료 알림.

        Args:
            object_key: 저장된 객체 키
            etag: S3 ETag 값
            size_bytes: 파일 크기
            content_type: MIME 타입
            public_url: 퍼블릭 접근 URL

        Raises:
            StorageUploadError: 완료 알림 실패 시
        """
        import httpx

        url = f"{self._backend_base_url}{self._complete_path}"
        payload = {
            "object_key": object_key,
            "etag": etag,
            "size_bytes": size_bytes,
            "content_type": content_type,
            "public_url": public_url,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                logger.info(f"Upload complete notification sent for: {object_key}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Complete notification failed: {e.response.status_code}")
            raise StorageUploadError(
                f"Complete notification failed: HTTP {e.response.status_code}",
                object_key,
                e,
            )
        except Exception as e:
            logger.error(f"Complete notification error: {e}")
            raise StorageUploadError(f"Complete notification error: {e}", object_key, e)

    async def put_object(
        self,
        data: Union[bytes, str, Path],
        key: str,
        content_type: Optional[str] = None,
    ) -> StorageResult:
        """객체를 Backend Presigned URL 방식으로 S3에 저장.

        Phase 35 핵심 로직:
        1. 파일 크기 검증 (VIDEO_MAX_UPLOAD_BYTES 초과 시 에러)
        2. Presign 요청 → upload_url 수신
        3. Presigned URL로 PUT 업로드
        4. Complete 콜백 호출
        5. StorageResult 반환
        """
        try:
            # 데이터 준비 및 크기 확인
            if isinstance(data, Path):
                if not data.exists():
                    raise StorageUploadError(f"File not found: {data}", key)
                file_bytes = data.read_bytes()
                size_bytes = len(file_bytes)
            elif isinstance(data, str):
                path = Path(data)
                if not path.exists():
                    raise StorageUploadError(f"File not found: {data}", key)
                file_bytes = path.read_bytes()
                size_bytes = len(file_bytes)
            else:
                file_bytes = data
                size_bytes = len(data)

            # 용량 제한 검증
            if size_bytes > self._max_upload_bytes:
                max_mb = self._max_upload_bytes / (1024 * 1024)
                actual_mb = size_bytes / (1024 * 1024)
                raise StorageUploadError(
                    f"File size {actual_mb:.1f}MB exceeds limit {max_mb:.1f}MB",
                    key,
                )

            # Content-Type 추론
            if content_type is None:
                content_type, _ = mimetypes.guess_type(key)
                content_type = content_type or "application/octet-stream"

            # 1. Presign 요청
            presign_response = await self._request_presign(
                object_key=key,
                content_type=content_type,
                content_length=size_bytes,
            )

            upload_url = presign_response["upload_url"]
            public_url = presign_response["public_url"]
            upload_headers = presign_response.get("headers", {})

            # Content-Type 헤더 추가
            if "Content-Type" not in upload_headers:
                upload_headers["Content-Type"] = content_type

            # 2. Presigned URL로 업로드
            etag = await self._upload_to_presigned_url(
                upload_url=upload_url,
                data=file_bytes,
                headers=upload_headers,
            )

            # 3. Complete 콜백
            await self._notify_complete(
                object_key=key,
                etag=etag,
                size_bytes=size_bytes,
                content_type=content_type,
                public_url=public_url,
            )

            logger.info(
                f"Backend presigned upload succeeded: {key}, "
                f"size={size_bytes}, url={public_url}"
            )

            return StorageResult(
                key=key,
                url=public_url,
                size_bytes=size_bytes,
                content_type=content_type,
            )

        except StorageUploadError:
            raise
        except Exception as e:
            logger.error(f"Backend presigned upload failed: {key}, error={e}")
            raise StorageUploadError(str(e), key, e)

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """객체 URL 반환.

        Phase 35 MVP: storage_public_base_url + "/" + key 형태로 구성.
        향후 백엔드 API로 조회하도록 확장 가능.
        """
        base = self._public_base_url.rstrip("/") if self._public_base_url else ""
        return f"{base}/{key}"

    async def delete_object(self, key: str) -> bool:
        """객체 삭제 (백엔드에 위임).

        Phase 35 MVP: 백엔드 /internal/storage/delete API 호출.
        AI 서버는 S3 delete 권한이 없으므로 백엔드에 위임.

        TODO: 백엔드에 delete API 구현 후 연동
        """
        import httpx

        # TODO: 백엔드 delete API 경로 설정 추가 필요
        delete_path = "/internal/storage/delete"
        url = f"{self._backend_base_url}{delete_path}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json={"object_key": key},
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                logger.info(f"Backend presigned storage: deleted {key}")
                return True
        except Exception as e:
            logger.error(f"Backend presigned delete failed: {key}, error={e}")
            return False


# =============================================================================
# Factory Function
# =============================================================================


def get_storage_provider(
    provider: Optional[StorageProvider] = None,
    config: Optional[StorageConfig] = None,
) -> BaseStorageProvider:
    """Storage Provider 인스턴스를 반환합니다.

    Args:
        provider: Provider 종류. None이면 환경변수 STORAGE_PROVIDER 사용.
        config: Storage 설정.

    Returns:
        BaseStorageProvider: Storage Provider 인스턴스

    환경변수:
        Phase 34:
        - STORAGE_PROVIDER: local | s3 | backend_presigned
        - STORAGE_LOCAL_DIR: 로컬 저장 경로 (기본: ./data/assets)
        - STORAGE_PUBLIC_BASE_URL: 퍼블릭 URL 기본 경로 (기본: /assets)

        Phase 35 (backend_presigned):
        - BACKEND_BASE_URL: 백엔드 서비스 URL
        - BACKEND_SERVICE_TOKEN: 내부 API 인증 토큰
        - BACKEND_STORAGE_PRESIGN_PATH: Presign 발급 API 경로
        - BACKEND_STORAGE_COMPLETE_PATH: 완료 콜백 API 경로
        - VIDEO_MAX_UPLOAD_BYTES: 업로드 최대 용량
    """
    if provider is None:
        provider_str = os.getenv("STORAGE_PROVIDER", "local").lower()
        try:
            provider = StorageProvider(provider_str)
        except ValueError:
            logger.warning(f"Unknown STORAGE_PROVIDER: {provider_str}, using local")
            provider = StorageProvider.LOCAL

    # Phase 34: Config 구성 (settings 사용)
    if config is None:
        from app.core.config import get_settings
        settings = get_settings()

        config = StorageConfig(
            provider=provider,
            # Phase 34: STORAGE_LOCAL_DIR 사용
            local_path=settings.STORAGE_LOCAL_DIR,
            # Phase 34: storage_public_base_url 프로퍼티 사용
            base_url=settings.storage_public_base_url,
            s3_bucket=os.getenv("AWS_S3_BUCKET"),
            s3_region=os.getenv("AWS_S3_REGION", "ap-northeast-2"),
            s3_prefix=settings.AWS_S3_PREFIX,
            s3_endpoint_url=settings.S3_ENDPOINT_URL,
            s3_public_base_url=settings.STORAGE_PUBLIC_BASE_URL,
            minio_endpoint=os.getenv("MINIO_ENDPOINT"),
            minio_access_key=os.getenv("MINIO_ACCESS_KEY"),
            minio_secret_key=os.getenv("MINIO_SECRET_KEY"),
            minio_bucket=os.getenv("MINIO_BUCKET", "videos"),
        )

    if provider == StorageProvider.LOCAL:
        return LocalStorageProvider(config)
    elif provider == StorageProvider.S3:
        return S3StorageProvider(config)
    elif provider == StorageProvider.MINIO:
        return MinIOStorageProvider(config)
    elif provider == StorageProvider.BACKEND_PRESIGNED:
        # Phase 35: BackendPresignedStorageProvider는 자체적으로 settings에서 설정을 읽음
        return BackendPresignedStorageProvider()
    else:
        logger.warning(f"Unknown provider: {provider}, using local")
        return LocalStorageProvider(config)


# =============================================================================
# Singleton for default provider
# =============================================================================


_default_storage: Optional[BaseStorageProvider] = None


def get_default_storage_provider() -> BaseStorageProvider:
    """기본 Storage Provider 싱글톤 인스턴스 반환."""
    global _default_storage
    if _default_storage is None:
        _default_storage = get_storage_provider()
    return _default_storage


def clear_storage_provider() -> None:
    """Storage Provider 싱글톤 초기화 (테스트용)."""
    global _default_storage
    _default_storage = None
