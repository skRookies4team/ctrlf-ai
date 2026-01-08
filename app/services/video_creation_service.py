"""
영상 생성 통합 서비스

프론트엔드 파일 업로드부터 영상 생성까지 전체 워크플로우를 통합 관리합니다.

워크플로우:
1. 프론트엔드 → 백엔드: 파일 업로드 요청
2. 백엔드 → S3: 파일 저장
3. 백엔드 → AI 서버: RAG 전처리 요청
4. AI 서버 → RAGFlow: 문서 ingest
5. RAGFlow → Milvus: 전처리된 데이터 저장
6. AI 서버 → Milvus: 데이터 조회
7. AI 서버 → LLM: 스크립트 생성 (2개)
   - 백엔드 저장용 스크립트
   - HeyGen API용 스크립트
8. AI 서버 → 백엔드: 스크립트 저장 콜백
9. AI 서버 → HeyGen: 영상 제작 요청
10. HeyGen → AI 서버: 영상 파일 다운로드
11. AI 서버 → S3: 영상 파일 저장
12. AI 서버 → 백엔드: 영상 URL 전달
"""

import json
import uuid
from typing import Any, Dict, Optional

from app.clients.backend_client import BackendClient, get_backend_client
from app.clients.heygen_client import HeyGenClient
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.source_set_orchestrator import (
    ProcessingStatus,
    get_source_set_orchestrator,
)
from app.services.video_renderer_heygen import HeyGenVideoRenderer
from app.utils.heygen_payload import (
    build_heygen_generate_payload,
    build_heygen_video_inputs,
)
from app.utils.script_enhance import enhance_video_script_for_video

logger = get_logger(__name__)


class VideoCreationService:
    """영상 생성 통합 서비스.

    파일 업로드부터 영상 생성까지 전체 워크플로우를 관리합니다.
    """

    def __init__(
        self,
        backend_client: Optional[BackendClient] = None,
        heygen_renderer: Optional[HeyGenVideoRenderer] = None,
    ):
        """초기화.

        Args:
            backend_client: 백엔드 클라이언트
            heygen_renderer: HeyGen 렌더러
        """
        self._backend_client = backend_client or get_backend_client()
        self._orchestrator = get_source_set_orchestrator()
        self._heygen_renderer = heygen_renderer or HeyGenVideoRenderer()
        self._settings = get_settings()

    async def create_video_from_source_set(
        self,
        source_set_id: str,
        video_id: str,
        education_id: Optional[str] = None,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """소스셋으로부터 영상을 생성합니다.

        전체 워크플로우:
        1. 소스셋 처리 시작 (RAG 전처리 + 스크립트 생성)
        2. 백엔드에 스크립트 저장
        3. HeyGen으로 영상 생성
        4. S3에 영상 저장
        5. 백엔드에 영상 URL 전달

        Args:
            source_set_id: 소스셋 ID
            video_id: 영상 ID
            education_id: 교육 ID (선택)
            request_id: 요청 ID (멱등성)
            trace_id: 추적 ID

        Returns:
            생성 결과:
            {
                "source_set_id": str,
                "video_id": str,
                "script_id": str,
                "video_url": str,
                "s3_uri": str,
                "duration_sec": float,
                "status": "COMPLETED" | "FAILED",
            }
        """
        logger.info(
            f"Starting video creation from source set: "
            f"source_set_id={source_set_id}, video_id={video_id}"
        )

        try:
            # 1. 소스셋 처리 시작 (RAG 전처리 + 스크립트 생성)
            from app.models.source_set import SourceSetStartRequest

            start_request = SourceSetStartRequest(
                video_id=video_id,
                education_id=education_id,
                request_id=request_id or str(uuid.uuid4()),
                trace_id=trace_id,
            )

            # 소스셋 처리 시작 (비동기)
            start_response = await self._orchestrator.start(source_set_id, start_request)

            # 처리 완료 대기
            job = await self._wait_for_script_generation(source_set_id)

            if job.status != ProcessingStatus.COMPLETED:
                raise RuntimeError(
                    f"Script generation failed: {job.error_code} - {job.error_message}"
                )

            if not job.generated_script:
                raise RuntimeError("Generated script is missing")

            generated_script = job.generated_script

            # 2. 백엔드용 스크립트와 HeyGen용 스크립트 분리
            backend_script = self._create_backend_script(generated_script)
            heygen_script = self._create_heygen_script(generated_script)

            # 3. 백엔드에 스크립트 저장
            script_id = str(uuid.uuid4())
            await self._save_script_to_backend(
                video_id=video_id,
                script_id=script_id,
                script=backend_script,
            )

            logger.info(f"Script saved to backend: script_id={script_id}")

            # 4. HeyGen으로 영상 생성
            video_result = await self._create_video_with_heygen(
                video_id=video_id,
                script_id=script_id,
                heygen_script=heygen_script,
            )

            logger.info(
                f"Video created with HeyGen: "
                f"video_id={video_id}, s3_uri={video_result['s3_uri']}"
            )

            # 5. 백엔드에 영상 URL 전달
            await self._notify_video_complete(
                video_id=video_id,
                script_id=script_id,
                video_url=video_result["video_url"],
                s3_uri=video_result["s3_uri"],
                duration_sec=video_result["duration_sec"],
            )

            return {
                "source_set_id": source_set_id,
                "video_id": video_id,
                "script_id": script_id,
                "video_url": video_result["video_url"],
                "s3_uri": video_result["s3_uri"],
                "duration_sec": video_result["duration_sec"],
                "status": "COMPLETED",
            }

        except Exception as e:
            logger.error(
                f"Video creation failed: source_set_id={source_set_id}, "
                f"video_id={video_id}, error={e}",
                exc_info=True,
            )
            return {
                "source_set_id": source_set_id,
                "video_id": video_id,
                "status": "FAILED",
                "error": str(e),
            }

    async def _wait_for_script_generation(
        self, source_set_id: str, timeout_sec: int = 600
    ) -> Any:
        """스크립트 생성 완료 대기.

        Args:
            source_set_id: 소스셋 ID
            timeout_sec: 타임아웃 (초)

        Returns:
            ProcessingJob: 처리 작업
        """
        import asyncio

        start_time = asyncio.get_event_loop().time()
        poll_interval = 2.0

        while True:
            job = self._orchestrator.get_job_status(source_set_id)

            if not job:
                raise RuntimeError(f"Job not found: {source_set_id}")

            if job.status == ProcessingStatus.COMPLETED:
                return job

            if job.status == ProcessingStatus.FAILED:
                raise RuntimeError(
                    f"Script generation failed: {job.error_code} - {job.error_message}"
                )

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout_sec:
                raise TimeoutError(
                    f"Script generation timeout: {source_set_id} "
                    f"(elapsed: {elapsed:.1f}s)"
                )

            await asyncio.sleep(poll_interval)

    def _create_backend_script(
        self, generated_script: Any
    ) -> Dict[str, Any]:
        """백엔드 저장용 스크립트 생성.

        백엔드 DB에 저장할 스크립트 형식으로 변환합니다.

        Args:
            generated_script: 생성된 스크립트 (GeneratedScript)

        Returns:
            백엔드용 스크립트 JSON
        """
        # GeneratedScript를 백엔드 API 스펙에 맞게 변환
        backend_script = {
            "title": generated_script.title,
            "total_duration_sec": generated_script.total_duration_sec,
            "chapters": [],
        }

        for chapter in generated_script.chapters:
            backend_chapter = {
                "title": chapter.title,
                "duration_sec": chapter.duration_sec,
                "scenes": [],
            }

            for scene in chapter.scenes:
                backend_scene = {
                    "scene_id": str(uuid.uuid4()),
                    "purpose": scene.purpose,
                    "narration": scene.narration,
                    "caption": scene.caption,
                    "visual": scene.visual,
                    "duration_sec": scene.duration_sec,
                }

                if hasattr(scene, "source_chunks") and scene.source_chunks:
                    backend_scene["sourceChunkIndexes"] = scene.source_chunks

                backend_chapter["scenes"].append(backend_scene)

            backend_script["chapters"].append(backend_chapter)

        return backend_script

    def _create_heygen_script(
        self, generated_script: Any
    ) -> Dict[str, Any]:
        """HeyGen API용 스크립트 생성.

        HeyGen API에 전달할 스크립트 형식으로 변환합니다.

        Args:
            generated_script: 생성된 스크립트 (GeneratedScript)

        Returns:
            HeyGen용 스크립트 JSON
        """
        # GeneratedScript를 HeyGen 형식으로 변환
        heygen_script = {
            "title": generated_script.title,
            "total_duration_sec": generated_script.total_duration_sec,
            "chapters": [],
        }

        for chapter in generated_script.chapters:
            heygen_chapter = {
                "chapter_id": str(uuid.uuid4()),
                "title": chapter.title,
                "duration_sec": chapter.duration_sec,
                "scenes": [],
            }

            for scene in chapter.scenes:
                # narration이 필수이므로 보정
                narration = scene.narration or scene.caption or "설명입니다."

                heygen_scene = {
                    "scene_id": str(uuid.uuid4()),
                    "scene_type": scene.purpose,
                    "narration": narration,
                    "on_screen_text": scene.caption,
                    "duration_sec": scene.duration_sec,
                }

                heygen_chapter["scenes"].append(heygen_scene)

            heygen_script["chapters"].append(heygen_chapter)

        # 스크립트 강화 (인트로 씬 추가 등)
        enhanced = enhance_video_script_for_video(heygen_script)

        return enhanced

    async def _save_script_to_backend(
        self,
        video_id: str,
        script_id: str,
        script: Dict[str, Any],
    ) -> None:
        """백엔드에 스크립트 저장.

        백엔드 API 스펙: POST /scripts/complete
        - videoId: 영상 컨텐츠 ID
        - script: LLM이 자동 생성한 스크립트 (JSON 객체)
        - version: 스크립트 버전 번호

        Args:
            video_id: 영상 ID
            script_id: 스크립트 ID (생성된 스크립트 ID는 백엔드에서 반환)
            script: 스크립트 JSON 객체
        """
        import httpx

        settings = get_settings()
        base_url = settings.BACKEND_BASE_URL

        if not base_url:
            logger.warning(
                "BACKEND_BASE_URL not configured, skipping script save to backend"
            )
            return

        # 백엔드 API 스펙에 맞게 요청 생성
        url = f"{base_url}/scripts/complete"
        headers = {
            "X-Internal-Token": settings.BACKEND_INTERNAL_TOKEN or "",
            "Content-Type": "application/json",
        }

        request_body = {
            "videoId": video_id,
            "script": script,  # JSON 객체로 전송 (문자열이 아님)
            "version": 1,
        }

        logger.info(
            f"Saving script to backend: video_id={video_id}, script_id={script_id}"
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=request_body,
                )

                if response.status_code not in (200, 201):
                    error_text = response.text[:200]
                    raise RuntimeError(
                        f"Failed to save script to backend: "
                        f"status={response.status_code}, error={error_text}"
                    )

                # 응답에서 scriptId 추출 (백엔드가 생성한 스크립트 ID)
                response_data = response.json()
                saved_script_id = response_data.get("scriptId")
                if saved_script_id:
                    logger.info(
                        f"Script saved to backend: "
                        f"video_id={video_id}, script_id={saved_script_id}"
                    )

        except Exception as e:
            logger.error(
                f"Failed to save script to backend: video_id={video_id}, error={e}",
                exc_info=True,
            )
            raise

    async def _create_video_with_heygen(
        self,
        video_id: str,
        script_id: str,
        heygen_script: Dict[str, Any],
    ) -> Dict[str, Any]:
        """HeyGen으로 영상 생성.

        Args:
            video_id: 영상 ID
            script_id: 스크립트 ID
            heygen_script: HeyGen용 스크립트

        Returns:
            {
                "video_url": str,
                "s3_uri": str,
                "duration_sec": float,
            }
        """
        # HeyGen 렌더러를 사용하여 영상 생성
        job_id = f"heygen-{script_id}"

        # 렌더링 단계별 실행
        from app.models.video_render import RenderStep

        # 1. 스크립트 검증
        await self._heygen_renderer.execute_step(
            RenderStep.VALIDATE_SCRIPT, heygen_script, job_id
        )

        # 2. 영상 렌더링 (HeyGen API 호출)
        await self._heygen_renderer.execute_step(
            RenderStep.COMPOSE_VIDEO, heygen_script, job_id
        )

        # 3. S3 업로드
        await self._heygen_renderer.execute_step(
            RenderStep.UPLOAD_ASSETS, heygen_script, job_id
        )

        # 4. 최종화
        await self._heygen_renderer.execute_step(
            RenderStep.FINALIZE, heygen_script, job_id
        )

        # 렌더링된 에셋 조회
        assets = await self._heygen_renderer.get_rendered_assets(job_id)

        # S3 URI 조회
        s3_uri = self._heygen_renderer.get_s3_uri(job_id)

        return {
            "video_url": assets.mp4_path,  # S3 URL 또는 로컬 경로
            "s3_uri": s3_uri or assets.mp4_path,
            "duration_sec": assets.duration_sec,
        }

    async def _notify_video_complete(
        self,
        video_id: str,
        script_id: str,
        video_url: str,
        s3_uri: str,
        duration_sec: float,
    ) -> None:
        """백엔드에 영상 완료 알림.

        Args:
            video_id: 영상 ID
            script_id: 스크립트 ID
            video_url: 영상 URL
            s3_uri: S3 URI
            duration_sec: 영상 길이 (초)
        """
        # 백엔드 API 스펙에 맞게 콜백 호출
        # API 명세: POST /video/job/{job_id}/complete
        job_id = f"heygen-{script_id}"

        try:
            await self._backend_client.notify_job_complete(
                job_id=job_id,
                video_url=s3_uri,  # S3 URI 사용
                duration=int(duration_sec),
                status="COMPLETED",
            )
            logger.info(
                f"Video complete notification sent: "
                f"video_id={video_id}, job_id={job_id}"
            )
        except Exception as e:
            logger.error(
                f"Failed to notify video complete: "
                f"video_id={video_id}, error={e}",
                exc_info=True,
            )
            # 에러가 발생해도 영상은 생성되었으므로 계속 진행


def get_video_creation_service() -> VideoCreationService:
    """영상 생성 서비스 싱글톤."""
    return VideoCreationService()

