"""
검색 API 엔드포인트 (Phase 18)

AI Gateway 표준 RAG 검색 API를 제공합니다.
외부 서비스/프론트엔드에서 단일 엔드포인트로 RAG 검색을 수행할 수 있습니다.

엔드포인트:
    POST /search - RAG 검색 수행

사용 예시:
    curl -X POST http://localhost:8000/search \\
      -H "Content-Type: application/json" \\
      -d '{"query": "연차휴가 규정", "top_k": 5, "dataset": "policy"}'
"""

from fastapi import APIRouter, HTTPException, status

from app.core.logging import get_logger
from app.models.search import SearchRequest, SearchResponse
from app.services.search_service import DatasetNotFoundError, SearchService

logger = get_logger(__name__)

router = APIRouter()

# 서비스 인스턴스 (lazy initialization)
_search_service: SearchService | None = None


def get_search_service() -> SearchService:
    """SearchService 인스턴스를 반환합니다 (싱글턴)."""
    global _search_service
    if _search_service is None:
        _search_service = SearchService()
    return _search_service


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="RAG 검색",
    description="RAGFlow를 통해 관련 문서를 검색합니다.",
    responses={
        200: {
            "description": "검색 성공",
            "content": {
                "application/json": {
                    "example": {
                        "results": [
                            {
                                "doc_id": "chunk-001",
                                "title": "인사규정",
                                "page": 3,
                                "score": 0.87,
                                "snippet": "연차휴가는 1년 근무 시 15일이 부여됩니다...",
                                "dataset": "policy",
                                "source": "ragflow",
                            }
                        ]
                    }
                }
            },
        },
        400: {
            "description": "잘못된 요청 (dataset 없음 등)",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Dataset 'unknown' not found. Available: policy, training, incident"
                    }
                }
            },
        },
    },
)
async def search(request: SearchRequest) -> SearchResponse:
    """
    RAG 검색을 수행합니다.

    Args:
        request: 검색 요청
            - query: 검색 쿼리 텍스트 (필수)
            - top_k: 반환할 최대 결과 수 (기본값: 5)
            - dataset: 데이터셋 슬러그 (필수, 예: "policy")

    Returns:
        SearchResponse: 검색 결과

    Raises:
        HTTPException 400: 알 수 없는 dataset 슬러그
    """
    logger.info(
        f"Search request: query='{request.query[:50]}...', "
        f"dataset={request.dataset}, top_k={request.top_k}"
    )

    service = get_search_service()

    try:
        response = await service.search(request)
        logger.info(f"Search completed: {len(response.results)} results")
        return response

    except DatasetNotFoundError as e:
        logger.warning(f"Dataset not found: {e.dataset}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except Exception as e:
        logger.exception("Unexpected error in search endpoint")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during search",
        )
