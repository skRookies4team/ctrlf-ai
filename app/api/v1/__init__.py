"""
API v1 Package

Version 1 API endpoints.

Included routers:
    - health: Health check endpoints (/health, /health/ready)
    - chat: AI chat endpoints (/ai/chat/messages)
    - rag: RAG document processing endpoints (/ai/rag/process)
    - gap_suggestions: RAG Gap 보완 제안 endpoints (/ai/gap/policy-edu/suggestions)
    - quiz_generate: 퀴즈 자동 생성 endpoints (/ai/quiz/generate)
    - search: 표준 RAG 검색 endpoints (/search) (Phase 18)
"""

from app.api.v1 import chat, gap_suggestions, health, quiz_generate, rag, search

__all__ = ["health", "chat", "rag", "gap_suggestions", "quiz_generate", "search"]
