"""
검색 API 모델 (Phase 18)

AI Gateway 표준 RAG 검색 API의 요청/응답 DTO를 정의합니다.
외부 서비스/프론트엔드에서 사용하는 표준 스키마입니다.

사용 예시:
    from app.models.search import SearchRequest, SearchResponse

    request = SearchRequest(query="연차휴가 규정", top_k=5, dataset="policy")
    response = SearchResponse(results=[...])
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """
    검색 요청 DTO

    Attributes:
        query: 검색 쿼리 텍스트 (필수)
        top_k: 반환할 최대 결과 수 (기본값: 5)
        dataset: 검색할 데이터셋 슬러그 (필수, 예: "policy", "training", "incident")
    """

    query: str = Field(..., min_length=1, description="검색 쿼리 텍스트")
    top_k: int = Field(default=5, ge=1, le=100, description="반환할 최대 결과 수")
    dataset: str = Field(..., min_length=1, description="데이터셋 슬러그 (예: policy, training)")


class SearchResultItem(BaseModel):
    """
    검색 결과 항목

    Attributes:
        doc_id: 문서 또는 청크 ID
        title: 문서명
        page: 페이지 번호 (있는 경우)
        score: 유사도 점수 (0.0 ~ 1.0)
        snippet: 내용 일부/미리보기
        dataset: 데이터셋 슬러그
        source: 출처 시스템 (예: "ragflow")
    """

    doc_id: str = Field(..., description="문서 또는 청크 ID")
    title: str = Field(..., description="문서명")
    page: Optional[int] = Field(None, description="페이지 번호")
    score: float = Field(..., ge=0.0, le=1.0, description="유사도 점수")
    snippet: Optional[str] = Field(None, description="내용 미리보기")
    dataset: str = Field(..., description="데이터셋 슬러그")
    source: str = Field(default="ragflow", description="출처 시스템")


class SearchResponse(BaseModel):
    """
    검색 응답 DTO

    Attributes:
        results: 검색 결과 리스트
    """

    results: List[SearchResultItem] = Field(default_factory=list, description="검색 결과 리스트")
