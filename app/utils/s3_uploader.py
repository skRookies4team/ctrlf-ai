import boto3
import os
from pathlib import Path

from app.core.config import get_settings

def upload_to_s3(file_path: Path, s3_key: str) -> str:
    """S3에 파일을 업로드하고 URL을 반환합니다.
    
    Args:
        file_path: 업로드할 파일 경로
        s3_key: S3 객체 키
        
    Returns:
        S3 URL (STORAGE_PUBLIC_BASE_URL이 설정되어 있으면 사용, 아니면 기본 S3 URL)
    """
    settings = get_settings()
    
    # S3 클라이언트 생성
    s3_kwargs = {
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "region_name": os.getenv("AWS_REGION") or settings.AWS_S3_REGION,
    }
    
    # S3_ENDPOINT_URL이 설정되어 있으면 사용 (MinIO 호환)
    endpoint_url = os.getenv("S3_ENDPOINT_URL") or settings.S3_ENDPOINT_URL
    if endpoint_url:
        s3_kwargs["endpoint_url"] = endpoint_url
    
    s3 = boto3.client("s3", **s3_kwargs)

    # 버킷 이름 (S3_BUCKET_NAME 또는 AWS_S3_BUCKET)
    bucket = os.getenv("S3_BUCKET_NAME") or settings.AWS_S3_BUCKET
    if not bucket:
        raise ValueError("S3 bucket not configured. Set S3_BUCKET_NAME or AWS_S3_BUCKET.")

    # 파일 업로드
    s3.upload_file(
        Filename=str(file_path),
        Bucket=bucket,
        Key=s3_key,
        ExtraArgs={
            "ContentType": "video/mp4",
        },
    )

    # URL 생성 (STORAGE_PUBLIC_BASE_URL 우선 사용)
    public_base_url = os.getenv("STORAGE_PUBLIC_BASE_URL") or settings.STORAGE_PUBLIC_BASE_URL
    if public_base_url:
        # STORAGE_PUBLIC_BASE_URL이 설정되어 있으면 사용
        base = public_base_url.rstrip("/")
        return f"{base}/{s3_key}"
    else:
        # 기본 S3 URL 형식
        if endpoint_url:
            # MinIO 등 커스텀 엔드포인트인 경우
            base = endpoint_url.rstrip("/")
            return f"{base}/{bucket}/{s3_key}"
        else:
            # AWS S3 기본 URL
            return f"https://{bucket}.s3.{settings.AWS_S3_REGION}.amazonaws.com/{s3_key}"
