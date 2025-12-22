"""
API v1 Package

Version 1 API endpoints.

Included routers:
    - health: Health check endpoints (/health, /health/ready)
    - chat: AI chat endpoints (/ai/chat/messages)
    - chat_stream: Streaming chat endpoints (/ai/chat/stream)
    - gap_suggestions: RAG Gap 보완 제안 endpoints (/ai/gap/policy-edu/suggestions)
    - quiz_generate: 퀴즈 자동 생성 endpoints (/ai/quiz/generate)
    - faq: FAQ 초안 생성 endpoints (/ai/faq/generate)
    - internal_rag: Milvus 직접 인덱싱 endpoints (/internal/rag/*)
    - scripts: 스크립트 CRUD + 편집 endpoints (/api/scripts/*)
    - render_jobs: 렌더 잡 CRUD + 실행 endpoints (/api/videos/*/render-jobs/*)
    - ws_render_progress: WebSocket 렌더 진행률 endpoints (/ws/videos/*/render-progress)
    - source_sets: SourceSet 오케스트레이션 endpoints (/internal/ai/source-sets/*)
"""

from app.api.v1 import (
    chat,
    chat_stream,
    faq,
    gap_suggestions,
    health,
    internal_rag,
    quiz_generate,
    render_jobs,
    scripts,
    source_sets,
    ws_render_progress,
)

__all__ = [
    "health",
    "chat",
    "chat_stream",
    "gap_suggestions",
    "quiz_generate",
    "faq",
    "internal_rag",
    "scripts",
    "render_jobs",
    "ws_render_progress",
    "source_sets",
]
