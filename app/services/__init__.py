"""
Services package

Business logic layer for AI Gateway.

Modules:
    - chat_service: Chat handling logic (PII masking, intent classification, RAG, LLM)
    - personalization_mapper: SubIntentId -> Q1-Q20 mapping
    - answer_generator: Facts-based answer generation

Note:
    Heavy imports (ChatService 등) are NOT imported at package level
    to avoid import chain side-effects during testing.
    Import directly from submodules when needed:
        from app.services.chat_service import ChatService
"""

# Lightweight exports only - no heavy dependencies
__all__ = [
    # Heavy modules should be imported directly:
    # from app.services.chat_service import ChatService
    # from app.services.personalization_mapper import to_personalization_q
]
