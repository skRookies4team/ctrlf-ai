"""
Mock RAGFlow Server for Integration Testing

Phase 8: Docker Compose 환경에서 RAG 검색을 시뮬레이션하는 Mock 서버입니다.
Phase 20: retrieval_results JSON 파일에서 실제 검색 결과를 로드하여 반환합니다.
Phase 51: 문서 처리 API 추가 (upload, parse, status, chunks) - 백엔드/스크립트 생성 테스트용

엔드포인트:
- POST /search: RAG 문서 검색 (레거시)
- POST /api/v1/retrieval: RAG 문서 검색 (RAGFlow 공식 API 호환)
- POST /api/v1/datasets/{dataset_id}/documents: 문서 업로드
- POST /api/v1/datasets/{dataset_id}/documents/{doc_id}/run: 파싱 트리거
- GET /api/v1/datasets/{dataset_id}/documents/{doc_id}: 문서 상태 조회
- GET /api/v1/datasets/{dataset_id}/documents/{doc_id}/chunks: 청크 조회
- GET /health: 헬스체크
- GET /stats: 호출 통계 (테스트 검증용)
"""

import json
import logging
import os
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, UploadFile, Query
from pydantic import BaseModel

# 로깅 설정
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Mock RAGFlow Server",
    description="Integration test용 RAGFlow 시뮬레이션 서버 (JSON 파일 기반)",
    version="0.2.0",
)


# =============================================================================
# 모델 정의
# =============================================================================


class SearchRequest(BaseModel):
    """RAG 검색 요청 (레거시)."""

    query: str
    top_k: int = 5
    dataset: Optional[str] = None
    user_role: Optional[str] = None
    department: Optional[str] = None


class RetrievalRequest(BaseModel):
    """RAGFlow 공식 API 호환 검색 요청."""

    question: str
    dataset_ids: List[str] = []
    top_k: int = 5


class RagDocument(BaseModel):
    """RAG 검색 결과 문서."""

    doc_id: str
    title: str
    page: Optional[int] = None
    score: float
    snippet: Optional[str] = None


class SearchResponse(BaseModel):
    """RAG 검색 응답 (레거시)."""

    results: List[RagDocument]


class RetrievalChunk(BaseModel):
    """RAGFlow 공식 API 호환 청크."""

    id: str
    content: str
    document_id: str
    document_name: str
    similarity: float
    dataset_id: str


class RetrievalResponse(BaseModel):
    """RAGFlow 공식 API 호환 응답."""

    code: int = 0
    data: Dict[str, Any]


class StatsResponse(BaseModel):
    """서버 통계."""

    search_call_count: int
    last_query: Optional[str]
    last_dataset: Optional[str]
    loaded_questions: int


# =============================================================================
# 글로벌 상태 (테스트 검증용)
# =============================================================================


class MockDocument:
    """Mock 문서 상태."""

    def __init__(self, doc_id: str, dataset_id: str, file_name: str):
        self.id = doc_id
        self.dataset_id = dataset_id
        self.name = file_name
        self.run = "UNSTART"  # UNSTART, RUNNING, DONE, FAIL, CANCEL
        self.progress = 0.0
        self.chunk_count = 0
        self.created_at = datetime.now().isoformat()
        self.chunks: List[Dict[str, Any]] = []


class ServerState:
    """서버 상태 추적."""

    def __init__(self):
        self.search_call_count = 0
        self.last_query: Optional[str] = None
        self.last_dataset: Optional[str] = None
        self.retrieval_data: List[Dict[str, Any]] = []
        # Phase 51: 문서 처리 상태 추적
        self.documents: Dict[str, MockDocument] = {}  # doc_id → MockDocument
        self.upload_count = 0
        self.parse_count = 0

    def reset(self):
        self.search_call_count = 0
        self.last_query = None
        self.last_dataset = None
        self.documents = {}
        self.upload_count = 0
        self.parse_count = 0


state = ServerState()


# =============================================================================
# Mock 청크 생성 (문서 처리용)
# =============================================================================


def generate_mock_chunks(doc_id: str, file_name: str, num_chunks: int = 5) -> List[Dict[str, Any]]:
    """Mock 청크를 생성합니다.

    실제 문서 파싱 없이 테스트용 청크를 생성합니다.
    """
    chunks = []

    # 파일명에서 주제 추출
    topic = file_name.replace(".pdf", "").replace(".docx", "").replace("_", " ")

    mock_contents = [
        f"{topic}에 대한 개요입니다. 이 문서는 {topic}의 핵심 내용을 다룹니다.",
        f"{topic}의 주요 정책 및 절차에 대해 설명합니다. 모든 직원은 이를 준수해야 합니다.",
        f"{topic} 관련 법률 및 규정을 안내합니다. 관련 법률에 따라 시행됩니다.",
        f"{topic}의 실제 사례와 적용 방법을 소개합니다. 실무에서 활용할 수 있습니다.",
        f"{topic}에 대한 FAQ 및 문의처 안내입니다. 추가 문의는 담당부서로 연락하세요.",
    ]

    for i in range(min(num_chunks, len(mock_contents))):
        chunk = {
            "id": f"chunk-{doc_id}-{i}",
            "content": mock_contents[i],
            "document_id": doc_id,
            "document_name": file_name,
            "positions": [[i * 100, i * 100 + 50]],
            "important_keywords": [topic, "정책", "규정"],
            "questions": [f"{topic}이란 무엇인가요?"],
        }
        chunks.append(chunk)

    return chunks


# =============================================================================
# 데이터 로드
# =============================================================================


def load_retrieval_data() -> List[Dict[str, Any]]:
    """
    retrieval_results JSON 파일에서 데이터를 로드합니다.

    여러 파일명 패턴을 시도합니다.
    """
    # 현재 파일 기준 디렉토리
    current_dir = Path(__file__).parent

    # 시도할 파일 패턴들
    patterns = [
        "retrieval_results_*.json",
        "retrieval_results.json",
    ]

    for pattern in patterns:
        files = list(current_dir.glob(pattern))
        if files:
            # 가장 최신 파일 선택 (이름 기준 정렬)
            json_file = sorted(files)[-1]
            logger.info(f"[Mock RAGFlow] Loading retrieval data from: {json_file}")

            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    results = data.get("results", [])
                    logger.info(f"[Mock RAGFlow] Loaded {len(results)} questions")
                    return results
            except Exception as e:
                logger.error(f"[Mock RAGFlow] Failed to load {json_file}: {e}")

    logger.warning("[Mock RAGFlow] No retrieval_results file found, using empty data")
    return []


def find_best_match(query: str, data: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    쿼리와 가장 유사한 질문을 찾습니다.

    단순 문자열 유사도 매칭을 사용합니다.
    """
    if not data:
        return None

    best_match = None
    best_score = 0.0

    for item in data:
        question = item.get("question", "")
        # 문자열 유사도 계산
        score = SequenceMatcher(None, query.lower(), question.lower()).ratio()

        # 키워드 매칭 보너스
        query_words = set(query.lower().split())
        question_words = set(question.lower().split())
        common_words = query_words & question_words
        if common_words:
            score += len(common_words) * 0.1

        if score > best_score:
            best_score = score
            best_match = item

    if best_match:
        logger.info(
            f"[Mock RAGFlow] Best match: '{best_match.get('question', '')[:50]}...' "
            f"(score: {best_score:.2f})"
        )

    return best_match


def convert_to_chunks(
    match: Dict[str, Any],
    top_k: int,
    dataset_id: str = "mock-dataset"
) -> List[Dict[str, Any]]:
    """
    매칭된 결과를 RAGFlow 청크 형식으로 변환합니다.
    """
    chunks = []

    contexts = match.get("retrieved_contexts", [])
    scores = match.get("retrieved_scores", [])
    sources = match.get("retrieved_sources", [])

    for i, context in enumerate(contexts[:top_k]):
        score = scores[i] if i < len(scores) else 0.5
        source = sources[i] if i < len(sources) else "unknown"

        chunk = {
            "id": f"chunk-{match.get('q_id', 'unknown')}-{i}",
            "content": context,
            "document_id": f"doc-{source.replace(' ', '_').replace('/', '_')}",
            "document_name": source,
            "similarity": score,
            "dataset_id": dataset_id,
        }
        chunks.append(chunk)

    return chunks


def convert_to_rag_documents(
    match: Dict[str, Any],
    top_k: int,
) -> List[RagDocument]:
    """
    매칭된 결과를 RagDocument 형식으로 변환합니다 (레거시 API용).
    """
    documents = []

    contexts = match.get("retrieved_contexts", [])
    scores = match.get("retrieved_scores", [])
    sources = match.get("retrieved_sources", [])

    for i, context in enumerate(contexts[:top_k]):
        score = scores[i] if i < len(scores) else 0.5
        source = sources[i] if i < len(sources) else "unknown"

        doc = RagDocument(
            doc_id=f"doc-{match.get('q_id', 'unknown')}-{i}",
            title=source,
            page=i + 1,
            score=score,
            snippet=context[:500] if len(context) > 500 else context,
        )
        documents.append(doc)

    return documents


# =============================================================================
# 앱 시작 시 데이터 로드
# =============================================================================


@app.on_event("startup")
async def startup_event():
    """앱 시작 시 검색 데이터를 로드합니다."""
    state.retrieval_data = load_retrieval_data()
    logger.info(f"[Mock RAGFlow] Server started with {len(state.retrieval_data)} questions")


# =============================================================================
# 엔드포인트
# =============================================================================


@app.get("/health")
async def health_check():
    """헬스체크 엔드포인트."""
    return {
        "status": "ok",
        "service": "mock-ragflow",
        "timestamp": datetime.now().isoformat(),
        "loaded_questions": len(state.retrieval_data),
    }


@app.post("/api/v1/retrieval", response_model=RetrievalResponse)
async def retrieval_api(request: RetrievalRequest):
    """
    RAGFlow 공식 API 호환 검색 엔드포인트.

    POST /api/v1/retrieval
    {
        "question": "검색 쿼리",
        "dataset_ids": ["dataset_id"],
        "top_k": 5
    }
    """
    # 상태 업데이트
    state.search_call_count += 1
    state.last_query = request.question
    state.last_dataset = request.dataset_ids[0] if request.dataset_ids else None

    logger.info(
        f"[Mock RAGFlow] Retrieval API called: question='{request.question[:50]}...', "
        f"dataset_ids={request.dataset_ids}, top_k={request.top_k}"
    )

    # 데이터가 없으면 빈 결과 반환
    if not state.retrieval_data:
        logger.warning("[Mock RAGFlow] No data loaded, returning empty results")
        return RetrievalResponse(
            code=0,
            data={"chunks": [], "doc_aggs": [], "total": 0}
        )

    # 가장 유사한 질문 찾기
    match = find_best_match(request.question, state.retrieval_data)

    if not match:
        logger.info("[Mock RAGFlow] No matching question found")
        return RetrievalResponse(
            code=0,
            data={"chunks": [], "doc_aggs": [], "total": 0}
        )

    # 청크로 변환
    dataset_id = request.dataset_ids[0] if request.dataset_ids else "mock-dataset"
    chunks = convert_to_chunks(match, request.top_k, dataset_id)

    logger.info(f"[Mock RAGFlow] Returning {len(chunks)} chunks")

    return RetrievalResponse(
        code=0,
        data={
            "chunks": chunks,
            "doc_aggs": [
                {
                    "count": len(chunks),
                    "doc_id": chunks[0]["document_id"] if chunks else "unknown",
                    "doc_name": chunks[0]["document_name"] if chunks else "unknown",
                }
            ] if chunks else [],
            "total": len(chunks),
        }
    )


@app.post("/search", response_model=SearchResponse)
async def search_documents(request: SearchRequest):
    """
    RAG 문서 검색 엔드포인트 (레거시).

    실제 RAGFlow API를 시뮬레이션합니다.
    """
    # 상태 업데이트
    state.search_call_count += 1
    state.last_query = request.query
    state.last_dataset = request.dataset

    logger.info(
        f"[Mock RAGFlow] Search called: query='{request.query[:50]}...', "
        f"dataset={request.dataset}, top_k={request.top_k}"
    )

    # 데이터가 없으면 빈 결과 반환
    if not state.retrieval_data:
        logger.warning("[Mock RAGFlow] No data loaded, returning empty results")
        return SearchResponse(results=[])

    # 가장 유사한 질문 찾기
    match = find_best_match(request.query, state.retrieval_data)

    if not match:
        logger.info("[Mock RAGFlow] No matching question found")
        return SearchResponse(results=[])

    # RagDocument로 변환
    documents = convert_to_rag_documents(match, request.top_k)

    logger.info(f"[Mock RAGFlow] Returning {len(documents)} documents")
    return SearchResponse(results=documents)


@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    """
    서버 통계 엔드포인트 (테스트 검증용).

    통합 테스트에서 RAGFlow가 호출되었는지 확인할 때 사용합니다.
    """
    return StatsResponse(
        search_call_count=state.search_call_count,
        last_query=state.last_query,
        last_dataset=state.last_dataset,
        loaded_questions=len(state.retrieval_data),
    )


@app.post("/stats/reset")
async def reset_stats():
    """
    통계 초기화 엔드포인트.

    테스트 간 상태 초기화에 사용합니다.
    """
    state.reset()
    logger.info("[Mock RAGFlow] Stats reset")
    return {"status": "ok", "message": "Stats reset"}


@app.post("/reload")
async def reload_data():
    """
    데이터 리로드 엔드포인트.

    JSON 파일이 업데이트된 후 서버 재시작 없이 리로드할 때 사용합니다.
    """
    state.retrieval_data = load_retrieval_data()
    logger.info(f"[Mock RAGFlow] Data reloaded: {len(state.retrieval_data)} questions")
    return {
        "status": "ok",
        "message": "Data reloaded",
        "loaded_questions": len(state.retrieval_data),
    }


# =============================================================================
# Phase 51: 문서 처리 API (백엔드/스크립트 생성 테스트용)
# =============================================================================


@app.post("/api/v1/datasets/{dataset_id}/documents")
async def upload_document(
    dataset_id: str,
    file: Optional[UploadFile] = File(None),
    file_url: Optional[str] = Form(None),
    file_name: Optional[str] = Form(None),
):
    """
    문서 업로드 엔드포인트.

    RAGFlow API 호환:
    - 파일 직접 업로드 또는 URL로 업로드 지원
    - 업로드 즉시 문서 ID 반환
    """
    state.upload_count += 1

    # 파일명 결정
    if file and file.filename:
        name = file.filename
    elif file_name:
        name = file_name
    elif file_url:
        name = file_url.split("/")[-1].split("?")[0] or "document.pdf"
    else:
        name = f"document-{uuid.uuid4().hex[:8]}.pdf"

    # Mock 문서 생성
    doc_id = f"doc-{uuid.uuid4().hex[:12]}"
    mock_doc = MockDocument(doc_id=doc_id, dataset_id=dataset_id, file_name=name)
    state.documents[doc_id] = mock_doc

    logger.info(
        f"[Mock RAGFlow] Document uploaded: doc_id={doc_id}, "
        f"dataset_id={dataset_id}, name={name}"
    )

    return {
        "code": 0,
        "data": {
            "id": doc_id,
            "name": name,
            "dataset_id": dataset_id,
            "created_at": mock_doc.created_at,
            "status": "UNSTART",
        },
    }


@app.post("/api/v1/datasets/{dataset_id}/documents/{doc_id}/run")
async def trigger_parsing(dataset_id: str, doc_id: str):
    """
    문서 파싱 트리거 엔드포인트.

    RAGFlow API 호환:
    - 파싱 시작 요청
    - Mock에서는 즉시 DONE 상태로 전환하고 청크 생성
    """
    state.parse_count += 1

    if doc_id not in state.documents:
        logger.warning(f"[Mock RAGFlow] Document not found: doc_id={doc_id}")
        return {"code": 404, "message": "Document not found"}

    mock_doc = state.documents[doc_id]

    # Mock: 즉시 파싱 완료 처리 (실제는 비동기)
    mock_doc.run = "DONE"
    mock_doc.progress = 1.0
    mock_doc.chunks = generate_mock_chunks(doc_id, mock_doc.name)
    mock_doc.chunk_count = len(mock_doc.chunks)

    logger.info(
        f"[Mock RAGFlow] Parsing triggered and completed: doc_id={doc_id}, "
        f"chunks={mock_doc.chunk_count}"
    )

    return {
        "code": 0,
        "data": {
            "id": doc_id,
            "run": mock_doc.run,
            "progress": mock_doc.progress,
            "chunk_count": mock_doc.chunk_count,
        },
    }


@app.get("/api/v1/datasets/{dataset_id}/documents/{doc_id}")
async def get_document_status(dataset_id: str, doc_id: str):
    """
    문서 상태 조회 엔드포인트.

    RAGFlow API 호환:
    - 파싱 진행 상태 조회 (polling용)
    - run: UNSTART, RUNNING, DONE, FAIL, CANCEL
    """
    if doc_id not in state.documents:
        logger.warning(f"[Mock RAGFlow] Document not found: doc_id={doc_id}")
        return {"code": 404, "message": "Document not found"}

    mock_doc = state.documents[doc_id]

    logger.debug(
        f"[Mock RAGFlow] Document status queried: doc_id={doc_id}, "
        f"run={mock_doc.run}, progress={mock_doc.progress}"
    )

    return {
        "code": 0,
        "data": {
            "id": mock_doc.id,
            "name": mock_doc.name,
            "dataset_id": mock_doc.dataset_id,
            "run": mock_doc.run,
            "progress": mock_doc.progress,
            "chunk_count": mock_doc.chunk_count,
            "created_at": mock_doc.created_at,
        },
    }


@app.get("/api/v1/datasets/{dataset_id}/documents/{doc_id}/chunks")
async def get_document_chunks(
    dataset_id: str,
    doc_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
):
    """
    문서 청크 조회 엔드포인트.

    RAGFlow API 호환:
    - 파싱 완료된 문서의 청크 조회
    - 페이지네이션 지원
    """
    if doc_id not in state.documents:
        logger.warning(f"[Mock RAGFlow] Document not found: doc_id={doc_id}")
        return {"code": 404, "message": "Document not found"}

    mock_doc = state.documents[doc_id]

    if mock_doc.run != "DONE":
        logger.warning(
            f"[Mock RAGFlow] Document not parsed yet: doc_id={doc_id}, "
            f"run={mock_doc.run}"
        )
        return {
            "code": 0,
            "data": {
                "chunks": [],
                "total": 0,
            },
        }

    # 페이지네이션
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated_chunks = mock_doc.chunks[start_idx:end_idx]

    logger.info(
        f"[Mock RAGFlow] Chunks returned: doc_id={doc_id}, "
        f"page={page}, count={len(paginated_chunks)}, total={len(mock_doc.chunks)}"
    )

    return {
        "code": 0,
        "data": {
            "chunks": paginated_chunks,
            "total": len(mock_doc.chunks),
        },
    }


# =============================================================================
# 확장 통계 (문서 처리 포함)
# =============================================================================


@app.get("/stats/documents")
async def get_document_stats():
    """
    문서 처리 통계 엔드포인트.

    테스트 검증용으로 업로드/파싱 횟수 및 문서 상태를 확인합니다.
    """
    docs_summary = [
        {
            "id": doc.id,
            "name": doc.name,
            "run": doc.run,
            "chunk_count": doc.chunk_count,
        }
        for doc in state.documents.values()
    ]

    return {
        "upload_count": state.upload_count,
        "parse_count": state.parse_count,
        "documents_count": len(state.documents),
        "documents": docs_summary,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
