"""
문서 인덱싱 API 모델 (Phase 19)

AI Gateway 문서 인덱싱 API의 요청/응답 DTO를 정의합니다.
Spring 백엔드에서 문서를 업로드한 후, 이 API를 통해 RAGFlow에 인덱싱을 요청합니다.

사용 예시:
    from app.models.ingest import IngestRequest, IngestResponse

    request = IngestRequest(
        doc_id="DOC-2025-00123",
        source_type="policy",
        storage_url="https://files.internal/documents/DOC-2025-00123.pdf",
        file_name="정보보안규정_v3.pdf",
        mime_type="application/pdf",
    )
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


# 인덱싱 상태 타입 정의
IngestStatusType = Literal["DONE", "QUEUED", "PROCESSING", "FAILED"]


class IngestRequest(BaseModel):
    """
    문서 인덱싱 요청 DTO

    Attributes:
        doc_id: 백엔드 문서 PK 또는 유니크 ID (필수)
        source_type: 문서 유형 (필수, 예: "policy", "training", "incident")
                     RAGFLOW_DATASET_MAPPING의 key와 동일한 값 사용
        storage_url: 파일 다운로드 URL (필수, AI Gateway가 접근 가능한 내부 URL)
        file_name: 파일명 (필수)
        mime_type: MIME 타입 (필수, 예: application/pdf)
        department: 소속 부서 (선택)
        acl: 접근 제어 리스트 (선택, 예: ["ROLE_EMPLOYEE", "DEPT_DEV"])
        tags: 태그 리스트 (선택)
        version: 문서 버전 (선택, 기본값: 1)
    """

    doc_id: str = Field(..., min_length=1, description="백엔드 문서 ID")
    source_type: str = Field(
        ...,
        min_length=1,
        description="문서 유형 (예: policy, training, incident)",
    )
    storage_url: HttpUrl = Field(..., description="파일 다운로드 URL")
    file_name: str = Field(..., min_length=1, description="파일명")
    mime_type: str = Field(..., min_length=1, description="MIME 타입")
    department: Optional[str] = Field(None, description="소속 부서")
    acl: List[str] = Field(default_factory=list, description="접근 제어 리스트")
    tags: List[str] = Field(default_factory=list, description="태그 리스트")
    version: int = Field(default=1, ge=1, description="문서 버전")


class IngestResponse(BaseModel):
    """
    문서 인덱싱 응답 DTO

    Attributes:
        task_id: 인덱싱 작업 ID (RAGFlow에서 받은 값 또는 Gateway에서 생성한 값)
        status: 인덱싱 상태 (DONE, QUEUED, PROCESSING, FAILED)
    """

    task_id: str = Field(..., description="인덱싱 작업 ID")
    status: IngestStatusType = Field(..., description="인덱싱 상태")


class IngestErrorResponse(BaseModel):
    """
    인덱싱 에러 응답 DTO

    Attributes:
        detail: 에러 상세 메시지
    """

    detail: str = Field(..., description="에러 상세 메시지")
