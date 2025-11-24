"""
LLM 인터페이스 - 추상 클래스 및 구현체들

아키텍처:
- BaseLLM: 추상 인터페이스
- MockLLM: 개발/데모용 더미 구현
- OpenAILLM: OpenAI API 구현 (dev-only, ENABLE_OPENAI=true)
- (향후) UpstageAI, Qwen, Local LLM 등 플러그인 추가 가능
"""

from abc import ABC, abstractmethod
from typing import List, Optional
import os
import logging

logger = logging.getLogger(__name__)


class BaseLLM(ABC):
    """LLM 추상 인터페이스"""

    @abstractmethod
    def generate_answer(
        self,
        query: str,
        context_chunks: List[str],
        max_tokens: int = 500
    ) -> str:
        """
        검색된 청크를 기반으로 답변 생성

        Args:
            query: 사용자 질문
            context_chunks: 검색된 문서 청크들
            max_tokens: 최대 생성 토큰 수

        Returns:
            str: 생성된 답변
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """LLM 사용 가능 여부"""
        pass


class MockLLM(BaseLLM):
    """
    개발/데모용 Mock LLM

    실제 LLM 호출 없이, 검색된 청크 정보를 바탕으로
    템플릿 기반 답변 반환
    """

    def generate_answer(
        self,
        query: str,
        context_chunks: List[str],
        max_tokens: int = 500
    ) -> str:
        """Mock 답변 생성"""
        if not context_chunks:
            return "검색 결과가 없어 답변을 생성할 수 없습니다."

        # 청크 개수와 평균 길이 계산
        num_chunks = len(context_chunks)
        avg_length = sum(len(chunk) for chunk in context_chunks) // num_chunks

        # 첫 번째 청크에서 일부 발췌
        preview = context_chunks[0][:200] + "..." if len(context_chunks[0]) > 200 else context_chunks[0]

        answer = f"""[Mock LLM 답변]

질문: {query}

검색된 문서 {num_chunks}개를 바탕으로 답변드립니다.

관련 내용 발췌:
{preview}

※ 참고: 이 답변은 Mock LLM이 생성한 것입니다.
실제 LLM을 사용하려면 ENABLE_OPENAI=true 환경변수를 설정하거나,
내부 LLM 구현체를 연결하세요.

검색된 청크 개수: {num_chunks}
평균 청크 길이: {avg_length} 자
"""
        return answer

    def is_available(self) -> bool:
        """항상 사용 가능"""
        return True


class OpenAILLM(BaseLLM):
    """
    OpenAI GPT 구현체

    환경변수:
    - ENABLE_OPENAI=true: OpenAI 활성화
    - OPENAI_API_KEY: API 키
    - OPENAI_MODEL: 모델명 (기본: gpt-3.5-turbo)
    """

    def __init__(self):
        self.enabled = os.getenv("ENABLE_OPENAI", "false").lower() == "true"
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
        self.client = None

        if self.enabled:
            if not self.api_key:
                logger.warning("ENABLE_OPENAI=true but OPENAI_API_KEY is not set")
                self.enabled = False
            else:
                try:
                    from openai import OpenAI
                    self.client = OpenAI(api_key=self.api_key)
                    logger.info(f"OpenAI LLM initialized with model: {self.model}")
                except ImportError:
                    logger.error("openai package not installed. Run: pip install openai")
                    self.enabled = False
                except Exception as e:
                    logger.error(f"Failed to initialize OpenAI client: {e}")
                    self.enabled = False

    def generate_answer(
        self,
        query: str,
        context_chunks: List[str],
        max_tokens: int = 500
    ) -> str:
        """OpenAI API로 답변 생성"""
        if not self.is_available():
            raise RuntimeError("OpenAI LLM is not available")

        # 컨텍스트 결합
        context = "\n\n---\n\n".join(context_chunks[:5])  # 최대 5개 청크만 사용

        # 프롬프트 생성
        prompt = f"""다음 문서들을 참고하여 질문에 답변해주세요.

문서 내용:
{context}

질문: {query}

답변 시 유의사항:
1. 문서에 명시된 내용만 사용하세요
2. 문서에 없는 내용은 "문서에서 해당 정보를 찾을 수 없습니다"라고 답하세요
3. 간결하고 명확하게 답변하세요

답변:"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "당신은 문서 기반 질의응답 시스템입니다. 주어진 문서만을 참고하여 정확하게 답변하세요."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=0.3  # 낮은 temperature로 일관성 유지
            )

            answer = response.choices[0].message.content
            logger.info(f"Generated answer for query: {query[:50]}...")
            return answer

        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return f"답변 생성 중 오류가 발생했습니다: {str(e)}"

    def is_available(self) -> bool:
        """OpenAI 사용 가능 여부"""
        return self.enabled and self.client is not None


def get_llm(llm_type: Optional[str] = None) -> BaseLLM:
    """
    LLM 인스턴스 가져오기

    Args:
        llm_type: "mock" | "openai" | None (자동 선택)

    Returns:
        BaseLLM: LLM 인스턴스

    선택 우선순위:
    1. llm_type이 명시된 경우 해당 타입 반환
    2. ENABLE_OPENAI=true인 경우 OpenAI 반환
    3. 그 외 Mock 반환
    """
    if llm_type == "mock":
        logger.info("Using Mock LLM")
        return MockLLM()

    if llm_type == "openai" or os.getenv("ENABLE_OPENAI", "false").lower() == "true":
        openai_llm = OpenAILLM()
        if openai_llm.is_available():
            logger.info("Using OpenAI LLM")
            return openai_llm
        else:
            logger.warning("OpenAI LLM not available, falling back to Mock LLM")
            return MockLLM()

    logger.info("Using Mock LLM (default)")
    return MockLLM()
