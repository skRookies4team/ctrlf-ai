"""
A/B 테스트 API 모델

Backend → AI 모델 선택 API용 Request/Response 모델입니다.

엔드포인트: POST /internal/ai/context/model
"""

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ABModelEnum(str, Enum):
    """A/B 테스트 모델 타입."""

    OPENAI = "openai"
    SROBERTA = "sroberta"


class ABModelSetRequest(BaseModel):
    """
    A/B 테스트 모델 설정 요청.

    Backend → AI: POST /internal/ai/context/model
    """

    requestId: str = Field(
        ...,
        description="요청 ID (UUID)",
        min_length=1,
        max_length=100,
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    model: Literal["openai", "sroberta"] = Field(
        ...,
        description="사용할 임베딩 모델 ('openai' 또는 'sroberta')",
        examples=["sroberta"],
    )


class ABModelSetResponse(BaseModel):
    """
    A/B 테스트 모델 설정 응답.

    성공 시 반환.
    """

    success: bool = Field(
        True,
        description="설정 성공 여부",
    )
    requestId: str = Field(
        ...,
        description="요청 ID",
    )
    model: str = Field(
        ...,
        description="설정된 모델",
    )
    message: str = Field(
        "Model context set successfully",
        description="결과 메시지",
    )


class ABModelGetResponse(BaseModel):
    """
    A/B 테스트 모델 조회 응답.

    GET /internal/ai/context/model/{requestId}
    """

    requestId: str = Field(
        ...,
        description="요청 ID",
    )
    model: Optional[str] = Field(
        None,
        description="설정된 모델 (없으면 null)",
    )
    embeddingModel: Optional[str] = Field(
        None,
        description="임베딩 모델 이름",
    )
    embeddingDim: Optional[int] = Field(
        None,
        description="임베딩 차원",
    )
    collectionName: Optional[str] = Field(
        None,
        description="Milvus 컬렉션 이름",
    )


class ABContextStatsResponse(BaseModel):
    """
    A/B 컨텍스트 통계 응답.

    GET /internal/ai/context/stats
    """

    total: int = Field(
        ...,
        description="총 활성 컨텍스트 수",
    )
    byModel: dict = Field(
        default_factory=dict,
        description="모델별 컨텍스트 수",
    )


class ABModelErrorResponse(BaseModel):
    """
    A/B 테스트 API 에러 응답.
    """

    error: str = Field(
        ...,
        description="에러 코드",
    )
    message: str = Field(
        ...,
        description="에러 메시지",
    )
    requestId: Optional[str] = Field(
        None,
        description="요청 ID (있는 경우)",
    )
