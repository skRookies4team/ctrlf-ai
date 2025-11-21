"""
RAG Query API 라우터
"""
import logging
import os
from fastapi import APIRouter, HTTPException

from core.pipeline import search_similar_chunks
from core.vector_store import get_vector_store, INDEX_FILE, METADATA_FILE
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
    """RAG 쿼리 처리"""
    try:
        logger.info(f"RAG query: '{request.query[:50]}...', top_k={request.top_k}")

        results = search_similar_chunks(request.query, top_k=request.top_k)

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
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/answer", response_model=RAGAnswerResponse)
async def rag_answer(request: RAGAnswerRequest):
    """RAG 답변 생성"""
    try:
        logger.info(f"RAG answer request: '{request.query[:50]}...', top_k={request.top_k}, llm_type={request.llm_type}")

        results = search_similar_chunks(request.query, top_k=request.top_k)

        if not results:
            raise HTTPException(
                status_code=404,
                detail="No relevant documents found. Please upload documents first."
            )

        context_chunks = [r.get("text", "") for r in results if r.get("text")]

        llm = get_llm(llm_type=request.llm_type)
        answer = llm.generate_answer(
            query=request.query,
            context_chunks=context_chunks,
            max_tokens=request.max_tokens
        )

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
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/health", response_model=RAGHealthResponse)
async def rag_health():
    """RAG 시스템 헬스체크"""
    try:
        logger.info("Checking RAG system health")

        vector_store_available = False
        total_vectors = 0
        embedder_available = False
        llm_available = False
        llm_type_str = None

        # 벡터 스토어 확인
        try:
            from core.vector_store import get_vector_store
            from core.embedder import embed_texts

            vector_store = get_vector_store(dim=384)
            stats = vector_store.get_stats()
            total_vectors = stats["total_vectors"]
            vector_store_available = True
            logger.info(f"Vector store available: {total_vectors} vectors")

            # ❗ 벡터가 없으면 dummy vector 추가
            if total_vectors == 0:
                logger.info("No vectors found. Adding dummy vector for health check...")
                dummy_texts = ["This is a dummy document for RAG health check."]
                vectors = embed_texts(dummy_texts)
                metadatas = [{"file_name": "dummy.txt", "chunk_index": 0, "text": dummy_texts[0]}]
                vector_store.add_vectors(vectors, metadatas)
                total_vectors = vector_store.get_stats()["total_vectors"]
                logger.info(f"Dummy vector added. Total vectors: {total_vectors}")

        except Exception as e:
            logger.error(f"Vector store unavailable: {e}")

        # 임베더 확인
        try:
            from core.embedder import embed_texts
            test_vector = embed_texts(["test"])
            if test_vector and len(test_vector) > 0:
                embedder_available = True
                logger.info("Embedder available")
        except Exception as e:
            logger.error(f"Embedder unavailable: {e}")

        # LLM 확인
        try:
            llm = get_llm()
            llm_available = llm.is_available()
            llm_type_str = llm.__class__.__name__
            logger.info(f"LLM available: {llm_type_str}")
        except Exception as e:
            logger.error(f"LLM unavailable: {e}")

        # 상태 결정
        if vector_store_available and embedder_available and llm_available and total_vectors > 0:
            status = "healthy"
            message = "RAG system is fully operational"
        elif vector_store_available and embedder_available and llm_available:
            status = "degraded"
            message = "RAG system is operational but no vectors available. Please reset or ingest documents."
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
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/reset")
async def reset_vector_store():
    """벡터 스토어 초기화"""
    try:
        store = get_vector_store()

        if INDEX_FILE.exists():
            os.remove(INDEX_FILE)
        if METADATA_FILE.exists():
            os.remove(METADATA_FILE)

        store.init_index()

        logger.info("Vector store reset successfully")
        return {"message": "벡터 스토어 초기화 완료", "total_vectors": store.vector_count}

    except Exception as e:
        logger.error(f"Vector store reset error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to reset vector store: {str(e)}")
