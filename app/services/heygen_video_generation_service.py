"""
HeyGen 영상 생성 서비스

HeyGen API를 사용하여 교육 영상을 생성하는 서비스입니다.

주요 기능:
1. Heygen Job 생성
2. 상태 폴링
3. 결과 다운로드
4. S3 직접 업로드 (presign 금지)
5. 백엔드 콜백
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.adapters.heygen_script_adapter import convert_script_to_heygen_format
from app.clients.backend_client import (
    BackendClient,
    JobCompleteCallbackError,
    get_backend_client,
)
from app.clients.heygen_client import HeyGenClient, HeyGenError
from app.clients.storage_adapter import (
    S3StorageProvider,
    StorageProvider,
    StorageUploadError,
    get_storage_provider,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.retry import RetryConfig, retry_async_operation
from app.services.video_job_store import (
    VideoJob,
    VideoJobStatus,
    VideoJobStore,
    get_video_job_store,
)

logger = get_logger(__name__)


class HeyGenVideoGenerationService:
    """
    HeyGen 영상 생성 서비스.

    HeyGen API를 사용하여 교육 영상을 생성하고 S3에 업로드합니다.

    Usage:
        service = HeyGenVideoGenerationService()
        
        # 영상 생성 시작
        job = await service.create_video_job(
            job_id="job-123",
            video_id="video-123",
            script_id="script-123",
            education_id="edu-123",
        )
        
        # 백그라운드에서 처리됨
        
        # 상태 조회
        job = service.get_job_status("job-123")
    """

    def __init__(
        self,
        heygen_client: Optional[HeyGenClient] = None,
        storage_provider: Optional[S3StorageProvider] = None,
        backend_client: Optional[BackendClient] = None,
    ):
        """
        서비스 초기화.

        Args:
            heygen_client: HeyGen 클라이언트 (None이면 설정에서 생성)
            storage_provider: S3 Storage Provider (None이면 설정에서 생성)
            backend_client: 백엔드 클라이언트 (None이면 싱글톤 사용)
        """
        settings = get_settings()

        # HeyGen 클라이언트 초기화
        if heygen_client:
            self._heygen = heygen_client
        elif settings.HEYGEN_API_KEY:
            self._heygen = HeyGenClient(
                api_key=settings.HEYGEN_API_KEY,
                timeout=settings.HEYGEN_TIMEOUT_SEC,
                poll_interval=settings.HEYGEN_POLL_INTERVAL_SEC,
                poll_timeout=settings.HEYGEN_POLL_TIMEOUT_SEC,
            )
        else:
            self._heygen = None
            logger.warning("HEYGEN_API_KEY not configured, Heygen integration disabled")

        # Storage Provider 초기화 (S3 직접 업로드용)
        if storage_provider:
            self._storage = storage_provider
        else:
            # S3 직접 업로드를 위해 S3StorageProvider 사용 (presign 금지)
            provider = get_storage_provider(provider=StorageProvider.S3)
            if isinstance(provider, S3StorageProvider):
                self._storage = provider
            else:
                logger.warning(
                    "S3StorageProvider not available, using default provider. "
                    "Direct S3 upload may not work."
                )
                self._storage = provider

        # 백엔드 클라이언트 초기화
        self._backend = backend_client or get_backend_client()

        # Job 상태 저장소 (파일 기반, 서버 재시작 후에도 상태 유지)
        self._job_store = get_video_job_store()

    @property
    def is_configured(self) -> bool:
        """HeyGen이 설정되었는지 확인."""
        return self._heygen is not None

    async def create_video_job(
        self,
        job_id: str,
        video_id: str,
        script_id: str,
        education_id: str,
        script_data: Optional[Dict[str, Any]] = None,
    ) -> VideoJob:
        """
        영상 생성 Job을 생성하고 백그라운드에서 처리 시작.

        Args:
            job_id: Job ID (백엔드에서 발급)
            video_id: 영상 ID
            script_id: 스크립트 ID
            education_id: 교육 ID
            script_data: 스크립트 데이터 (None이면 백엔드에서 조회)

        Returns:
            VideoJob: 생성된 Job 정보

        Raises:
            ValueError: HeyGen이 설정되지 않은 경우
        """
        if not self.is_configured:
            raise ValueError("HeyGen not configured. Set HEYGEN_API_KEY.")

        # Job 생성 (파일 기반 저장소에 저장)
        job = VideoJob(
            job_id=job_id,
            video_id=video_id,
            script_id=script_id,
            education_id=education_id,
            status=VideoJobStatus.PENDING,
        )
        self._job_store.save(job)

        logger.info(
            f"Creating video job: job_id={job_id}, video_id={video_id}, "
            f"script_id={script_id}, education_id={education_id}"
        )

        # 백그라운드에서 처리 시작
        asyncio.create_task(self._process_video_job(job_id, script_data))

        return job

    def get_job_status(self, job_id: str) -> Optional[VideoJob]:
        """
        Job 상태를 조회합니다.

        Args:
            job_id: Job ID

        Returns:
            VideoJob 또는 None
        """
        return self._job_store.get(job_id)

    async def _process_video_job(
        self,
        job_id: str,
        script_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        영상 생성 Job을 처리합니다 (백그라운드).

        1. 스크립트 조회 (script_data가 None인 경우)
        2. Heygen 형식으로 변환
        3. Heygen Job 생성
        4. 폴링
        5. 결과 다운로드
        6. S3 직접 업로드
        7. 백엔드 콜백

        Args:
            job_id: Job ID
            script_data: 스크립트 데이터 (None이면 백엔드에서 조회)
        """
        job = self._job_store.get(job_id)
        if not job:
            logger.error(f"Job not found: {job_id}")
            return

        try:
            # 상태를 PROCESSING으로 변경
            job.status = VideoJobStatus.PROCESSING
            job.updated_at = datetime.utcnow()
            self._job_store.save(job)  # 상태 저장
            logger.info(f"Processing video job: job_id={job_id}")

            # 1. 스크립트 데이터 조회 (없는 경우)
            if script_data is None:
                script_data = await self._fetch_script_from_backend(job.script_id)

            if not script_data:
                raise ValueError(f"Script not found: {job.script_id}")

            # 2. Heygen 형식으로 변환
            settings = get_settings()
            heygen_payload = convert_script_to_heygen_format(
                script_data,
                voice_id=settings.HEYGEN_VOICE_ID,
                avatar_id=settings.HEYGEN_AVATAR_ID,
            )

            logger.info(
                f"HeyGen payload prepared: job_id={job_id}, "
                f"chapters={len(heygen_payload.get('video_inputs', []))}"
            )
            # 디버깅: 첫 번째 video_input 구조 로깅
            if heygen_payload.get("video_inputs"):
                import json
                logger.debug(
                    f"HeyGen payload first video_input: {json.dumps(heygen_payload['video_inputs'][0], indent=2, ensure_ascii=False)}"
                )

            # 3. Heygen Job 생성 (Heygen API 호출)
            # 플로우 요구사항: "ctrlf-ai는 Heygen API 호출로 영상 생성"
            logger.info(
                f"Creating Heygen video job: job_id={job_id}, "
                f"chapters={len(heygen_payload.get('video_inputs', []))}"
            )
            heygen_video_id = await self._heygen.generate_video(heygen_payload)
            job.heygen_video_id = heygen_video_id
            job.updated_at = datetime.utcnow()
            self._job_store.save(job)  # 상태 저장
            logger.info(
                f"HeyGen video job created: job_id={job_id}, "
                f"heygen_video_id={heygen_video_id}"
            )

            # 4. 폴링
            status_data = await self._heygen.poll_video_status(heygen_video_id)
            logger.info(f"HeyGen video completed: job_id={job_id}, heygen_video_id={heygen_video_id}")

            # 5. 비디오 URL 추출
            video_url = None
            if "data" in status_data:
                video_url = status_data["data"].get("video_url") or status_data["data"].get("url")
            elif "video_url" in status_data:
                video_url = status_data["video_url"]
            elif "url" in status_data:
                video_url = status_data["url"]

            if not video_url:
                raise ValueError(f"Video URL not found in HeyGen response: {status_data}")

            job.video_url = video_url
            self._job_store.save(job)  # 상태 저장
            logger.info(f"HeyGen video URL: job_id={job_id}, url={video_url}")

            # 6. 결과 다운로드 및 S3 업로드 (boto3로 직접 업로드, presign 사용 금지)
            # 플로우 요구사항: "Heygen 결과물을 받아 ctrlf-ai가 '파일 형태(mp4)'로 S3에 저장
            # (업로드 presign 사용 금지, 서버가 boto3로 직접 put_object)"
            logger.info(
                f"Starting S3 upload: job_id={job_id}, video_id={job.video_id}, "
                f"script_id={job.script_id}, heygen_video_url={video_url}"
            )
            s3_key = await self._download_and_upload_to_s3(
                job_id=job_id,
                video_id=job.video_id,
                script_id=job.script_id,
                video_url=video_url,
            )
            job.s3_key = s3_key
            job.updated_at = datetime.utcnow()
            self._job_store.save(job)  # 상태 저장
            
            logger.info(
                f"S3 upload completed: job_id={job_id}, s3_key={s3_key}"
            )

            # 7. 영상 길이 추출 (대략적으로 스크립트 길이 사용)
            duration_sec = self._calculate_video_duration(script_data)
            job.duration_sec = duration_sec

            # 8. 완료 상태로 변경
            job.status = VideoJobStatus.COMPLETED
            job.updated_at = datetime.utcnow()
            self._job_store.save(job)  # 상태 저장
            logger.info(
                f"Video job completed: job_id={job_id}, s3_key={s3_key}, "
                f"duration={duration_sec}s"
            )

            # 9. 백엔드 콜백 (재시도 포함)
            await self._notify_backend_complete_with_retry(job)

        except HeyGenError as e:
            logger.error(f"HeyGen error in job {job_id}: {e}")
            job.status = VideoJobStatus.FAILED
            job.fail_reason = f"HeyGen error: {str(e)}"
            job.updated_at = datetime.utcnow()
            self._job_store.save(job)  # 상태 저장
            await self._notify_backend_failed_with_retry(job, str(e))

        except StorageUploadError as e:
            logger.error(f"Storage upload error in job {job_id}: {e}")
            job.status = VideoJobStatus.FAILED
            job.fail_reason = f"Storage upload error: {str(e)}"
            job.updated_at = datetime.utcnow()
            self._job_store.save(job)  # 상태 저장
            await self._notify_backend_failed_with_retry(job, str(e))

        except Exception as e:
            logger.error(f"Unexpected error in job {job_id}: {e}", exc_info=True)
            job.status = VideoJobStatus.FAILED
            job.fail_reason = f"Unexpected error: {str(e)}"
            job.updated_at = datetime.utcnow()
            self._job_store.save(job)  # 상태 저장
            await self._notify_backend_failed_with_retry(job, str(e))

    async def _fetch_script_from_backend(self, script_id: str) -> Optional[Dict[str, Any]]:
        """
        백엔드에서 스크립트 데이터를 조회합니다.

        백엔드의 render-spec API를 사용하여 스크립트를 조회하고,
        내부 스크립트 형식으로 변환합니다.

        Args:
            script_id: 스크립트 ID

        Returns:
            스크립트 데이터 (내부 형식) 또는 None
        """
        try:
            # 백엔드에서 render-spec 조회
            render_spec = await self._backend.get_render_spec(script_id)
            
            # RenderSpec을 내부 스크립트 형식으로 변환
            script_data = self._render_spec_to_script_data(render_spec)
            
            logger.info(
                f"Script fetched from backend: script_id={script_id}, "
                f"title={script_data.get('title')}, chapters={len(script_data.get('chapters', []))}"
            )
            
            return script_data

        except Exception as e:
            logger.error(
                f"Failed to fetch script from backend: script_id={script_id}, error={e}",
                exc_info=True
            )
            return None

    def _render_spec_to_script_data(self, render_spec) -> Dict[str, Any]:
        """
        RenderSpec을 내부 스크립트 형식으로 변환합니다.

        Args:
            render_spec: RenderSpec 객체 (from app.models.render_spec)

        Returns:
            Dict: 내부 스크립트 형식 (chapters/scenes 구조)
        """
        chapters_dict: Dict[str, List[Dict[str, Any]]] = {}
        
        # 씬을 챕터별로 그룹화
        for scene in render_spec.scenes:
            chapter_title = scene.chapter_title or "기본 챕터"
            if chapter_title not in chapters_dict:
                chapters_dict[chapter_title] = []
            
            scene_data = {
                "sceneIndex": scene.scene_order,
                "purpose": scene.purpose,
                "narration": scene.narration,
                "caption": scene.caption,
                "duration_sec": scene.duration_sec,
            }
            
            # visual_spec이 있으면 visual 필드에 추가
            if scene.visual_spec:
                scene_data["visual"] = scene.visual_spec.text or scene.caption
            
            chapters_dict[chapter_title].append(scene_data)

        # 챕터 리스트 생성
        chapters = []
        for chapter_idx, (chapter_title, scenes) in enumerate(chapters_dict.items(), 1):
            # 챕터 duration 계산
            chapter_duration = sum(s.get("duration_sec", 0) for s in scenes)
            
            chapters.append({
                "chapterIndex": chapter_idx - 1,
                "title": chapter_title,
                "duration_sec": chapter_duration,
                "scenes": scenes,
            })

        # 전체 duration 계산
        total_duration = sum(ch["duration_sec"] for ch in chapters)

        return {
            "title": render_spec.title or "교육 영상",
            "total_duration_sec": total_duration or render_spec.total_duration_sec,
            "chapters": chapters,
        }

    async def _download_and_upload_to_s3(
        self,
        job_id: str,
        video_id: str,
        script_id: str,
        video_url: str,
    ) -> str:
        """
        Heygen 결과를 다운로드하고 S3에 직접 업로드합니다 (presign 금지).

        Args:
            job_id: Job ID
            video_id: 영상 ID
            script_id: 스크립트 ID
            video_url: Heygen 비디오 다운로드 URL

        Returns:
            str: S3 key (예: education_videos/{video_id}/{script_id}/{job_id}/video.mp4)

        Raises:
            StorageUploadError: 업로드 실패 시
        """
        logger.info(
            f"Downloading and uploading to S3: job_id={job_id}, video_url={video_url}"
        )

        # 임시 파일 경로
        temp_dir = Path(get_settings().RENDER_OUTPUT_DIR) / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / f"{job_id}_video.mp4"

        try:
            # 1. Heygen에서 다운로드
            downloaded_path = await self._heygen.download_video(video_url, temp_file)
            logger.info(f"Downloaded from Heygen: {downloaded_path}")

            # 2. S3 key 생성
            s3_key = f"education_videos/{video_id}/{script_id}/{job_id}/video.mp4"

            # 3. S3에 직접 업로드 (presign 금지)
            result = await self._storage.put_object(
                data=downloaded_path,
                key=s3_key,
                content_type="video/mp4",
            )

            logger.info(
                f"Uploaded to S3: job_id={job_id}, s3_key={s3_key}, "
                f"size={result.size_bytes}, url={result.url}"
            )

            # 4. 임시 파일 삭제
            if downloaded_path.exists():
                downloaded_path.unlink()
                logger.debug(f"Deleted temp file: {downloaded_path}")

            return s3_key

        except Exception as e:
            # 임시 파일 정리
            if temp_file.exists():
                temp_file.unlink()
            raise StorageUploadError(
                f"Failed to download and upload to S3: {str(e)}",
                s3_key=s3_key if 's3_key' in locals() else "unknown",
                original_error=e,
            )

    def _calculate_video_duration(self, script_data: Dict[str, Any]) -> int:
        """
        스크립트 데이터에서 영상 길이를 계산합니다.

        Args:
            script_data: 스크립트 데이터

        Returns:
            int: 영상 길이 (초)
        """
        total_duration = 0
        
        # 스크립트 구조에 따라 duration 추출
        if "total_duration_sec" in script_data:
            return int(script_data["total_duration_sec"])
        
        if "chapters" in script_data:
            for chapter in script_data["chapters"]:
                if "duration_sec" in chapter:
                    total_duration += int(chapter["duration_sec"])
                elif "scenes" in chapter:
                    for scene in chapter["scenes"]:
                        if "duration_sec" in scene:
                            total_duration += int(scene["duration_sec"])
        
        return max(total_duration, 1)  # 최소 1초

    async def _notify_backend_complete_with_retry(self, job: VideoJob) -> None:
        """
        백엔드에 영상 생성 완료를 알립니다 (재시도 포함).

        플로우 요구사항:
        - 백엔드에는 저장된 S3 key 또는 재생 URL(정책에 맞는 방식) 전달
        - 현재는 재생 URL을 전달하되, S3 key도 로그에 기록

        Args:
            job: 완료된 Job
        """
        if not job.s3_key:
            logger.error(f"S3 key not available for job {job.job_id}, skipping callback")
            return

        # 백엔드 콜백 재시도 설정 (exponential backoff)
        retry_config = RetryConfig(
            max_retries=3,  # 최대 3회 재시도
            base_delay=1.0,  # 첫 재시도 전 1초 대기
            max_delay=10.0,  # 최대 10초 대기
            exponential_base=2,  # 지수 백오프
            retryable_exceptions=(JobCompleteCallbackError, Exception),
        )

        try:
            # S3 재생 URL 생성 (public URL 또는 presigned URL)
            # 플로우 요구사항: "백엔드에는 저장된 S3 key 또는 재생 URL(정책에 맞는 방식) 전달"
            # 현재는 재생 URL을 전달 (정책에 맞는 방식)
            video_url = await self._storage.get_url(job.s3_key)
            
            logger.info(
                f"Preparing backend callback: job_id={job.job_id}, "
                f"s3_key={job.s3_key}, video_url={video_url}, duration={job.duration_sec}s"
            )
            
            # 재시도 로직과 함께 백엔드 콜백 호출
            # 플로우 요구사항: "백엔드에는 저장된 S3 key 또는 재생 URL(정책에 맞는 방식) 전달"
            # 백엔드는 videoUrl만 사용하므로, video_url에 재생 URL 또는 S3 URI를 전달
            await retry_async_operation(
                self._backend.notify_job_complete,
                job_id=job.job_id,
                video_url=video_url,  # 재생 URL (HTTP URL)
                s3_key=job.s3_key,  # S3 key (선택적, 백엔드에서 사용하지 않음)
                duration=job.duration_sec or 0,
                status="COMPLETED",
                config=retry_config,
                operation_name=f"backend_callback_complete_job_{job.job_id}",
            )
            
            logger.info(
                f"Backend callback succeeded: job_id={job.job_id}, "
                f"s3_key={job.s3_key}, video_url={video_url}, duration={job.duration_sec}s"
            )

        except JobCompleteCallbackError as e:
            logger.error(
                f"Backend callback failed after retries: job_id={job.job_id}, "
                f"error={e.message}, status_code={e.status_code}"
            )
            # 콜백 실패는 Job 상태를 변경하지 않음 (이미 COMPLETED)
            # 하지만 로그에 기록하여 모니터링 가능하게 함
        except Exception as e:
            logger.error(
                f"Backend callback unexpected error after retries: job_id={job.job_id}, error={e}",
                exc_info=True
            )

    async def _notify_backend_failed_with_retry(self, job: VideoJob, error_message: str) -> None:
        """
        백엔드에 영상 생성 실패를 알립니다 (재시도 포함).

        Args:
            job: 실패한 Job
            error_message: 에러 메시지
        """
        # 백엔드 콜백 재시도 설정 (exponential backoff)
        retry_config = RetryConfig(
            max_retries=2,  # 최대 2회 재시도 (실패 알림은 덜 중요)
            base_delay=0.5,  # 첫 재시도 전 0.5초 대기
            max_delay=5.0,  # 최대 5초 대기
            exponential_base=2,  # 지수 백오프
            retryable_exceptions=(Exception,),
        )

        try:
            # 백엔드 API에 실패 알림 (필요한 경우 구현)
            # 현재는 로그만 기록하되, 향후 백엔드 API가 추가되면 재시도 로직 적용
            logger.warning(
                f"Job failed (backend notification not implemented): "
                f"job_id={job.job_id}, error={error_message}"
            )
            
            # 향후 백엔드 API가 추가되면:
            # await retry_async_operation(
            #     self._backend.notify_job_failed,
            #     job_id=job.job_id,
            #     error_message=error_message,
            #     config=retry_config,
            #     operation_name=f"backend_callback_failed_job_{job.job_id}",
            # )
        except Exception as e:
            logger.error(
                f"Failed to notify backend of failure: job_id={job.job_id}, error={e}",
                exc_info=True
            )


# 싱글톤 인스턴스
_service: Optional[HeyGenVideoGenerationService] = None


def get_heygen_video_generation_service() -> HeyGenVideoGenerationService:
    """HeyGen 영상 생성 서비스 싱글톤 인스턴스 반환."""
    global _service
    if _service is None:
        _service = HeyGenVideoGenerationService()
    return _service


def clear_heygen_video_generation_service() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _service
    _service = None
