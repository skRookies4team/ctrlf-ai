"""
API v1 Package

Version 1 API endpoints.

Included routers:
    - health: Health check endpoints (/health, /health/ready)
    - chat: AI chat endpoints (/ai/chat/messages)
    - gap_suggestions: RAG Gap 보완 제안 endpoints (/ai/gap/policy-edu/suggestions)
    - quiz_generate: 퀴즈 자동 생성 endpoints (/ai/quiz/generate)
    - faq: FAQ 초안 생성 endpoints (/ai/faq/generate) (Phase 18)
    - internal_rag: Milvus 직접 인덱싱 endpoints (/internal/rag/*) (Phase 25)

NOTE: rag.py, search.py, ingest.py 제거됨 (Phase 25 internal_rag로 대체)
"""

from app.api.v1 import chat, faq, gap_suggestions, health, quiz_generate

__all__ = ["health", "chat", "gap_suggestions", "quiz_generate", "faq"]
