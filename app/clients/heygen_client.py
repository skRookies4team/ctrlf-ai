"""
HeyGen API Client (확장)

HeyGen API를 사용하여 영상 생성을 수행하는 클라이언트입니다.
- Job 생성
- 상태 폴링
- 결과 다운로드

참고: HeyGen API v2 사용
"""

from __future__ import annotations
import asyncio
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


class HeyGenVideoStatus(str, Enum):
    """HeyGen 비디오 상태."""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class HeyGenError(Exception):
    """HeyGen API 예외."""
    pass


class HeyGenClient:
    """
    HeyGen API 클라이언트 (확장).

    기능:
    1. Job 생성: `generate_video()` - video_id 반환
    2. 상태 조회: `get_video_status()` - 상태 및 진행률
    3. 폴링: `poll_video_status()` - 완료까지 대기
    4. 다운로드: `download_video()` - 결과 영상 다운로드
    """

    BASE_URL = "https://api.heygen.com"
    DEFAULT_TIMEOUT = 60.0
    DEFAULT_POLL_INTERVAL = 5.0  # 폴링 간격 (초)
    DEFAULT_POLL_TIMEOUT = 3600.0  # 폴링 최대 대기 시간 (1시간)

    def __init__(
        self,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> None:
        """
        HeyGen 클라이언트 초기화.

        Args:
            api_key: HeyGen API 키
            timeout: HTTP 요청 타임아웃 (초)
            poll_interval: 폴링 간격 (초)
            poll_timeout: 폴링 최대 대기 시간 (초)
        """
        self.api_key = api_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def _headers(self) -> Dict[str, str]:
        """API 요청 헤더 반환."""
        return {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    async def generate_video(self, payload: Dict[str, Any]) -> str:
        """
        영상 생성 Job을 생성합니다.

        POST /v2/video/generate

        Args:
            payload: HeyGen API 요청 페이로드

        Returns:
            str: video_id (HeyGen에서 발급한 비디오 ID)

        Raises:
            HeyGenError: API 호출 실패 시
        """
        url = f"{self.BASE_URL}/v2/video/generate"
        logger.info(f"Creating HeyGen video job: url={url}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(url, headers=self._headers(), json=payload)
                
                if not r.is_success:
                    error_text = r.text
                    logger.error(
                        f"HeyGen API Error ({r.status_code}): {error_text}"
                    )
                    try:
                        error_json = r.json()
                        if "error" in error_json:
                            error_msg = error_json["error"].get("message", "")
                            if "Voice not found" in error_msg:
                                logger.error(
                                    "Voice not found. Please check HEYGEN_VOICE_ID in .env file."
                                )
                    except:
                        pass
                
                r.raise_for_status()
                data = r.json()

            if "data" not in data or "video_id" not in data["data"]:
                raise HeyGenError(f"Unexpected HeyGen response: {data}")

            video_id = data["data"]["video_id"]
            logger.info(f"HeyGen video job created: video_id={video_id}")
            return video_id

        except httpx.HTTPStatusError as e:
            logger.error(f"HeyGen API HTTP error: {e.response.status_code}, {e.response.text}")
            raise HeyGenError(f"HeyGen API HTTP error: {e.response.status_code}")
        except httpx.TimeoutException as e:
            logger.error(f"HeyGen API timeout: {e}")
            raise HeyGenError(f"HeyGen API timeout after {self.timeout}s")
        except Exception as e:
            logger.error(f"HeyGen API unexpected error: {e}")
            raise HeyGenError(f"HeyGen API error: {str(e)}")

    async def get_video_status(self, video_id: str) -> Dict[str, Any]:
        """
        비디오 상태를 조회합니다.

        GET /v1/video_status.get?video_id={video_id}

        Args:
            video_id: HeyGen video ID

        Returns:
            Dict: 상태 정보 (status, progress, video_url 등)

        Raises:
            HeyGenError: API 호출 실패 시
        """
        url = f"{self.BASE_URL}/v1/video_status.get"
        logger.debug(f"Checking HeyGen video status: video_id={video_id}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(
                    url,
                    headers=self._headers(),
                    params={"video_id": video_id},
                )
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HeyGen status API HTTP error: {e.response.status_code}")
            raise HeyGenError(f"HeyGen status API HTTP error: {e.response.status_code}")
        except httpx.TimeoutException as e:
            logger.error(f"HeyGen status API timeout: {e}")
            raise HeyGenError(f"HeyGen status API timeout after {self.timeout}s")
        except Exception as e:
            logger.error(f"HeyGen status API unexpected error: {e}")
            raise HeyGenError(f"HeyGen status API error: {str(e)}")

    async def poll_video_status(
        self,
        video_id: str,
        poll_interval: Optional[float] = None,
        poll_timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        비디오가 완료될 때까지 상태를 폴링합니다.

        Args:
            video_id: HeyGen video ID
            poll_interval: 폴링 간격 (초, None이면 기본값 사용)
            poll_timeout: 폴링 최대 대기 시간 (초, None이면 기본값 사용)

        Returns:
            Dict: 최종 상태 정보 (status="completed" 또는 "failed")

        Raises:
            HeyGenError: 타임아웃 또는 API 오류 시
        """
        interval = poll_interval or self.poll_interval
        timeout = poll_timeout or self.poll_timeout
        start_time = time.time()

        logger.info(
            f"Polling HeyGen video status: video_id={video_id}, "
            f"interval={interval}s, timeout={timeout}s"
        )

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise HeyGenError(
                    f"HeyGen video polling timeout after {timeout}s: video_id={video_id}"
                )

            try:
                status_data = await self.get_video_status(video_id)
                
                # 응답 형식 파싱
                status = None
                if "data" in status_data:
                    status = status_data["data"].get("status")
                elif "status" in status_data:
                    status = status_data["status"]
                
                if not status:
                    logger.warning(f"Unexpected status response: {status_data}")
                    await asyncio.sleep(interval)
                    continue

                status_lower = status.lower()
                
                if status_lower == HeyGenVideoStatus.COMPLETED.value:
                    logger.info(
                        f"HeyGen video completed: video_id={video_id}, "
                        f"elapsed={elapsed:.1f}s"
                    )
                    return status_data
                elif status_lower == HeyGenVideoStatus.FAILED.value:
                    error_msg = status_data.get("error", {}).get("message", "Unknown error")
                    logger.error(
                        f"HeyGen video failed: video_id={video_id}, error={error_msg}"
                    )
                    raise HeyGenError(f"HeyGen video generation failed: {error_msg}")
                else:
                    # QUEUED or PROCESSING
                    progress = status_data.get("data", {}).get("progress", 0)
                    logger.debug(
                        f"HeyGen video {status}: video_id={video_id}, "
                        f"progress={progress}%, elapsed={elapsed:.1f}s"
                    )
                    await asyncio.sleep(interval)

            except HeyGenError:
                raise
            except Exception as e:
                logger.warning(f"Error polling status: {e}, retrying...")
                await asyncio.sleep(interval)

    async def download_video(
        self,
        video_url: str,
        output_path: Path,
        timeout: Optional[float] = None,
    ) -> Path:
        """
        완료된 비디오를 다운로드합니다.

        Args:
            video_url: HeyGen에서 제공한 비디오 다운로드 URL
            output_path: 저장할 파일 경로
            timeout: 다운로드 타임아웃 (초, None이면 기본값 사용)

        Returns:
            Path: 다운로드된 파일 경로

        Raises:
            HeyGenError: 다운로드 실패 시
        """
        download_timeout = timeout or (self.timeout * 10)  # 다운로드는 더 긴 타임아웃
        logger.info(f"Downloading HeyGen video: url={video_url}, output={output_path}")

        try:
            # 출력 디렉토리 생성
            output_path.parent.mkdir(parents=True, exist_ok=True)

            async with httpx.AsyncClient(timeout=download_timeout) as client:
                async with client.stream("GET", video_url) as response:
                    response.raise_for_status()
                    
                    # 파일에 쓰기
                    with open(output_path, "wb") as f:
                        async for chunk in response.aiter_bytes():
                            f.write(chunk)

            file_size = output_path.stat().st_size
            logger.info(
                f"HeyGen video downloaded: path={output_path}, size={file_size} bytes"
            )
            return output_path

        except httpx.HTTPStatusError as e:
            logger.error(f"HeyGen video download HTTP error: {e.response.status_code}")
            raise HeyGenError(f"HeyGen video download HTTP error: {e.response.status_code}")
        except httpx.TimeoutException as e:
            logger.error(f"HeyGen video download timeout: {e}")
            raise HeyGenError(f"HeyGen video download timeout after {download_timeout}s")
        except Exception as e:
            logger.error(f"HeyGen video download error: {e}")
            raise HeyGenError(f"HeyGen video download error: {str(e)}")

    async def generate_and_wait(
        self,
        payload: Dict[str, Any],
        output_path: Optional[Path] = None,
        poll_interval: Optional[float] = None,
        poll_timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        영상 생성 → 폴링 → 다운로드까지 전체 프로세스를 실행합니다.

        Args:
            payload: HeyGen API 요청 페이로드
            output_path: 다운로드할 파일 경로 (None이면 다운로드 안 함)
            poll_interval: 폴링 간격 (초)
            poll_timeout: 폴링 최대 대기 시간 (초)

        Returns:
            Dict: 최종 결과 (video_id, status, video_url, downloaded_path 등)
        """
        # 1. Job 생성
        video_id = await self.generate_video(payload)

        # 2. 폴링
        status_data = await self.poll_video_status(
            video_id, poll_interval=poll_interval, poll_timeout=poll_timeout
        )

        # 3. 비디오 URL 추출
        video_url = None
        if "data" in status_data:
            video_url = status_data["data"].get("video_url") or status_data["data"].get("url")
        elif "video_url" in status_data:
            video_url = status_data["video_url"]
        elif "url" in status_data:
            video_url = status_data["url"]

        result = {
            "video_id": video_id,
            "status": HeyGenVideoStatus.COMPLETED.value,
            "video_url": video_url,
        }

        # 4. 다운로드 (경로가 제공된 경우)
        if output_path and video_url:
            downloaded_path = await self.download_video(video_url, output_path)
            result["downloaded_path"] = str(downloaded_path)
            result["file_size"] = downloaded_path.stat().st_size

        return result
