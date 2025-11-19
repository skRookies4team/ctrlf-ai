"""
Unit tests for core.cleaner module
"""
import pytest
from core.cleaner import clean_text


def test_clean_text_basic():
    """기본 텍스트 클리닝 테스트"""
    input_text = "Hello\nWorld"
    result = clean_text(input_text)
    assert "Hello" in result
    assert "World" in result


def test_clean_text_removes_carriage_returns():
    """\\r 문자를 \\n으로 변환하는지 테스트"""
    input_text = "Line1\r\nLine2\rLine3"
    result = clean_text(input_text)
    assert "\r" not in result
    assert "Line1" in result
    assert "Line2" in result
    assert "Line3" in result


def test_clean_text_removes_empty_lines():
    """빈 줄을 제거하는지 테스트"""
    input_text = "Line1\n\n\nLine2\n  \n\t\nLine3"
    result = clean_text(input_text)
    # 연속된 빈 줄이 제거되어야 함
    assert result.count("\n\n") < input_text.count("\n\n")


def test_clean_text_strips_whitespace():
    """각 줄의 앞뒤 공백을 제거하는지 테스트"""
    input_text = "  Line1  \n\t Line2 \t\n   Line3   "
    result = clean_text(input_text)
    lines = result.split("\n")
    for line in lines:
        if line:  # 빈 줄이 아닌 경우
            assert line == line.strip()


def test_clean_text_empty_input():
    """빈 문자열 입력 테스트"""
    result = clean_text("")
    assert result == ""


def test_clean_text_whitespace_only():
    """공백만 있는 입력 테스트"""
    input_text = "   \n  \t  \n   "
    result = clean_text(input_text)
    # 공백만 있는 경우 빈 문자열 또는 매우 짧은 결과
    assert len(result) < len(input_text)


def test_clean_text_preserves_content():
    """실제 내용은 유지되는지 테스트"""
    input_text = """
    제1장 총칙

    제1조 (목적)
    이 규정은 정보보안에 관한 사항을 규정함을 목적으로 한다.

    제2조 (정의)
    이 규정에서 사용하는 용어의 정의는 다음과 같다.
    """
    result = clean_text(input_text)

    # 주요 내용이 보존되는지 확인
    assert "제1장 총칙" in result
    assert "제1조" in result
    assert "목적" in result
    assert "정보보안" in result
    assert "제2조" in result
    assert "정의" in result


def test_clean_text_multiple_spaces():
    """연속된 공백 처리 테스트"""
    input_text = "Word1    Word2     Word3"
    result = clean_text(input_text)
    # 내용은 유지되어야 함
    assert "Word1" in result
    assert "Word2" in result
    assert "Word3" in result
