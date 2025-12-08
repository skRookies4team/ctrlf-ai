"""
LLM Client Module

HTTP client for communicating with internal LLM service.
Handles chat completion requests using OpenAI-compatible API format.

This module provides a client layer for LLM integration:
- generate_chat_completion: Generate chat responses from messages

NOTE: Actual LLM API endpoints are placeholders (TODO).
      Update endpoint paths and payload structures when LLM API spec is finalized.
"""

from typing import Any, Dict, List, Optional

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMClient:
    """
    Client for communicating with internal LLM service.

    Handles HTTP communication with LLM server for chat completions.
    Uses OpenAI-compatible API format (messages array with role/content).
    Uses shared httpx.AsyncClient singleton for connection pooling.

    Attributes:
        _base_url: LLM service base URL from settings
        _client: Shared httpx.AsyncClient instance

    Example:
        client = LLMClient()
        response = await client.generate_chat_completion(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is the leave policy?"}
            ]
        )
    """

    # Default fallback message when LLM is not configured or fails
    FALLBACK_MESSAGE = (
        "LLM service is not configured or unavailable. "
        "This is a fallback response. Please configure LLM_BASE_URL "
        "or check the LLM service status."
    )

    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        Initialize LLMClient.

        Args:
            base_url: LLM service URL. If None, uses LLM_BASE_URL from settings.
            client: httpx.AsyncClient instance. If None, uses shared singleton.
        """
        settings = get_settings()
        self._base_url = base_url or settings.LLM_BASE_URL
        self._client = client or get_async_http_client()

        if not self._base_url:
            logger.warning(
                "LLM_BASE_URL is not configured. "
                "LLM API calls will be skipped and return fallback responses."
            )

    async def generate_chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        """
        Generate chat completion from LLM service.

        Sends messages to LLM and returns the generated response text.
        Uses OpenAI-compatible API format.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
                      role: 'system', 'user', or 'assistant'
                      content: message text
            model: Model name to use (optional, uses server default if not specified)
            temperature: Sampling temperature (0.0 to 1.0, lower = more deterministic)
            max_tokens: Maximum tokens in response

        Returns:
            Generated response text string

        Note:
            - If LLM_BASE_URL is not configured, returns fallback message
            - On HTTP error, returns fallback message (logs error)
        """
        if not self._base_url:
            logger.warning("LLM generate_chat_completion skipped: base_url not configured")
            return self.FALLBACK_MESSAGE

        # TODO: Update endpoint path when LLM API spec is finalized
        # Using OpenAI-compatible endpoint format
        url = f"{self._base_url}/v1/chat/completions"

        # Build request payload (OpenAI-compatible format)
        # TODO: Adjust payload structure to match actual LLM API spec
        payload: Dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if model:
            payload["model"] = model

        logger.info(
            f"Sending chat completion request to LLM: "
            f"messages_count={len(messages)}, model={model or 'default'}"
        )
        logger.debug(f"LLM request payload: {payload}")

        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()

            data = response.json()

            # TODO: Adjust response parsing to match actual LLM response structure
            # Expected structure (OpenAI-compatible):
            # {
            #     "choices": [
            #         {
            #             "message": {
            #                 "role": "assistant",
            #                 "content": "..."
            #             }
            #         }
            #     ]
            # }
            choices = data.get("choices", [])
            if not choices:
                logger.warning("LLM response has no choices")
                return self.FALLBACK_MESSAGE

            message = choices[0].get("message", {})
            content = message.get("content", "")

            if not content:
                logger.warning("LLM response has empty content")
                return self.FALLBACK_MESSAGE

            logger.info(
                f"LLM chat completion success: response_length={len(content)}"
            )
            return content

        except httpx.HTTPStatusError as e:
            logger.error(
                f"LLM chat completion HTTP error: status={e.response.status_code}, "
                f"detail={e.response.text[:200]}"
            )
            return self.FALLBACK_MESSAGE

        except httpx.RequestError as e:
            logger.error(f"LLM chat completion request error: {e}")
            return self.FALLBACK_MESSAGE

        except KeyError as e:
            logger.error(f"LLM response parsing error: missing key {e}")
            return self.FALLBACK_MESSAGE

        except Exception as e:
            logger.exception("LLM chat completion unexpected error")
            return self.FALLBACK_MESSAGE
