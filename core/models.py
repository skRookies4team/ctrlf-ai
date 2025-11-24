"""
데이터 모델 정의
"""
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional
import json


@dataclass
class ChunkingReport:
    """청킹 프로세스 결과 리포트"""
    ingest_id: str
    file_name: str
    file_path: str
    raw_text_len: int
    cleaned_text_len: int
    num_chunks: int
    chunk_lengths: List[int]
    status: str  # "OK" | "WARN" | "FAIL"
    reasons: List[str]
    chunk_strategy: str  # "character_window"
    max_chars: int
    overlap_chars: int
    created_at: str

    def to_dict(self) -> dict:
        """딕셔너리로 변환"""
        return asdict(self)

    def to_json(self) -> str:
        """JSON 문자열로 변환"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "ChunkingReport":
        """딕셔너리에서 객체 생성"""
        return cls(**data)


@dataclass
class Chunk:
    """청크 단위 데이터"""
    chunk_id: str
    ingest_id: str
    text: str
    chunk_index: int
    start_char: int
    end_char: int
    length: int
    embedding: Optional[List[float]] = None

    def to_dict(self) -> dict:
        """딕셔너리로 변환"""
        return asdict(self)


@dataclass
class ParseResult:
    """파싱 결과"""
    success: bool
    text: str
    error: Optional[str] = None
    page_count: Optional[int] = None


@dataclass
class CleanResult:
    """클리닝 결과"""
    text: str
    original_length: int
    cleaned_length: int
    removed_chars: int


@dataclass
class PipelineResult:
    """전체 파이프라인 실행 결과"""
    ingest_id: str
    success: bool
    chunks: List[Chunk]
    report: ChunkingReport
    error: Optional[str] = None
