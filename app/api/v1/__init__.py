"""
API v1 Package

Version 1 API endpoints.

Included routers:
    - health: Health check endpoints (/health, /health/ready)
    - chat: AI chat endpoints (/ai/chat/messages)
    - rag: RAG document processing endpoints (/ai/rag/process)
    - gap_suggestions: RAG Gap 보완 제안 endpoints (/ai/gap/policy-edu/suggestions)
"""

from app.api.v1 import chat, gap_suggestions, health, rag

__all__ = ["health", "chat", "rag", "gap_suggestions"]
