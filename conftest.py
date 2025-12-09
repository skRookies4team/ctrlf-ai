"""
Pytest configuration for ctrlf-ai tests.

Configures pytest-anyio to use only asyncio backend (excludes trio).
"""

import pytest


# Configure anyio to use only asyncio backend
@pytest.fixture
def anyio_backend():
    return "asyncio"
