"""
Phase 31: Video Script Generation Service

교육 원문을 입력받아 LLM을 통해 VideoScript JSON을 자동 생성하는 서비스.

주요 기능:
- 교육 원문 → VideoScript JSON 변환 (LLM 호출)
- JSON 파싱 + Pydantic 스키마 검증
- 실패 시 자동 복구 (최대 2회 재시도)

VideoScript JSON 스키마:
{
    "chapters": [
        {
            "chapter_id": int,
            "title": str,
            "scenes": [
                {
                    "scene_id": int,
                    "narration": str,
                    "on_screen_text": str (optional),
                    "duration_sec": float (optional)
                }
            ]
        }
    ]
}
"""

import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from app.clients.llm_client import LLMClient
from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Pydantic Models for Script JSON Validation
# =============================================================================


class SceneSchema(BaseModel):
    """씬 스키마."""
    scene_id: int = Field(..., description="씬 ID (1부터 시작)")
    narration: str = Field(..., min_length=1, description="나레이션 텍스트")
    on_screen_text: Optional[str] = Field(None, description="화면에 표시할 텍스트 (자막/슬라이드)")
    duration_sec: Optional[float] = Field(None, ge=0, description="씬 길이 (초)")


class ChapterSchema(BaseModel):
    """챕터 스키마."""
    chapter_id: int = Field(..., description="챕터 ID (1부터 시작)")
    title: str = Field(..., min_length=1, description="챕터 제목")
    scenes: List[SceneSchema] = Field(..., min_length=1, description="씬 목록")


class VideoScriptSchema(BaseModel):
    """VideoScript JSON 스키마."""
    chapters: List[ChapterSchema] = Field(..., min_length=1, description="챕터 목록")

    def to_raw_json(self) -> Dict[str, Any]:
        """raw_json 형식으로 변환."""
        return self.model_dump()


# =============================================================================
# Generation Options
# =============================================================================


class ScriptGenerationOptions(BaseModel):
    """스크립트 생성 옵션."""
    language: str = Field(default="ko", description="언어 코드 (ko, en 등)")
    target_minutes: float = Field(default=3, ge=1, le=30, description="목표 영상 길이 (분)")
    max_chapters: int = Field(default=5, ge=1, le=10, description="최대 챕터 수")
    max_scenes_per_chapter: int = Field(default=6, ge=1, le=15, description="챕터당 최대 씬 수")
    style: str = Field(default="friendly_security_training", description="스크립트 스타일")


# =============================================================================
# Script Generation Service
# =============================================================================


class VideoScriptGenerationService:
    """교육 원문을 LLM으로 VideoScript JSON으로 변환하는 서비스.

    Usage:
        service = VideoScriptGenerationService()
        raw_json = await service.generate_script(
            video_id="video-001",
            source_text="교육 원문 ...",
            options=ScriptGenerationOptions(target_minutes=5),
        )
    """

    # 최대 재시도 횟수 (첫 시도 + fix 재시도)
    MAX_RETRIES = 2

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        """서비스 초기화.

        Args:
            llm_client: LLM 클라이언트 (테스트용 mock 주입 가능)
        """
        self._llm_client = llm_client or LLMClient()

    async def generate_script(
        self,
        video_id: str,
        source_text: str,
        options: Optional[ScriptGenerationOptions] = None,
    ) -> Dict[str, Any]:
        """교육 원문에서 VideoScript JSON을 생성합니다.

        Args:
            video_id: 비디오 ID (로깅용)
            source_text: 교육 원문 텍스트
            options: 생성 옵션

        Returns:
            Dict[str, Any]: 검증된 VideoScript raw_json

        Raises:
            ValueError: JSON 파싱 또는 스키마 검증 실패 시
        """
        opts = options or ScriptGenerationOptions()

        logger.info(
            f"Generating script: video_id={video_id}, "
            f"source_length={len(source_text)}, "
            f"target_minutes={opts.target_minutes}"
        )

        # 1차 시도: 전체 프롬프트로 생성
        last_error = None
        raw_output = None

        for attempt in range(self.MAX_RETRIES):
            try:
                if attempt == 0:
                    # 1차 시도: 스키마 + 예시 포함
                    prompt = self._build_generation_prompt(source_text, opts)
                else:
                    # 재시도: fix 프롬프트
                    prompt = self._build_fix_prompt(raw_output, str(last_error))

                messages = [
                    {"role": "system", "content": self._get_system_prompt(opts)},
                    {"role": "user", "content": prompt},
                ]

                raw_output = await self._llm_client.generate_chat_completion(
                    messages=messages,
                    temperature=0.3,  # 일관성을 위해 낮은 temperature
                    max_tokens=4096,  # 긴 스크립트 지원
                )

                # JSON 추출 및 파싱
                parsed_json = self._extract_json(raw_output)

                # Pydantic 스키마 검증
                validated = VideoScriptSchema.model_validate(parsed_json)

                logger.info(
                    f"Script generated successfully: video_id={video_id}, "
                    f"chapters={len(validated.chapters)}, "
                    f"attempt={attempt + 1}"
                )

                return validated.to_raw_json()

            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_error = e
                logger.warning(
                    f"Script generation attempt {attempt + 1} failed: "
                    f"video_id={video_id}, error={type(e).__name__}: {e}"
                )
                continue

        # 모든 시도 실패
        error_msg = f"Script generation failed after {self.MAX_RETRIES} attempts"
        logger.error(f"{error_msg}: video_id={video_id}, last_error={last_error}")
        raise ValueError(error_msg, {"reason_code": "SCRIPT_GENERATION_FAILED", "detail": str(last_error)})

    def _get_system_prompt(self, opts: ScriptGenerationOptions) -> str:
        """시스템 프롬프트 생성."""
        style_descriptions = {
            "friendly_security_training": "친근하고 이해하기 쉬운 보안 교육 스타일",
            "formal_compliance": "공식적이고 정확한 컴플라이언스 교육 스타일",
            "engaging_awareness": "흥미롭고 참여를 유도하는 인식 제고 스타일",
        }
        style_desc = style_descriptions.get(opts.style, opts.style)

        return f"""당신은 교육 영상 스크립트 작성 전문가입니다.
주어진 교육 원문을 분석하여 영상 스크립트를 JSON 형식으로 생성해야 합니다.

스타일: {style_desc}
언어: {opts.language}
목표 영상 길이: 약 {opts.target_minutes}분

규칙:
1. 반드시 유효한 JSON만 출력하세요. 다른 텍스트나 설명은 포함하지 마세요.
2. 챕터 수는 최대 {opts.max_chapters}개, 챕터당 씬 수는 최대 {opts.max_scenes_per_chapter}개입니다.
3. narration은 자연스럽게 읽을 수 있도록 작성하세요.
4. on_screen_text는 핵심 키워드나 요약을 포함하세요.
5. 각 씬의 narration 길이를 고려하여 duration_sec을 추정하세요 (한국어 기준 약 150자/분)."""

    def _build_generation_prompt(
        self,
        source_text: str,
        opts: ScriptGenerationOptions,
    ) -> str:
        """생성 프롬프트 구성."""
        schema_example = """{
  "chapters": [
    {
      "chapter_id": 1,
      "title": "챕터 제목",
      "scenes": [
        {
          "scene_id": 1,
          "narration": "나레이션 텍스트...",
          "on_screen_text": "화면에 표시할 핵심 내용",
          "duration_sec": 30.0
        }
      ]
    }
  ]
}"""

        return f"""아래 교육 원문을 분석하여 VideoScript JSON을 생성하세요.

**출력 JSON 스키마:**
```json
{schema_example}
```

**교육 원문:**
```
{source_text}
```

위 원문을 바탕으로 약 {opts.target_minutes}분 분량의 교육 영상 스크립트를 JSON으로 생성하세요.
반드시 유효한 JSON만 출력하세요."""

    def _build_fix_prompt(self, previous_output: str, error: str) -> str:
        """JSON 수정 요청 프롬프트."""
        return f"""이전 출력이 유효한 JSON이 아니었습니다.

**오류 내용:**
{error}

**이전 출력:**
```
{previous_output[:2000]}...
```

반드시 유효한 JSON만 출력하세요. 다른 텍스트나 설명은 포함하지 마세요.
VideoScript JSON 형식을 정확히 따라야 합니다:
- chapters: 배열 (각 챕터에 chapter_id, title, scenes 필수)
- scenes: 배열 (각 씬에 scene_id, narration 필수)"""

    def _extract_json(self, raw_output: str) -> Dict[str, Any]:
        """LLM 출력에서 JSON 추출 및 파싱.

        Args:
            raw_output: LLM 출력 텍스트

        Returns:
            Dict[str, Any]: 파싱된 JSON

        Raises:
            ValueError: JSON 추출 실패
            json.JSONDecodeError: JSON 파싱 실패
        """
        # 빈 출력 체크
        if not raw_output or not raw_output.strip():
            raise ValueError("Empty LLM output")

        text = raw_output.strip()

        # 방법 1: 코드블록 내 JSON 추출
        json_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_block_match:
            json_str = json_block_match.group(1).strip()
            return json.loads(json_str)

        # 방법 2: { } 괄호로 둘러싸인 부분 추출
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            json_str = brace_match.group(0)
            return json.loads(json_str)

        # 방법 3: 전체를 JSON으로 시도
        return json.loads(text)


# =============================================================================
# Singleton Instance
# =============================================================================


_script_gen_service: Optional[VideoScriptGenerationService] = None


def get_video_script_generation_service() -> VideoScriptGenerationService:
    """VideoScriptGenerationService 싱글톤 인스턴스 반환."""
    global _script_gen_service
    if _script_gen_service is None:
        _script_gen_service = VideoScriptGenerationService()
    return _script_gen_service
