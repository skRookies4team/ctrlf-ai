"""
Mock LLM Server for Integration Testing

Phase 8: Docker Compose 환경에서 내부 LLM API를 시뮬레이션하는 Mock 서버입니다.
OpenAI API 호환 형식을 사용합니다.

엔드포인트:
- POST /v1/chat/completions: LLM 채팅 완성
- GET /v1/models: 사용 가능한 모델 목록
- GET /health: 헬스체크
- GET /stats: 호출 통계 (테스트 검증용)
"""

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

# 로깅 설정
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Mock LLM Server",
    description="Integration test용 OpenAI 호환 LLM 시뮬레이션 서버",
    version="0.1.0",
)


# =============================================================================
# 모델 정의 (OpenAI API 호환)
# =============================================================================


class ChatMessage(BaseModel):
    """채팅 메시지."""

    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    """채팅 완성 요청."""

    model: str = "gpt-3.5-turbo"
    messages: List[ChatMessage]
    temperature: float = 0.7
    max_tokens: int = 1024
    stream: bool = False


class ChatCompletionChoice(BaseModel):
    """채팅 완성 선택지."""

    index: int
    message: ChatMessage
    finish_reason: str


class ChatCompletionUsage(BaseModel):
    """토큰 사용량."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """채팅 완성 응답."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionUsage


class ModelInfo(BaseModel):
    """모델 정보."""

    id: str
    object: str = "model"
    created: int
    owned_by: str


class ModelsResponse(BaseModel):
    """모델 목록 응답."""

    object: str = "list"
    data: List[ModelInfo]


class StatsResponse(BaseModel):
    """서버 통계."""

    completion_call_count: int
    last_model: Optional[str]
    last_messages_count: int


# =============================================================================
# 글로벌 상태 (테스트 검증용)
# =============================================================================


class ServerState:
    """서버 상태 추적."""

    def __init__(self):
        self.completion_call_count = 0
        self.last_model: Optional[str] = None
        self.last_messages: List[Dict[str, Any]] = []

    def reset(self):
        self.completion_call_count = 0
        self.last_model = None
        self.last_messages = []


state = ServerState()


# =============================================================================
# 응답 생성 로직
# =============================================================================


def extract_rag_context(messages: List[ChatMessage]) -> Optional[str]:
    """
    시스템 메시지에서 RAG 컨텍스트를 추출합니다.

    Returns:
        str: RAG 컨텍스트 텍스트, 없으면 None
    """
    for msg in messages:
        if msg.role == "system":
            content = msg.content
            # "참고 문서:" 또는 "Context:" 이후의 내용 추출
            markers = ["참고 문서:", "참고문서:", "Context:", "검색 결과:"]
            for marker in markers:
                if marker in content:
                    idx = content.find(marker)
                    context = content[idx + len(marker):].strip()
                    if context:
                        return context
            # 마커가 없어도 시스템 메시지에 충분한 내용이 있으면 사용
            if len(content) > 200:
                return content
    return None


def generate_response(messages: List[ChatMessage]) -> str:
    """
    메시지 내용에 따라 적절한 응답을 생성합니다.

    RAG 컨텍스트가 있으면 이를 기반으로 응답을 생성합니다.
    """
    # 마지막 사용자 메시지 추출
    user_message = ""
    for msg in reversed(messages):
        if msg.role == "user":
            user_message = msg.content
            break

    # RAG 컨텍스트 추출
    rag_context = extract_rag_context(messages)

    logger.info(f"[Mock LLM] User question: {user_message[:100]}")
    logger.info(f"[Mock LLM] RAG context available: {rag_context is not None}")
    if rag_context:
        logger.info(f"[Mock LLM] RAG context preview: {rag_context[:200]}...")

    # RAG 컨텍스트가 있으면 컨텍스트 기반 응답 생성
    if rag_context:
        # 컨텍스트에서 핵심 문장 추출 (첫 500자 기준)
        context_summary = rag_context[:500]

        return (
            f"문의하신 '{user_message[:30]}...'에 대해 답변드립니다.\n\n"
            f"사내 규정에 따르면, {context_summary}\n\n"
            f"추가 문의사항이 있으시면 담당 부서로 연락해 주세요."
        )

    # RAG 컨텍스트 없는 경우: 키워드 기반 응답
    if "연차" in user_message or "휴가" in user_message:
        return (
            "연차휴가에 대해 안내드립니다. "
            "구체적인 규정은 인사팀에 문의해 주세요."
        )
    elif "교육" in user_message:
        return (
            "정보보호 교육 일정에 대해 안내드립니다. "
            "정확한 일정은 사내 공지사항을 확인하시거나 정보보안팀에 문의해 주세요."
        )
    elif "보안" in user_message or "사고" in user_message:
        return (
            "보안 관련 문의에 대해 안내드립니다. "
            "보안사고 발생 시 즉시 정보보안팀(내선 1234)에 신고해 주세요."
        )
    elif "근무" in user_message or "출퇴근" in user_message or "시간" in user_message:
        return (
            "근무시간에 대해 안내드립니다. "
            "일반적인 근무시간은 오전 9시부터 오후 6시까지이며, "
            "자세한 내용은 인사팀에 문의해 주세요."
        )
    else:
        return (
            f"'{user_message[:50]}'에 대한 문의 감사합니다. "
            f"해당 내용에 대해서는 담당 부서에서 정확한 안내를 받으실 수 있습니다."
        )


# =============================================================================
# 엔드포인트
# =============================================================================


@app.get("/health")
async def health_check():
    """헬스체크 엔드포인트."""
    return {"status": "ok", "service": "mock-llm", "timestamp": datetime.now().isoformat()}


@app.get("/v1/models", response_model=ModelsResponse)
async def list_models():
    """사용 가능한 모델 목록."""
    return ModelsResponse(
        data=[
            ModelInfo(
                id="gpt-3.5-turbo",
                created=int(time.time()),
                owned_by="mock-llm",
            ),
            ModelInfo(
                id="gpt-4",
                created=int(time.time()),
                owned_by="mock-llm",
            ),
            ModelInfo(
                id="internal-llm",
                created=int(time.time()),
                owned_by="ctrlf",
            ),
        ]
    )


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(request: ChatCompletionRequest):
    """
    채팅 완성 엔드포인트.

    OpenAI API 형식을 시뮬레이션합니다.
    """
    # 상태 업데이트
    state.completion_call_count += 1
    state.last_model = request.model
    state.last_messages = [{"role": m.role, "content": m.content} for m in request.messages]

    logger.info(
        f"[Mock LLM] Completion called: model={request.model}, "
        f"messages_count={len(request.messages)}"
    )

    # 응답 생성
    response_text = generate_response(request.messages)

    # 토큰 수 추정 (간단히 문자 수 기반)
    prompt_tokens = sum(len(m.content) // 4 for m in request.messages)
    completion_tokens = len(response_text) // 4

    logger.info(f"[Mock LLM] Generated response: {response_text[:100]}...")

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:8]}",
        created=int(time.time()),
        model=request.model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role="assistant", content=response_text),
                finish_reason="stop",
            )
        ],
        usage=ChatCompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    """
    서버 통계 엔드포인트 (테스트 검증용).

    통합 테스트에서 LLM이 호출되었는지 확인할 때 사용합니다.
    """
    return StatsResponse(
        completion_call_count=state.completion_call_count,
        last_model=state.last_model,
        last_messages_count=len(state.last_messages),
    )


@app.post("/stats/reset")
async def reset_stats():
    """
    통계 초기화 엔드포인트.

    테스트 간 상태 초기화에 사용합니다.
    """
    state.reset()
    logger.info("[Mock LLM] Stats reset")
    return {"status": "ok", "message": "Stats reset"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
