"""
Ingest API 스키마
"""
from pydantic import BaseModel, Field
from typing import List, Optional


class IngestRequest(BaseModel):
    """파일 업로드 요청 (multipart/form-data 사용 시 불필요)"""
    pass


class ChunkResponse(BaseModel):
    """청크 응답"""
    chunk_id: str
    chunk_index: int
    text: str
    length: int
    start_char: int
    end_char: int


class IngestResponse(BaseModel):
    """Ingest 응답"""
    success: bool
    ingest_id: str
    file_name: str
    message: str
    num_chunks: int
    status: str  # "OK" | "WARN" | "FAIL"
    reasons: List[str]
    error: Optional[str] = None


class IngestDetailResponse(BaseModel):
    """Ingest 상세 응답 (청크 포함)"""
    success: bool
    ingest_id: str
    file_name: str
    file_path: str
    raw_text_len: int
    cleaned_text_len: int
    num_chunks: int
    chunk_strategy: str
    max_chars: int
    overlap_chars: int
    status: str
    reasons: List[str]
    chunks: List[ChunkResponse]
    created_at: str
    error: Optional[str] = None


class HealthResponse(BaseModel):
    """헬스체크 응답"""
    status: str
    version: str = "1.0.0"
    pipeline_ready: bool
