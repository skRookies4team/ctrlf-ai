"""
PII 마스킹 서비스 (PII Masking Service)

개인식별정보(PII) 검출 및 마스킹을 처리하는 서비스입니다.
GLiNER-PII 기반 내부 마이크로서비스와 HTTP 통신하여 마스킹을 수행합니다.

3단계 마스킹 구조를 지원합니다:
  - INPUT: 사용자 입력/업로드 시 (RAG/LLM 전달 전)
  - OUTPUT: LLM 응답 출력 직전 (사용자에게 전달 전)
  - LOG: 로그/학습 데이터 저장 전 (장기 보관용)

PII HTTP 서비스 API 스펙 (게이트웨이 관점):
  - 베이스 URL: PII_BASE_URL (예: http://pii-service:8003)
  - 엔드포인트: POST {PII_BASE_URL}/mask
  - 요청 JSON: {"text": "...", "stage": "input"|"output"|"log"}
  - 응답 JSON: {
      "original_text": "...",
      "masked_text": "...",
      "has_pii": true|false,
      "tags": [{"entity": "...", "label": "...", "start": N, "end": N}]
    }

Fallback 전략:
  - PII_ENABLED=False 또는 PII_BASE_URL 미설정: 원문 그대로 반환
  - HTTP 에러/타임아웃: 원문 그대로 반환 (시스템 가용성 우선)
  - 빈 문자열/공백: HTTP 호출 없이 원문 그대로 반환
"""

from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.intent import MaskingStage, PiiMaskResult, PiiTag

logger = get_logger(__name__)

# HTTP 요청 타임아웃 (초)
PII_SERVICE_TIMEOUT = 10.0


class PiiService:
    """PII 마스킹 서비스.

    GLiNER-PII 기반 HTTP 서비스와 통신하여 개인정보를 검출하고 마스킹합니다.

    동작 흐름:
    1. 빈 문자열/공백 체크 → 원문 반환
    2. PII_ENABLED/PII_BASE_URL 체크 → 미설정 시 원문 반환
    3. HTTP POST {base_url}/mask 호출
    4. 응답 파싱 및 PiiMaskResult 반환
    5. 에러 발생 시 fallback (원문 반환)

    Attributes:
        _base_url: PII 서비스 기본 URL
        _enabled: PII 마스킹 활성화 여부
        _client: HTTP 클라이언트 (외부 주입 가능, 테스트용)

    Usage:
        service = PiiService()
        result = await service.detect_and_mask(
            "홍길동 010-1234-5678",
            MaskingStage.INPUT
        )
        print(result.masked_text)  # "[PERSON] [PHONE]" (PII 서비스 설정 시)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        enabled: Optional[bool] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """PiiService 초기화.

        Args:
            base_url: PII 서비스 기본 URL. None이면 Settings에서 읽어옴.
            enabled: PII 마스킹 활성화 여부. None이면 Settings에서 읽어옴.
            client: HTTP 클라이언트 (테스트용 mock 주입 가능)
        """
        settings = get_settings()

        # URL 설정: 인자로 전달되면 그것을 사용, 아니면 Settings에서
        if base_url is not None:
            self._base_url = base_url
        elif settings.PII_BASE_URL:
            self._base_url = str(settings.PII_BASE_URL)
        else:
            self._base_url = ""

        # 활성화 여부 설정
        if enabled is not None:
            self._enabled = enabled
        else:
            self._enabled = settings.PII_ENABLED

        # HTTP 클라이언트 (외부 주입 또는 내부 생성)
        self._client = client

    async def detect_and_mask(
        self,
        text: str,
        stage: MaskingStage,
    ) -> PiiMaskResult:
        """텍스트에서 PII를 검출하고 마스킹합니다.

        HTTP PII 서비스를 호출하여 개인정보를 검출하고 마스킹된 결과를 반환합니다.
        마스킹 단계(stage)는 PII 서비스에 그대로 전달되어, 서비스 측에서
        단계별로 다른 마스킹 전략을 적용할 수 있습니다.

        Args:
            text: 마스킹할 텍스트
            stage: 마스킹 단계 (INPUT, OUTPUT, LOG)

        Returns:
            PiiMaskResult: 마스킹 결과
                - original_text: 원본 텍스트
                - masked_text: 마스킹된 텍스트 (PII 미검출 시 원본과 동일)
                - has_pii: PII 검출 여부
                - tags: 검출된 PII 태그 목록

        Note:
            다음 경우 HTTP 호출 없이 원문을 그대로 반환합니다:
            - 입력이 빈 문자열이거나 공백뿐인 경우
            - PII_ENABLED == False
            - PII_BASE_URL이 비어있거나 None

            HTTP 에러 발생 시에도 시스템 안정성을 위해 원문을 반환합니다.
        """
        # 1. 빈 문자열/공백 체크 - HTTP 호출 불필요
        if not text or not text.strip():
            logger.debug(
                f"PII masking skipped for empty/whitespace text (stage={stage.value})"
            )
            return self._create_fallback_result(text)

        # 2. PII 비활성/미설정 체크
        if not self._enabled or not self._base_url:
            logger.debug(
                f"PII disabled or base URL not configured, skipping masking "
                f"(stage={stage.value})"
            )
            return self._create_fallback_result(text)

        # 3. HTTP PII 서비스 호출
        return await self._call_pii_service(text, stage)

    async def _call_pii_service(
        self,
        text: str,
        stage: MaskingStage,
    ) -> PiiMaskResult:
        """외부 PII HTTP 서비스를 호출하여 마스킹을 수행합니다.

        GLiNER-PII 기반 마이크로서비스의 /mask 엔드포인트를 호출합니다.

        Args:
            text: 마스킹할 텍스트
            stage: 마스킹 단계 (INPUT, OUTPUT, LOG)

        Returns:
            PiiMaskResult: 마스킹 결과

        Note:
            에러 발생 시 원문 그대로 반환합니다 (시스템 가용성 우선).
            PII 서비스 장애 상태에서는 민감정보가 그대로 노출될 수 있으나,
            시스템 전체가 멈추는 것보다는 가용성을 우선합니다.
            이 상황은 로그/모니터링으로 빠르게 감지할 수 있도록 합니다.
        """
        # HTTP 클라이언트 준비
        client = self._client
        should_close = False

        if client is None:
            client = httpx.AsyncClient(timeout=PII_SERVICE_TIMEOUT)
            should_close = True

        try:
            # 요청 URL 및 payload 구성
            url = f"{self._base_url.rstrip('/')}/mask"
            payload = {
                "text": text,
                "stage": stage.value,  # "input" / "output" / "log"
            }

            logger.debug(
                f"Calling PII service: url={url}, stage={stage.value}, "
                f"text_length={len(text)}"
            )

            # HTTP POST 요청
            response = await client.post(url, json=payload)
            response.raise_for_status()

            # 응답 JSON 파싱
            data = response.json()

            # PiiTag 목록 변환
            tags = [
                PiiTag(
                    entity=tag.get("entity", ""),
                    label=tag.get("label", ""),
                    start=tag.get("start"),
                    end=tag.get("end"),
                )
                for tag in data.get("tags", [])
            ]

            # PiiMaskResult 생성
            result = PiiMaskResult(
                original_text=data.get("original_text", text),
                masked_text=data.get("masked_text", text),
                has_pii=bool(data.get("has_pii", False)),
                tags=tags,
            )

            # 결과 로깅
            if result.has_pii:
                logger.info(
                    f"PII detected and masked (stage={stage.value}): "
                    f"{len(tags)} entities found"
                )
            else:
                logger.debug(f"No PII detected (stage={stage.value})")

            return result

        except httpx.HTTPStatusError as e:
            # HTTP 4xx/5xx 에러
            logger.error(
                f"PII service HTTP error (stage={stage.value}): "
                f"status={e.response.status_code}, url={e.request.url}"
            )
            return self._create_fallback_result(text)

        except httpx.TimeoutException as e:
            # 타임아웃 에러
            logger.error(
                f"PII service timeout (stage={stage.value}): "
                f"timeout={PII_SERVICE_TIMEOUT}s"
            )
            return self._create_fallback_result(text)

        except httpx.RequestError as e:
            # 네트워크/연결 에러
            logger.error(
                f"PII service request error (stage={stage.value}): "
                f"{type(e).__name__}: {str(e)}"
            )
            return self._create_fallback_result(text)

        except ValueError as e:
            # JSON 파싱 에러
            logger.error(
                f"PII service JSON parsing error (stage={stage.value}): "
                f"{type(e).__name__}: {str(e)}"
            )
            return self._create_fallback_result(text)

        except Exception as e:
            # 기타 예외
            logger.exception(
                f"Unexpected error in PII service (stage={stage.value}): "
                f"{type(e).__name__}: {str(e)}"
            )
            return self._create_fallback_result(text)

        finally:
            # 내부에서 생성한 클라이언트만 닫음
            if should_close and client is not None:
                await client.aclose()

    def _create_fallback_result(self, text: str) -> PiiMaskResult:
        """안전한 폴백 결과를 생성합니다.

        PII 마스킹을 수행할 수 없는 상황(비활성화, 미설정, 에러)에서
        원문을 그대로 반환하여 시스템 전체가 죽지 않도록 합니다.

        Args:
            text: 원본 텍스트

        Returns:
            PiiMaskResult: has_pii=False, masked_text=original_text인 결과
        """
        return PiiMaskResult(
            original_text=text,
            masked_text=text,
            has_pii=False,
            tags=[],
        )


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_pii_service: Optional["PiiService"] = None


def get_pii_service() -> "PiiService":
    """
    PiiService 싱글톤 인스턴스를 반환합니다.

    첫 호출 시 인스턴스를 생성하고, 이후에는 동일 인스턴스를 반환합니다.
    테스트에서는 clear_pii_service()로 초기화할 수 있습니다.

    Returns:
        PiiService: 싱글톤 서비스 인스턴스
    """
    global _pii_service
    if _pii_service is None:
        _pii_service = PiiService()
    return _pii_service


def clear_pii_service() -> None:
    """
    PiiService 싱글톤 인스턴스를 제거합니다 (테스트용).

    테스트 격리를 위해 각 테스트 후 호출하여 싱글톤을 초기화합니다.
    """
    global _pii_service
    _pii_service = None
