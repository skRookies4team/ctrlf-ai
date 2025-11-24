"""
RAG Query API 라우터
"""
import logging
from fastapi import APIRouter, HTTPException

from core.pipeline import search_similar_chunks
from core.vector_store import get_vector_store
from core.llm import get_llm
from app.schemas.rag import (
    RAGQueryRequest,
    RAGQueryResponse,
    RAGChunk,
    RAGAnswerRequest,
    RAGAnswerResponse,
    RAGHealthResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


@router.post("/query", response_model=RAGQueryResponse)
async def rag_query(request: RAGQueryRequest):
    """
    RAG 쿼리 처리

    1. 사용자 쿼리를 임베딩
    2. 벡터 스토어에서 유사한 청크 검색
    3. 검색 결과 반환

    Args:
        request: RAG 쿼리 요청

    Returns:
        RAGQueryResponse: 검색된 청크 리스트
    """
    try:
        logger.info(f"RAG query: '{request.query[:50]}...', top_k={request.top_k}")

        # 유사한 청크 검색
        results = search_similar_chunks(request.query, top_k=request.top_k)

        # 응답 포맷
        chunks = []
        for r in results:
            chunk = RAGChunk(
                score=r["score"],
                vector_id=r["vector_id"],
                ingest_id=r.get("ingest_id", ""),
                file_name=r.get("file_name", ""),
                chunk_index=r.get("chunk_index", 0),
                text=r.get("text", "") if request.include_context else None,
                strategy=r.get("strategy", "")
            )
            chunks.append(chunk)

        response = RAGQueryResponse(
            query=request.query,
            top_k=request.top_k,
            retrieved_chunks=chunks,
            total_retrieved=len(chunks)
        )

        logger.info(f"RAG query completed: {len(chunks)} chunks retrieved")
        return response

    except Exception as e:
        logger.error(f"RAG query error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.post("/answer", response_model=RAGAnswerResponse)
async def rag_answer(request: RAGAnswerRequest):
    """
    RAG 답변 생성

    1. 사용자 쿼리를 임베딩
    2. 벡터 스토어에서 유사한 청크 검색
    3. LLM으로 답변 생성

    Args:
        request: RAG 답변 요청

    Returns:
        RAGAnswerResponse: 생성된 답변 및 검색 결과
    """
    try:
        logger.info(f"RAG answer request: '{request.query[:50]}...', top_k={request.top_k}, llm_type={request.llm_type}")

        # 1. 유사한 청크 검색
        results = search_similar_chunks(request.query, top_k=request.top_k)

        if not results:
            raise HTTPException(
                status_code=404,
                detail="No relevant documents found. Please upload documents first."
            )

        # 2. 청크 텍스트 추출
        context_chunks = [r.get("text", "") for r in results if r.get("text")]

        # 3. LLM으로 답변 생성
        llm = get_llm(llm_type=request.llm_type)
        answer = llm.generate_answer(
            query=request.query,
            context_chunks=context_chunks,
            max_tokens=request.max_tokens
        )

        # 4. 응답 포맷
        chunks = []
        for r in results:
            chunk = RAGChunk(
                score=r["score"],
                vector_id=r["vector_id"],
                ingest_id=r.get("ingest_id", ""),
                file_name=r.get("file_name", ""),
                chunk_index=r.get("chunk_index", 0),
                text=r.get("text", ""),
                strategy=r.get("strategy", "")
            )
            chunks.append(chunk)

        response = RAGAnswerResponse(
            query=request.query,
            answer=answer,
            retrieved_chunks=chunks,
            total_retrieved=len(chunks),
            llm_type=llm.__class__.__name__
        )

        logger.info(f"RAG answer completed: {len(chunks)} chunks, LLM={llm.__class__.__name__}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"RAG answer error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/health", response_model=RAGHealthResponse)
async def rag_health():
    """
    RAG 시스템 헬스체크

    - 벡터 스토어 상태 확인
    - 임베더 사용 가능 여부 확인
    - LLM 사용 가능 여부 확인
    - 전체 벡터 개수 확인

    Returns:
        RAGHealthResponse: RAG 시스템 상태
    """
    try:
        logger.info("Checking RAG system health")

        # 벡터 스토어 상태 확인
        vector_store_available = False
        total_vectors = 0
        embedder_available = False
        llm_available = False
        llm_type_str = None

        try:
            vector_store = get_vector_store(dim=384)
            stats = vector_store.get_stats()
            total_vectors = stats["total_vectors"]
            vector_store_available = True
            logger.info(f"Vector store available: {total_vectors} vectors")
        except Exception as e:
            logger.error(f"Vector store unavailable: {e}")

        # 임베더 사용 가능 여부 확인
        try:
            from core.embedder import embed_texts
            # 간단한 테스트 임베딩
            test_vector = embed_texts(["test"])
            if test_vector and len(test_vector) > 0:
                embedder_available = True
                logger.info("Embedder available")
        except Exception as e:
            logger.error(f"Embedder unavailable: {e}")

        # LLM 사용 가능 여부 확인
        try:
            llm = get_llm()
            llm_available = llm.is_available()
            llm_type_str = llm.__class__.__name__
            logger.info(f"LLM available: {llm_type_str}")
        except Exception as e:
            logger.error(f"LLM unavailable: {e}")

        # 전체 상태 결정
        if vector_store_available and embedder_available and llm_available and total_vectors > 0:
            status = "healthy"
            message = "RAG system is fully operational"
        elif vector_store_available and embedder_available and llm_available:
            status = "degraded"
            message = "RAG system is operational but no vectors available"
        else:
            status = "unhealthy"
            message = "RAG system is not operational"

        response = RAGHealthResponse(
            status=status,
            vector_store_available=vector_store_available,
            total_vectors=total_vectors,
            embedder_available=embedder_available,
            llm_available=llm_available,
            llm_type=llm_type_str,
            message=message
        )

        logger.info(f"RAG health check: {status}")
        return response

    except Exception as e:
        logger.error(f"RAG health check error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )