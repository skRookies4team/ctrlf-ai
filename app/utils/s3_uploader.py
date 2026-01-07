import boto3
import os
from pathlib import Path

def upload_to_s3(file_path: Path, s3_key: str) -> str:
    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "ap-northeast-2"),
    )

    bucket = os.getenv("S3_BUCKET_NAME")

    s3.upload_file(
        Filename=str(file_path),
        Bucket=bucket,
        Key=s3_key,
        ExtraArgs={
            "ContentType": "video/mp4",
        },
    )

    return f"https://{bucket}.s3.amazonaws.com/{s3_key}"
