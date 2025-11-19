"""
RAG Query API 스키마
"""
from pydantic import BaseModel, Field
from typing import List, Optional


class RAGQueryRequest(BaseModel):
    """RAG 쿼리 요청"""
    query: str = Field(..., description="사용자 질문", min_length=1)
    top_k: int = Field(5, description="검색할 청크 개수", ge=1, le=20)
    include_context: bool = Field(True, description="검색된 청크 텍스트 포함 여부")


class RAGChunk(BaseModel):
    """RAG 검색 결과 청크"""
    score: float = Field(..., description="유사도 점수 (L2 거리, 작을수록 유사)")
    vector_id: int = Field(..., description="벡터 ID")
    ingest_id: str = Field(..., description="Ingest ID")
    file_name: str = Field(..., description="파일명")
    chunk_index: int = Field(..., description="청크 인덱스")
    text: Optional[str] = Field(None, description="청크 텍스트 (include_context=True일 때만)")
    strategy: str = Field(..., description="청킹 전략")


class RAGQueryResponse(BaseModel):
    """RAG 쿼리 응답"""
    query: str = Field(..., description="원본 쿼리")
    top_k: int = Field(..., description="요청한 검색 개수")
    retrieved_chunks: List[RAGChunk] = Field(..., description="검색된 청크 리스트")
    total_retrieved: int = Field(..., description="실제 검색된 청크 개수")


class RAGAnswerRequest(BaseModel):
    """RAG 답변 생성 요청"""
    query: str = Field(..., description="사용자 질문", min_length=1)
    top_k: int = Field(5, description="검색할 청크 개수", ge=1, le=20)
    max_tokens: int = Field(500, description="최대 생성 토큰 수", ge=100, le=2000)
    llm_type: Optional[str] = Field(None, description="LLM 타입 (mock, openai, 또는 None=자동)")


class RAGAnswerResponse(BaseModel):
    """RAG 답변 생성 응답"""
    query: str = Field(..., description="원본 쿼리")
    answer: str = Field(..., description="생성된 답변")
    retrieved_chunks: List[RAGChunk] = Field(..., description="참조된 청크 리스트")
    total_retrieved: int = Field(..., description="검색된 청크 개수")
    llm_type: str = Field(..., description="사용된 LLM 타입")


class RAGHealthResponse(BaseModel):
    """RAG 시스템 헬스체크 응답"""
    status: str = Field(..., description="시스템 상태 (healthy, degraded, unhealthy)")
    vector_store_available: bool = Field(..., description="벡터 스토어 사용 가능 여부")
    total_vectors: int = Field(..., description="전체 벡터 개수")
    embedder_available: bool = Field(..., description="임베더 사용 가능 여부")
    llm_available: bool = Field(False, description="LLM 사용 가능 여부")
    llm_type: Optional[str] = Field(None, description="사용 가능한 LLM 타입")
    message: str = Field("", description="추가 메시지")
