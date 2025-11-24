"""
Ingestion Monitoring - 전처리·청킹·임베딩 모니터링 데이터 모델
"""
from dataclasses import dataclass, asdict
from typing import List, Optional


@dataclass
class FileMetrics:
    """파일 정보 메트릭"""
    file_name: str
    file_path: str
    original_extension: str
    converted_extension: str
    mime_type: Optional[str]
    size_bytes: int
    num_pages: Optional[int]
    conversion_success: bool
    conversion_error: Optional[str] = None


@dataclass
class ParseMetrics:
    """파싱 단계 메트릭"""
    parser_name: str
    raw_text_len: int
    raw_line_count: int
    parse_success: bool
    parse_error: Optional[str]
    used_ocr: bool
    ocr_text_len: int
    page_text_coverage_ratio: Optional[float]


@dataclass
class CleaningMetrics:
    """클리닝 단계 메트릭"""
    cleaned_text_len: int
    cleaned_line_count: int
    clean_ratio: Optional[float]  # cleaned_text_len / raw_text_len (0~1)
    removed_header_footer: bool
    suspicious_char_ratio: Optional[float]


@dataclass
class StructureMetrics:
    """구조 분석 단계 메트릭"""
    paragraph_count: int
    heading_count: int
    section_count: int
    structure_ok: bool
    structure_warnings: List[str]


@dataclass
class ChunkingMetrics:
    """청킹 단계 메트릭"""
    chunk_strategy: str
    max_chars: int
    overlap_chars: int
    num_chunks: int
    chunk_lengths: List[int]
    chunk_len_min: Optional[int]
    chunk_len_max: Optional[int]
    chunk_len_avg: Optional[float]
    chunk_len_std: Optional[float]
    chunk_warnings: List[str]


@dataclass
class EmbeddingMetrics:
    """임베딩 단계 메트릭"""
    embedding_model: str
    embedding_dim: int
    embedding_count: int
    vector_store_type: str
    vector_store_collection: Optional[str]
    vector_store_insert_success: bool
    vector_store_error: Optional[str]


@dataclass
class EvaluationMetrics:
    """평가 단계 메트릭"""
    status: str  # "OK" | "WARN" | "FAIL"
    reasons: List[str]
    notes: Optional[str] = None


@dataclass
class IngestMonitoring:
    """전체 Ingestion 모니터링 객체"""
    ingest_id: str
    created_at: str
    file: FileMetrics
    parse: ParseMetrics
    cleaning: CleaningMetrics
    structure: StructureMetrics
    chunking: ChunkingMetrics
    embedding: EmbeddingMetrics
    evaluation: EvaluationMetrics

    def to_dict(self) -> dict:
        """
        JSON 직렬화용 헬퍼. dataclass 중첩 구조를 dict로 변환.
        """
        return asdict(self)
