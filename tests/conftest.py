"""
pytest conftest.py - Test Configuration

This module configures the test environment to ensure tests run
without external service dependencies (mock/real mode).

Unit tests should run without any external services,
so we set mock URLs to empty strings.
"""

import os

# Set environment variables BEFORE importing any app modules
# This ensures Settings class uses these values

# Disable mock URLs for unit tests (no external services required)
os.environ["RAGFLOW_BASE_URL_MOCK"] = ""
os.environ["LLM_BASE_URL_MOCK"] = ""
os.environ["BACKEND_BASE_URL_MOCK"] = ""

# Remove direct URL env vars (HttpUrl type doesn't accept empty string)
# So we need to unset them entirely
for key in ["RAGFLOW_BASE_URL", "LLM_BASE_URL", "BACKEND_BASE_URL"]:
    os.environ.pop(key, None)

# Set AI_ENV to mock mode (but with empty mock URLs = no external calls)
os.environ["AI_ENV"] = "mock"

# Now clear the settings cache so Settings reloads with new values
# Import AFTER setting env vars to ensure clean state
from app.core.config import clear_settings_cache
clear_settings_cache()
