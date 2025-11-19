"""
Search API 라우터
"""
import logging
from fastapi import APIRouter, HTTPException

from core.pipeline import search_similar_chunks
from core.vector_store import get_vector_store
from app.schemas.search import (
    SearchRequest,
    SearchResponse,
    SearchResult,
    VectorStoreStatsResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """
    벡터 유사도 검색

    Args:
        request: 검색 요청 (query, top_k)

    Returns:
        SearchResponse: 검색 결과
    """
    try:
        logger.info(f"Search request: query='{request.query[:50]}...', top_k={request.top_k}")

        # 유사한 청크 검색
        results = search_similar_chunks(request.query, top_k=request.top_k)

        # 응답 포맷
        search_results = [
            SearchResult(
                score=r["score"],
                vector_id=r["vector_id"],
                ingest_id=r.get("ingest_id", ""),
                file_name=r.get("file_name", ""),
                chunk_index=r.get("chunk_index", 0),
                text=r.get("text", ""),
                strategy=r.get("strategy", "")
            )
            for r in results
        ]

        response = SearchResponse(
            query=request.query,
            top_k=request.top_k,
            results=search_results,
            total_results=len(search_results)
        )

        logger.info(f"Search completed: {len(search_results)} results")
        return response

    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/vector-store/stats", response_model=VectorStoreStatsResponse)
async def get_vector_store_stats():
    """
    벡터 스토어 통계 조회

    Returns:
        VectorStoreStatsResponse: 통계 정보
    """
    try:
        logger.info("Fetching vector store stats")

        vector_store = get_vector_store(dim=384)
        stats = vector_store.get_stats()

        response = VectorStoreStatsResponse(
            dimension=stats["dimension"],
            total_vectors=stats["total_vectors"],
            vector_count=stats["vector_count"],
            metadata_count=stats["metadata_count"],
            index_file_exists=stats["index_file_exists"],
            metadata_file_exists=stats["metadata_file_exists"],
            index_file_path=stats["index_file_path"],
            metadata_file_path=stats["metadata_file_path"]
        )

        logger.info(f"VectorStore stats: {stats['total_vectors']} vectors")
        return response

    except Exception as e:
        logger.error(f"Error fetching stats: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
