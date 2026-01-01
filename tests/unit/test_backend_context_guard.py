# tests/unit/test_backend_context_guard.py
"""
Step 3: Backend Context Guard 테스트

테스트 목표:
1. backend_context 기본 동작 테스트
2. PersonalizationClient: blocked=True면 BackendBlockedError 발생
3. BackendHandler: blocked=True면 BackendBlockedError 발생
4. 컨텍스트 리셋 테스트
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.backend_context import (
    set_backend_blocked,
    is_backend_blocked,
    get_backend_block_reason,
    reset_backend_context,
    check_backend_allowed,
    BackendBlockedError,
    backend_blocked_context,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_context_before_each_test():
    """각 테스트 전후로 backend 컨텍스트를 리셋합니다."""
    reset_backend_context()
    yield
    reset_backend_context()


# =============================================================================
# Test 1: backend_context 기본 동작 테스트
# =============================================================================


class TestBackendContext:
    """backend_context 모듈 기본 기능 테스트."""

    def test_default_not_blocked(self):
        """기본 상태에서 backend가 차단되지 않음."""
        assert is_backend_blocked() is False
        assert get_backend_block_reason() is None

    def test_set_backend_blocked(self):
        """set_backend_blocked로 차단 플래그 설정."""
        set_backend_blocked(True, "FORBIDDEN_BACKEND:rule_001")

        assert is_backend_blocked() is True
        assert get_backend_block_reason() == "FORBIDDEN_BACKEND:rule_001"

    def test_reset_backend_context(self):
        """reset_backend_context로 플래그 리셋."""
        set_backend_blocked(True, "FORBIDDEN_BACKEND:rule_001")
        reset_backend_context()

        assert is_backend_blocked() is False
        assert get_backend_block_reason() is None

    def test_check_backend_allowed_passes_when_not_blocked(self):
        """차단 플래그가 없으면 check_backend_allowed가 통과."""
        # 예외 없이 통과해야 함
        check_backend_allowed("test_component")

    def test_check_backend_allowed_raises_when_blocked(self):
        """차단 플래그가 설정되면 check_backend_allowed가 예외 발생."""
        set_backend_blocked(True, "FORBIDDEN_BACKEND:rule_001")

        with pytest.raises(BackendBlockedError) as exc_info:
            check_backend_allowed("test_component")

        assert "test_component" in str(exc_info.value)
        assert "FORBIDDEN_BACKEND:rule_001" in str(exc_info.value)

    def test_context_manager_blocks_then_resets(self):
        """backend_blocked_context 컨텍스트 매니저가 블록 후 리셋."""
        assert is_backend_blocked() is False

        with backend_blocked_context("FORBIDDEN_BACKEND:rule_002"):
            assert is_backend_blocked() is True
            assert get_backend_block_reason() == "FORBIDDEN_BACKEND:rule_002"

        # 컨텍스트 종료 후 리셋
        assert is_backend_blocked() is False


# =============================================================================
# Test 2: PersonalizationClient 2차 가드 테스트
# =============================================================================


class TestPersonalizationClientBackendGuard:
    """PersonalizationClient의 backend 2차 가드 테스트."""

    @pytest.mark.asyncio
    async def test_resolve_facts_raises_when_blocked(self):
        """blocked=True일 때 PersonalizationClient.resolve_facts()가 BackendBlockedError 발생."""
        from app.clients.personalization_client import PersonalizationClient

        # Given: backend이 차단된 상태
        set_backend_blocked(True, "FORBIDDEN_BACKEND:rule_003")

        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_BACKEND_URL = "http://localhost:8080"
            mock_settings.BACKEND_API_TOKEN = None
            mock_settings.PERSONALIZATION_TIMEOUT = 5

            client = PersonalizationClient()

            # When/Then: BackendBlockedError 발생
            with pytest.raises(BackendBlockedError) as exc_info:
                await client.resolve_facts(
                    sub_intent_id="Q11",
                    user_id="test-user",
                )

            assert "FORBIDDEN_BACKEND:rule_003" in str(exc_info.value)
            assert "PersonalizationClient.resolve_facts" in str(exc_info.value)


# =============================================================================
# Test 3: BackendHandler 2차 가드 테스트
# =============================================================================


class TestBackendHandlerBackendGuard:
    """BackendHandler의 backend 2차 가드 테스트."""

    @pytest.mark.asyncio
    async def test_fetch_for_api_raises_when_blocked(self):
        """blocked=True일 때 BackendHandler.fetch_for_api()가 BackendBlockedError 발생."""
        from app.services.chat.backend_handler import BackendHandler
        from app.models.intent import IntentType, UserRole

        # Given: backend이 차단된 상태
        set_backend_blocked(True, "FORBIDDEN_BACKEND:rule_004")

        # Mock 클라이언트
        mock_backend_client = MagicMock()
        mock_context_formatter = MagicMock()

        handler = BackendHandler(
            backend_data_client=mock_backend_client,
            context_formatter=mock_context_formatter,
        )

        # When/Then: BackendBlockedError 발생
        with pytest.raises(BackendBlockedError) as exc_info:
            await handler.fetch_for_api(
                user_role=UserRole.EMPLOYEE,
                domain="EDU",
                intent=IntentType.EDU_STATUS,
                user_id="test-user",
            )

        assert "FORBIDDEN_BACKEND:rule_004" in str(exc_info.value)
        assert "BackendHandler.fetch_for_api" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_for_mixed_raises_when_blocked(self):
        """blocked=True일 때 BackendHandler.fetch_for_mixed()가 BackendBlockedError 발생."""
        from app.services.chat.backend_handler import BackendHandler
        from app.models.intent import IntentType, UserRole

        # Given: backend이 차단된 상태
        set_backend_blocked(True, "FORBIDDEN_BACKEND:rule_005")

        # Mock 클라이언트
        mock_backend_client = MagicMock()
        mock_context_formatter = MagicMock()

        handler = BackendHandler(
            backend_data_client=mock_backend_client,
            context_formatter=mock_context_formatter,
        )

        # When/Then: BackendBlockedError 발생
        with pytest.raises(BackendBlockedError) as exc_info:
            await handler.fetch_for_mixed(
                user_role=UserRole.ADMIN,
                domain="INCIDENT",
                intent=IntentType.EDU_STATUS,
                user_id="test-user",
            )

        assert "FORBIDDEN_BACKEND:rule_005" in str(exc_info.value)
        assert "BackendHandler.fetch_for_mixed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_for_api_passes_when_not_blocked(self):
        """blocked=False일 때 BackendHandler.fetch_for_api()가 정상 동작."""
        from app.services.chat.backend_handler import BackendHandler
        from app.models.intent import IntentType, UserRole

        # Given: backend이 허용된 상태
        assert is_backend_blocked() is False

        # Mock 클라이언트
        mock_backend_client = MagicMock()
        mock_backend_client.get_employee_edu_status = AsyncMock(
            return_value=MagicMock(success=True, data={"courses": []})
        )
        mock_context_formatter = MagicMock()
        mock_context_formatter.format_edu_status_for_llm = MagicMock(
            return_value="교육 현황 정보"
        )

        handler = BackendHandler(
            backend_data_client=mock_backend_client,
            context_formatter=mock_context_formatter,
        )

        # When: 정상 호출
        result = await handler.fetch_for_api(
            user_role=UserRole.EMPLOYEE,
            domain="EDU",
            intent=IntentType.EDU_STATUS,
            user_id="test-user",
        )

        # Then: 정상 결과 반환
        mock_backend_client.get_employee_edu_status.assert_called_once()
        assert result == "교육 현황 정보"


# =============================================================================
# Test 4: 컨텍스트 리셋 테스트
# =============================================================================


class TestBackendContextReset:
    """backend 컨텍스트 리셋 테스트."""

    def test_reset_clears_blocked_state(self):
        """reset_backend_context()가 blocked 상태를 정상으로 복원."""
        # Given: backend이 차단된 상태
        set_backend_blocked(True, "FORBIDDEN_BACKEND:rule_006")
        assert is_backend_blocked() is True
        assert get_backend_block_reason() == "FORBIDDEN_BACKEND:rule_006"

        # When: 컨텍스트 리셋
        reset_backend_context()

        # Then: 정상 상태로 복원
        assert is_backend_blocked() is False
        assert get_backend_block_reason() is None

    def test_autouse_fixture_ensures_clean_state(self):
        """autouse fixture가 테스트 간 상태 격리를 보장."""
        # Given: autouse fixture로 인해 clean state
        assert is_backend_blocked() is False
        assert get_backend_block_reason() is None

        # When: 이 테스트에서 blocked 설정
        set_backend_blocked(True, "TEST_ISOLATION")

        # Then: 현재 테스트에서는 blocked
        assert is_backend_blocked() is True

    def test_context_isolation_between_tests(self):
        """테스트 간 컨텍스트 격리 확인 (이전 테스트 영향 없음)."""
        # Given/When/Then: 이전 테스트 영향 없음
        assert is_backend_blocked() is False
        assert get_backend_block_reason() is None
