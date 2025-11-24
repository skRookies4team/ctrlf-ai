"""
Reports API 스키마
"""
from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class MonitoringBlock(BaseModel):
    """모니터링 데이터 블록"""
    file: Dict[str, Any]
    parse: Dict[str, Any]
    cleaning: Dict[str, Any]
    structure: Dict[str, Any]
    chunking: Dict[str, Any]
    embedding: Dict[str, Any]
    evaluation: Dict[str, Any]


class ReportSummary(BaseModel):
    """리포트 요약"""
    ingest_id: str
    file_name: str
    num_chunks: int
    status: str  # "OK" | "WARN" | "FAIL"
    created_at: str
    monitoring: Optional[MonitoringBlock] = None


class ReportDetail(BaseModel):
    """리포트 상세"""
    ingest_id: str
    file_name: str
    file_path: str
    raw_text_len: int
    cleaned_text_len: int
    num_chunks: int
    chunk_lengths: List[int]
    status: str
    reasons: List[str]
    chunk_strategy: str
    max_chars: int
    overlap_chars: int
    created_at: str
    monitoring: Optional[MonitoringBlock] = None


class ReportsListResponse(BaseModel):
    """리포트 목록 응답"""
    total: int
    reports: List[ReportSummary]


class ReportResponse(BaseModel):
    """단일 리포트 응답"""
    report: Optional[ReportDetail] = None
    error: Optional[str] = None
