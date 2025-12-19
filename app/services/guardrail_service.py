"""
Guardrail Service

콘텐츠 안전성 검사 서비스 (stub 구현).
실제 구현은 필요 시 추가.
"""

from typing import Optional


class GuardrailService:
    """콘텐츠 안전성 검사 서비스.

    현재는 stub 구현으로, 모든 콘텐츠를 통과시킵니다.
    """

    def __init__(self):
        """서비스 초기화."""
        pass

    def check_input(self, text: str) -> bool:
        """입력 텍스트 안전성 검사.

        Args:
            text: 검사할 텍스트

        Returns:
            bool: True면 안전, False면 차단
        """
        # Stub: 항상 통과
        return True

    def check_output(self, text: str) -> bool:
        """출력 텍스트 안전성 검사.

        Args:
            text: 검사할 텍스트

        Returns:
            bool: True면 안전, False면 차단
        """
        # Stub: 항상 통과
        return True

    def filter_response(self, text: str) -> str:
        """응답 텍스트 필터링.

        Args:
            text: 필터링할 텍스트

        Returns:
            str: 필터링된 텍스트
        """
        # Stub: 그대로 반환
        return text
