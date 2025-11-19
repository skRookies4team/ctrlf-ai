"""
Search API 스키마
"""
from pydantic import BaseModel, Field
from typing import List, Optional


class SearchRequest(BaseModel):
    """검색 요청"""
    query: str = Field(..., description="검색할 텍스트", min_length=1)
    top_k: int = Field(5, description="반환할 결과 개수", ge=1, le=100)


class SearchResult(BaseModel):
    """검색 결과 단일 아이템"""
    score: float = Field(..., description="유사도 점수 (L2 거리, 작을수록 유사)")
    vector_id: int = Field(..., description="벡터 ID")
    ingest_id: str = Field(..., description="Ingest ID")
    file_name: str = Field(..., description="파일명")
    chunk_index: int = Field(..., description="청크 인덱스")
    text: str = Field(..., description="청크 텍스트")
    strategy: str = Field(..., description="청킹 전략")


class SearchResponse(BaseModel):
    """검색 응답"""
    query: str = Field(..., description="검색 쿼리")
    top_k: int = Field(..., description="요청한 결과 개수")
    results: List[SearchResult] = Field(..., description="검색 결과 리스트")
    total_results: int = Field(..., description="반환된 결과 개수")


class VectorStoreStatsResponse(BaseModel):
    """벡터 스토어 통계 응답"""
    dimension: int = Field(..., description="벡터 차원")
    total_vectors: int = Field(..., description="전체 벡터 개수")
    vector_count: int = Field(..., description="벡터 카운트")
    metadata_count: int = Field(..., description="메타데이터 개수")
    index_file_exists: bool = Field(..., description="인덱스 파일 존재 여부")
    metadata_file_exists: bool = Field(..., description="메타데이터 파일 존재 여부")
    index_file_path: str = Field(..., description="인덱스 파일 경로")
    metadata_file_path: str = Field(..., description="메타데이터 파일 경로")
