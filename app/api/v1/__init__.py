"""
API v1 Package

Version 1 API endpoints.

Included routers:
    - health: Health check endpoints (/health, /health/ready)
    - chat: AI chat endpoints (/ai/chat/messages)
    - rag: RAG document processing endpoints (/ai/rag/process)
"""

from app.api.v1 import chat, health, rag

__all__ = ["health", "chat", "rag"]
