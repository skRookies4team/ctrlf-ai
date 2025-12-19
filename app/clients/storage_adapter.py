"""
Phase 32: Storage Adapter

파일 저장 어댑터 인터페이스 및 구현체.

지원 Provider:
- local: 로컬 파일 시스템 (개발용)
- s3: AWS S3 (운영용)
- minio: MinIO (S3 호환, 자체 호스팅)

환경변수:
- STORAGE_PROVIDER: local | s3 | minio (기본: local)
- STORAGE_LOCAL_PATH: 로컬 저장 경로 (기본: ./video_output)
- STORAGE_BASE_URL: 파일 접근 기본 URL (로컬 모드용)
- AWS_S3_BUCKET: S3 버킷 이름
- AWS_S3_REGION: S3 리전
- AWS_S3_PREFIX: S3 키 프리픽스 (예: videos/)
- MINIO_ENDPOINT: MinIO 엔드포인트
- MINIO_ACCESS_KEY, MINIO_SECRET_KEY: MinIO 인증
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
# Types and Enums
# =============================================================================


class StorageProvider(str, Enum):
    """Storage Provider 종류."""
    LOCAL = "local"
    S3 = "s3"
    MINIO = "minio"


@dataclass
class StorageResult:
    """저장 결과."""
    key: str  # 저장된 키/경로
    url: str  # 접근 URL
    size_bytes: int  # 파일 크기
    content_type: str  # MIME 타입


@dataclass
class StorageConfig:
    """Storage 설정."""
    provider: StorageProvider = StorageProvider.LOCAL
    local_path: str = "./video_output"
    base_url: str = "http://localhost:8000/static/videos"
    # S3 설정
    s3_bucket: Optional[str] = None
    s3_region: str = "ap-northeast-2"
    s3_prefix: str = "videos/"
    s3_public: bool = False  # True면 public-read ACL
    # MinIO 설정
    minio_endpoint: Optional[str] = None
    minio_access_key: Optional[str] = None
    minio_secret_key: Optional[str] = None
    minio_bucket: Optional[str] = None
    minio_secure: bool = False


# =============================================================================
# Abstract Base Provider
# =============================================================================


class BaseStorageProvider(ABC):
    """Storage Provider 기본 인터페이스."""

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
            key: 저장 키/경로 (예: job-123/video.mp4)
            content_type: MIME 타입 (None이면 자동 추론)

        Returns:
            StorageResult: 저장 결과
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
        """
        return await self.put_object(Path(file_path), key, content_type)


# =============================================================================
# Local Storage Provider
# =============================================================================


class LocalStorageProvider(BaseStorageProvider):
    """로컬 파일 시스템 Storage Provider (개발용).

    파일을 로컬 디렉토리에 저장하고 URL을 반환합니다.
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

        # URL 생성
        url = f"{self._base_url}/{key}"

        logger.info(f"Local storage: saved {key}, size={size_bytes}, url={url}")

        return StorageResult(
            key=key,
            url=url,
            size_bytes=size_bytes,
            content_type=content_type,
        )

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """로컬 파일 URL 반환."""
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

    환경변수:
    - AWS_S3_BUCKET: 버킷 이름
    - AWS_S3_REGION: 리전 (기본: ap-northeast-2)
    - AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY: 인증
    """

    def __init__(self, config: Optional[StorageConfig] = None):
        self.config = config or StorageConfig()
        self._client = None
        self._bucket = self.config.s3_bucket or os.getenv("AWS_S3_BUCKET")
        self._region = self.config.s3_region or os.getenv("AWS_S3_REGION", "ap-northeast-2")
        self._prefix = self.config.s3_prefix

        if not self._bucket:
            raise ValueError("S3 bucket not configured. Set AWS_S3_BUCKET.")

    def _get_client(self):
        """Boto3 S3 클라이언트 반환."""
        if self._client is None:
            try:
                import boto3
            except ImportError:
                raise RuntimeError("boto3 not installed. Run: pip install boto3")
            self._client = boto3.client("s3", region_name=self._region)
        return self._client

    async def put_object(
        self,
        data: Union[bytes, str, Path],
        key: str,
        content_type: Optional[str] = None,
    ) -> StorageResult:
        """객체를 S3에 저장."""
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
        if self.config.s3_public:
            url = f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{full_key}"
        else:
            url = await self.get_url(key)

        logger.info(f"S3 storage: uploaded {full_key}, size={size_bytes}")

        return StorageResult(
            key=full_key,
            url=url,
            size_bytes=size_bytes,
            content_type=content_type,
        )

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """Presigned URL 생성."""
        client = self._get_client()
        full_key = f"{self._prefix}{key}" if self._prefix else key

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
# MinIO Storage Provider
# =============================================================================


class MinIOStorageProvider(BaseStorageProvider):
    """MinIO Storage Provider (S3 호환).

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
        STORAGE_PROVIDER: local | s3 | minio
    """
    if provider is None:
        provider_str = os.getenv("STORAGE_PROVIDER", "local").lower()
        try:
            provider = StorageProvider(provider_str)
        except ValueError:
            logger.warning(f"Unknown STORAGE_PROVIDER: {provider_str}, using local")
            provider = StorageProvider.LOCAL

    # Config 구성
    if config is None:
        config = StorageConfig(
            provider=provider,
            local_path=os.getenv("STORAGE_LOCAL_PATH", "./video_output"),
            base_url=os.getenv("STORAGE_BASE_URL", "http://localhost:8000/static/videos"),
            s3_bucket=os.getenv("AWS_S3_BUCKET"),
            s3_region=os.getenv("AWS_S3_REGION", "ap-northeast-2"),
            s3_prefix=os.getenv("AWS_S3_PREFIX", "videos/"),
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
