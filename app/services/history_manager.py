"""
History Manager - 대화 히스토리 관리

안전한 히스토리 truncation 및 토큰 카운팅 로직.

주요 기능:
- truncate_history_safe: 최근 N턴 + 토큰 상한 적용
- count_tokens_safe: 보수적 토큰 카운팅
- 비정상 메시지 구조 대응 (system 메시지, 불완전 페어링 등)
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.chat import ChatMessage

logger = logging.getLogger(__name__)


# =============================================================================
# 설정
# =============================================================================

@dataclass
class HistoryConfig:
    """히스토리 관리 설정"""

    max_turns: int = 4  # 최근 N턴 (user 기준)
    max_tokens: int = 2000  # 히스토리 토큰 상한
    token_counting_mode: str = "char_conservative"  # "tiktoken" | "char_conservative"
    chars_per_token_korean: float = 2.0  # 한국어 토큰 추정치 (보수적)
    chars_per_token_english: float = 3.5  # 영어 토큰 추정치


def get_history_config() -> HistoryConfig:
    """설정에서 HistoryConfig 로드"""
    try:
        from app.core.config import get_settings
        settings = get_settings()
        return HistoryConfig(
            max_turns=getattr(settings, "CHAT_HISTORY_MAX_TURNS", 4),
            max_tokens=getattr(settings, "CHAT_HISTORY_MAX_TOKENS", 2000),
            token_counting_mode=getattr(
                settings, "CHAT_TOKEN_COUNTING_MODE", "char_conservative"
            ),
        )
    except Exception:
        return HistoryConfig()


# =============================================================================
# 토큰 카운팅
# =============================================================================

def count_tokens_safe(
    text: str,
    mode: str = "char_conservative",
    config: Optional[HistoryConfig] = None,
) -> int:
    """
    안전한 토큰 카운팅 (오버플로우 방지 우선)

    Args:
        text: 카운팅할 텍스트
        mode: 카운팅 모드 ("tiktoken" | "char_conservative")
        config: 히스토리 설정 (None이면 기본값 사용)

    Returns:
        int: 추정 토큰 수
    """
    if not text:
        return 0

    config = config or HistoryConfig()

    if mode == "tiktoken":
        try:
            import tiktoken
            # EXAONE은 tiktoken 미지원, cl100k_base로 근사
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError:
            logger.debug("tiktoken not available, falling back to char_conservative")
            mode = "char_conservative"
        except Exception as e:
            logger.warning(f"tiktoken error: {e}, falling back to char_conservative")
            mode = "char_conservative"

    if mode == "char_conservative":
        return _count_tokens_char_conservative(text, config)

    # 최후 fallback
    return len(text) // 2


def _count_tokens_char_conservative(text: str, config: HistoryConfig) -> int:
    """
    문자 기반 보수적 토큰 카운팅

    한국어 비율에 따라 다르게 계산:
    - 한국어 많으면: 2자 = 1토큰 (보수적)
    - 영어 많으면: 3.5자 = 1토큰
    """
    if not text:
        return 0

    # 한국어 문자 비율 계산
    korean_chars = len(re.findall(r"[가-힣]", text))
    total_chars = len(text)

    if total_chars == 0:
        return 0

    korean_ratio = korean_chars / total_chars

    if korean_ratio > 0.5:
        # 한국어 위주: 보수적 계산
        return int(total_chars / config.chars_per_token_korean)
    elif korean_ratio > 0.2:
        # 혼합: 중간 값
        avg_ratio = (config.chars_per_token_korean + config.chars_per_token_english) / 2
        return int(total_chars / avg_ratio)
    else:
        # 영어 위주
        return int(total_chars / config.chars_per_token_english)


def count_messages_tokens(
    messages: List["ChatMessage"],
    mode: str = "char_conservative",
) -> int:
    """메시지 목록의 총 토큰 수 계산"""
    return sum(count_tokens_safe(m.content, mode) for m in messages)


# =============================================================================
# 히스토리 Truncation
# =============================================================================

def truncate_history_safe(
    messages: List["ChatMessage"],
    max_turns: Optional[int] = None,
    max_tokens: Optional[int] = None,
    config: Optional[HistoryConfig] = None,
) -> List["ChatMessage"]:
    """
    안전한 히스토리 truncation

    규칙:
    1. role in {user, assistant}만 필터링 (system 제외)
    2. 끝에서부터 스캔하며 "최근 N개의 user 턴" 기준으로 포함
    3. 토큰 상한 체크
    4. 마지막 user(현재 질문)는 항상 포함

    Args:
        messages: 전체 메시지 목록
        max_turns: 최대 user 턴 수 (None이면 설정에서 로드)
        max_tokens: 토큰 상한 (None이면 설정에서 로드)
        config: 히스토리 설정 (None이면 기본값 사용)

    Returns:
        List[ChatMessage]: truncation된 메시지 목록
    """
    if not messages:
        return messages

    config = config or get_history_config()
    max_turns = max_turns or config.max_turns
    max_tokens = max_tokens or config.max_tokens

    # 1. role 필터링 (user, assistant만)
    filtered = [m for m in messages if m.role in ("user", "assistant")]
    if not filtered:
        return messages

    # 2. 마지막 user 메시지 분리 (현재 질문)
    current_query = None
    if filtered[-1].role == "user":
        current_query = filtered[-1]
        history = filtered[:-1]
    else:
        # 마지막이 assistant면 그대로 히스토리로 처리
        history = filtered

    # 3. 끝에서부터 스캔하며 "최근 N개의 user 턴" 기준으로 포함
    result: List["ChatMessage"] = []
    user_count = 0
    token_count = 0

    # 현재 질문 토큰은 미리 계산 (상한에서 제외하지 않고 별도 관리)
    current_query_tokens = 0
    if current_query:
        current_query_tokens = count_tokens_safe(
            current_query.content,
            config.token_counting_mode,
        )

    # 히스토리 토큰 예산 = 전체 상한 - 현재 질문 토큰
    history_token_budget = max(max_tokens - current_query_tokens, 0)

    for msg in reversed(history):
        msg_tokens = count_tokens_safe(msg.content, config.token_counting_mode)

        # 토큰 상한 체크
        if token_count + msg_tokens > history_token_budget:
            logger.debug(
                f"Token limit reached: {token_count + msg_tokens} > {history_token_budget}"
            )
            break

        # user 턴 카운트
        if msg.role == "user":
            user_count += 1
            if user_count > max_turns:
                logger.debug(f"Turn limit reached: {user_count} > {max_turns}")
                break

        result.append(msg)
        token_count += msg_tokens

    # 4. 순서 복원 + 현재 질문 추가
    result.reverse()
    if current_query:
        result.append(current_query)

    logger.debug(
        f"History truncated: {len(messages)} → {len(result)} messages, "
        f"turns={user_count}, tokens={token_count + current_query_tokens}"
    )

    return result


# =============================================================================
# 히스토리 포맷팅 (LLM 프롬프트용)
# =============================================================================

def format_history_for_prompt(
    messages: List["ChatMessage"],
    include_roles: bool = True,
    max_chars_per_message: int = 500,
) -> str:
    """
    히스토리를 LLM 프롬프트용 문자열로 포맷팅

    Args:
        messages: 메시지 목록
        include_roles: role 라벨 포함 여부
        max_chars_per_message: 메시지당 최대 문자 수

    Returns:
        str: 포맷팅된 히스토리 문자열
    """
    lines = []

    for msg in messages:
        content = msg.content
        if len(content) > max_chars_per_message:
            content = content[:max_chars_per_message] + "..."

        if include_roles:
            role_label = "사용자" if msg.role == "user" else "AI"
            lines.append(f"[{role_label}] {content}")
        else:
            lines.append(content)

    return "\n".join(lines)


def extract_last_user_query(messages: List["ChatMessage"]) -> Optional[str]:
    """마지막 user 메시지 추출"""
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return None


def extract_last_assistant_response(messages: List["ChatMessage"]) -> Optional[str]:
    """마지막 assistant 메시지 추출"""
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.content
    return None


# =============================================================================
# 히스토리 분석 유틸리티
# =============================================================================

@dataclass
class HistoryAnalysis:
    """히스토리 분석 결과"""

    total_messages: int
    user_messages: int
    assistant_messages: int
    total_tokens: int
    avg_user_length: float
    avg_assistant_length: float


def analyze_history(
    messages: List["ChatMessage"],
    config: Optional[HistoryConfig] = None,
) -> HistoryAnalysis:
    """
    히스토리 분석

    Args:
        messages: 메시지 목록
        config: 히스토리 설정

    Returns:
        HistoryAnalysis: 분석 결과
    """
    config = config or HistoryConfig()

    user_msgs = [m for m in messages if m.role == "user"]
    assistant_msgs = [m for m in messages if m.role == "assistant"]

    user_tokens = sum(
        count_tokens_safe(m.content, config.token_counting_mode) for m in user_msgs
    )
    assistant_tokens = sum(
        count_tokens_safe(m.content, config.token_counting_mode) for m in assistant_msgs
    )

    avg_user = user_tokens / len(user_msgs) if user_msgs else 0
    avg_assistant = assistant_tokens / len(assistant_msgs) if assistant_msgs else 0

    return HistoryAnalysis(
        total_messages=len(messages),
        user_messages=len(user_msgs),
        assistant_messages=len(assistant_msgs),
        total_tokens=user_tokens + assistant_tokens,
        avg_user_length=avg_user,
        avg_assistant_length=avg_assistant,
    )
