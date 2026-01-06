"""
PERSONALIZATION_MODE 모드별 회귀 테스트

테스트 케이스:
1. mock 모드: 항상 mock 반환
2. auto 모드: 네트워크 예외일 때만 mock fallback
3. real 모드: base_url 없으면 CONFIG_ERROR, 네트워크 예외면 에러 반환
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx

from app.clients.personalization_client import PersonalizationClient
from app.models.personalization import (
    PersonalizationFacts,
    PersonalizationErrorType,
    PRIORITY_SUB_INTENTS,
)


class TestPersonalizationModeMock:
    """mock 모드 테스트: 항상 mock 반환"""

    @pytest.mark.asyncio
    async def test_mock_mode_returns_mock_data(self):
        """mock 모드에서는 백엔드 호출 없이 항상 mock 데이터 반환"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "mock"
            mock_settings.backend_base_url = "http://localhost:8085"

            client = PersonalizationClient()
            facts = await client.resolve_facts("Q4", "test-user")

            assert facts.error is None
            assert facts.items is not None
            assert len(facts.items) > 0


class TestPersonalizationModeAuto:
    """auto 모드 테스트: 네트워크 예외일 때만 mock fallback"""

    @pytest.mark.asyncio
    async def test_auto_mode_network_error_fallback_to_mock(self):
        """auto 모드에서 ConnectError 발생 시 mock fallback"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "auto"
            mock_settings.backend_base_url = "http://localhost:8085"

            with patch("app.clients.personalization_client.get_async_http_client") as mock_client:
                mock_client.return_value.post = AsyncMock(
                    side_effect=httpx.ConnectError("Connection refused")
                )

                client = PersonalizationClient()
                facts = await client.resolve_facts("Q4", "test-user")

                # auto 모드에서 네트워크 에러 시 mock fallback
                assert facts.error is None
                assert facts.items is not None

    @pytest.mark.asyncio
    async def test_auto_mode_timeout_fallback_to_mock(self):
        """auto 모드에서 ReadTimeout 발생 시 mock fallback"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "auto"
            mock_settings.backend_base_url = "http://localhost:8085"

            with patch("app.clients.personalization_client.get_async_http_client") as mock_client:
                mock_client.return_value.post = AsyncMock(
                    side_effect=httpx.ReadTimeout("Read timed out")
                )

                client = PersonalizationClient()
                facts = await client.resolve_facts("Q4", "test-user")

                # auto 모드에서 타임아웃 시 mock fallback
                assert facts.error is None
                assert facts.items is not None

    @pytest.mark.asyncio
    async def test_auto_mode_write_timeout_fallback_to_mock(self):
        """auto 모드에서 WriteTimeout 발생 시 mock fallback (httpx.TimeoutException 커버리지)"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "auto"
            mock_settings.backend_base_url = "http://localhost:8085"

            with patch("app.clients.personalization_client.get_async_http_client") as mock_client:
                mock_client.return_value.post = AsyncMock(
                    side_effect=httpx.WriteTimeout("Write timed out")
                )

                client = PersonalizationClient()
                facts = await client.resolve_facts("Q4", "test-user")

                # WriteTimeout도 TimeoutException 하위이므로 mock fallback
                assert facts.error is None
                assert facts.items is not None

    @pytest.mark.asyncio
    async def test_auto_mode_pool_timeout_fallback_to_mock(self):
        """auto 모드에서 PoolTimeout 발생 시 mock fallback (httpx.TimeoutException 커버리지)"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "auto"
            mock_settings.backend_base_url = "http://localhost:8085"

            with patch("app.clients.personalization_client.get_async_http_client") as mock_client:
                mock_client.return_value.post = AsyncMock(
                    side_effect=httpx.PoolTimeout("Pool timed out")
                )

                client = PersonalizationClient()
                facts = await client.resolve_facts("Q4", "test-user")

                # PoolTimeout도 TimeoutException 하위이므로 mock fallback
                assert facts.error is None
                assert facts.items is not None

    @pytest.mark.asyncio
    async def test_auto_mode_unexpected_error_returns_error(self):
        """auto 모드에서 기타 예외 발생 시 에러 반환 (mock 아님)"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "auto"
            mock_settings.backend_base_url = "http://localhost:8085"

            with patch("app.clients.personalization_client.get_async_http_client") as mock_client:
                mock_client.return_value.post = AsyncMock(
                    side_effect=ValueError("JSON parse error")
                )

                client = PersonalizationClient()
                facts = await client.resolve_facts("Q4", "test-user")

                # 기타 예외는 mock으로 숨기지 않고 에러 반환
                assert facts.error is not None
                assert facts.error.type == PersonalizationErrorType.UNEXPECTED_ERROR.value
                assert facts.items == []
                assert facts.metrics == {}

    @pytest.mark.asyncio
    async def test_auto_mode_no_base_url_returns_mock(self):
        """auto 모드에서 base_url 없으면 mock 반환"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "auto"
            mock_settings.backend_base_url = None

            client = PersonalizationClient(base_url=None)
            facts = await client.resolve_facts("Q4", "test-user")

            assert facts.error is None
            assert facts.items is not None


class TestPersonalizationModeReal:
    """real 모드 테스트: base_url 없으면 CONFIG_ERROR, 네트워크 예외면 에러 반환"""

    @pytest.mark.asyncio
    async def test_real_mode_no_base_url_returns_config_error(self):
        """real 모드에서 base_url 없으면 CONFIG_ERROR"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "real"
            mock_settings.backend_base_url = None
            mock_settings.BACKEND_API_TOKEN = None

            client = PersonalizationClient(base_url=None)
            facts = await client.resolve_facts("Q4", "test-user")

            assert facts.error is not None
            assert facts.error.type == PersonalizationErrorType.CONFIG_ERROR.value
            assert "BACKEND_BASE_URL" in facts.error.message
            assert facts.items == []
            assert facts.metrics == {}

    @pytest.mark.asyncio
    async def test_real_mode_network_error_returns_error(self):
        """real 모드에서 네트워크 에러 시 에러 반환 (mock 아님)"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "real"
            mock_settings.backend_base_url = "http://localhost:8085"

            with patch("app.clients.personalization_client.get_async_http_client") as mock_client:
                mock_client.return_value.post = AsyncMock(
                    side_effect=httpx.ConnectError("Connection refused")
                )

                client = PersonalizationClient()
                facts = await client.resolve_facts("Q4", "test-user")

                # real 모드에서는 네트워크 에러도 mock이 아닌 에러 반환
                assert facts.error is not None
                assert facts.error.type == PersonalizationErrorType.NETWORK_ERROR.value
                assert facts.items == []

    @pytest.mark.asyncio
    async def test_real_mode_timeout_returns_error(self):
        """real 모드에서 타임아웃 시 TIMEOUT 에러 반환"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "real"
            mock_settings.backend_base_url = "http://localhost:8085"

            with patch("app.clients.personalization_client.get_async_http_client") as mock_client:
                mock_client.return_value.post = AsyncMock(
                    side_effect=httpx.ReadTimeout("Read timed out")
                )

                client = PersonalizationClient()
                facts = await client.resolve_facts("Q4", "test-user")

                assert facts.error is not None
                assert facts.error.type == PersonalizationErrorType.TIMEOUT.value
                assert facts.items == []

    @pytest.mark.asyncio
    async def test_real_mode_http_500_returns_http_error(self):
        """real 모드에서 HTTP 500 응답 시 HTTP_ERROR 반환"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "real"
            mock_settings.backend_base_url = "http://localhost:8085"

            with patch("app.clients.personalization_client.get_async_http_client") as mock_client:
                # HTTP 500 Internal Server Error 응답 mock
                mock_response = MagicMock()
                mock_response.status_code = 500
                mock_response.text = "Internal Server Error"
                mock_client.return_value.post = AsyncMock(return_value=mock_response)

                client = PersonalizationClient()
                facts = await client.resolve_facts("Q4", "test-user")

                # HTTP 500은 HTTP_ERROR로 분류
                assert facts.error is not None
                assert facts.error.type == PersonalizationErrorType.HTTP_ERROR.value
                assert "500" in facts.error.message
                assert facts.items == []
                assert facts.metrics == {}

    @pytest.mark.asyncio
    async def test_real_mode_http_503_returns_http_error(self):
        """real 모드에서 HTTP 503 응답 시 HTTP_ERROR 반환"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "real"
            mock_settings.backend_base_url = "http://localhost:8085"

            with patch("app.clients.personalization_client.get_async_http_client") as mock_client:
                # HTTP 503 Service Unavailable 응답 mock
                mock_response = MagicMock()
                mock_response.status_code = 503
                mock_response.text = "Service Unavailable"
                mock_client.return_value.post = AsyncMock(return_value=mock_response)

                client = PersonalizationClient()
                facts = await client.resolve_facts("Q4", "test-user")

                # HTTP 503도 HTTP_ERROR로 분류
                assert facts.error is not None
                assert facts.error.type == PersonalizationErrorType.HTTP_ERROR.value
                assert "503" in facts.error.message
                assert facts.items == []
                assert facts.metrics == {}


class TestErrorResponseSchema:
    """에러 응답 스키마 일관성 테스트"""

    @pytest.mark.asyncio
    async def test_all_error_responses_have_items_and_metrics(self):
        """모든 에러 응답에 items=[], metrics={} 포함 확인"""
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "real"
            mock_settings.backend_base_url = None
            mock_settings.BACKEND_API_TOKEN = None

            client = PersonalizationClient(base_url=None)
            facts = await client.resolve_facts("Q4", "test-user")

            # 에러 응답에도 items, metrics 기본값 존재
            assert facts.items == []
            assert facts.metrics == {}
            assert facts.error is not None
