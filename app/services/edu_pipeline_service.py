"""
교육 영상 파이프라인 서비스

실제 동작하는 교육 영상 제작 파이프라인:
1. 스크립트 생성: RAGFLOW 전처리 → Milvus 검색 → 스크립트 2종 생성
2. 영상 생성: Heygen job 생성 → 폴링 → 다운로드 → S3 업로드
"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.clients.backend_client import BackendClient, get_backend_client
from app.clients.heygen_client import HeyGenClient, HeyGenError
from app.clients.ragflow_client import RagflowClient, get_ragflow_client
from app.clients.ragflow_ingest_client import get_ragflow_ingest_client
from app.clients.storage_adapter import BaseStorageProvider, get_storage_provider
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.store.job_store import (
    PipelineJob,
    ScriptStatus,
    SourceSetStatus,
    VideoJobStatus,
    get_job_store,
)
from app.services.chat.rag_handler import RagHandler
from app.services.scene_based_script_generator import SceneBasedScriptGenerator
from app.utils.heygen_payload import build_heygen_generate_payload, build_heygen_video_inputs

logger = get_logger(__name__)


class EducationPipelineService:
    """교육 영상 파이프라인 서비스."""
    
    def __init__(
        self,
        job_store=None,
        backend_client: Optional[BackendClient] = None,
        ragflow_client=None,
        rag_handler: Optional[RagHandler] = None,
        heygen_client: Optional[HeyGenClient] = None,
        storage_provider: Optional[BaseStorageProvider] = None,
    ):
        self._job_store = job_store or get_job_store()
        self._backend_client = backend_client or get_backend_client()
        self._ragflow_client = ragflow_client or get_ragflow_client()
        self._rag_handler = rag_handler or RagHandler()
        self._storage_provider = storage_provider or get_storage_provider()
        
        settings = get_settings()
        if heygen_client:
            self._heygen_client = heygen_client
        else:
            heygen_api_key = getattr(settings, "HEYGEN_API_KEY", None)
            if heygen_api_key:
                self._heygen_client = HeyGenClient(api_key=heygen_api_key)
            else:
                self._heygen_client = None
                logger.warning("HEYGEN_API_KEY not configured, video generation will fail")
    
    async def start_script_generation(
        self,
        source_set_id: str,
        video_id: str,
        education_id: str,
        s3_urls: List[str],
        metadata: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> PipelineJob:
        """
        스크립트 생성 파이프라인 시작.
        
        Args:
            source_set_id: 소스셋 ID
            video_id: 영상 ID
            education_id: 교육 ID
            s3_urls: S3 URL 목록 (백엔드가 업로드한 문서들)
            metadata: 메타데이터 (부서/카테고리/템플릿/언어 등)
            request_id: 요청 ID (멱등성)
            trace_id: 추적 ID
        
        Returns:
            PipelineJob: 생성된 Job
        """
        # Idempotency 체크
        existing_job = await self._job_store.get(source_set_id)
        if existing_job:
            if existing_job.source_set_status in (SourceSetStatus.SCRIPT_READY, SourceSetStatus.FAILED):
                logger.info(f"Job already exists: source_set_id={source_set_id}, status={existing_job.source_set_status.value}")
                return existing_job
            # PROCESSING 상태면 재시작 허용하지 않음
            if existing_job.source_set_status == SourceSetStatus.PROCESSING:
                logger.warning(f"Job already processing: source_set_id={source_set_id}")
                return existing_job
        
        # Job 생성
        job = PipelineJob(
            source_set_id=source_set_id,
            video_id=video_id,
            education_id=education_id,
            source_set_status=SourceSetStatus.PROCESSING,
            script_status=ScriptStatus.PENDING,
            request_id=request_id or str(uuid.uuid4()),
            trace_id=trace_id or str(uuid.uuid4()),
            progress=0,
        )
        await self._job_store.save(job)
        
        # RAGFLOW 전처리 시작 (비동기)
        asyncio.create_task(self._start_ragflow_preprocessing(job, s3_urls, metadata))
        
        logger.info(
            f"Script generation started: source_set_id={source_set_id}, "
            f"video_id={video_id}, s3_urls_count={len(s3_urls)}"
        )
        
        return job
    
    async def _start_ragflow_preprocessing(
        self,
        job: PipelineJob,
        s3_urls: List[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """RAGFLOW 전처리 시작."""
        try:
            from app.clients.ragflow_ingest_client import RAGFlowIngestClient, get_ragflow_ingest_client
            from app.core.config import get_settings
            from urllib.parse import urlparse, unquote
            
            settings = get_settings()
            ingest_client = get_ragflow_ingest_client()
            
            # 도메인 결정 (metadata에서 가져오거나 기본값)
            domain = (metadata or {}).get("domain", "EDUCATION")
            
            # RAGFlow dataset_id 매핑
            dataset_id = self._ragflow_client._dataset_to_kb_id(domain)
            if not dataset_id:
                # 환경변수에서 매핑 확인
                mapping_str = getattr(settings, "MILVUS_DATASET_MAPPING", "")
                if mapping_str:
                    for pair in mapping_str.split(","):
                        if ":" in pair:
                            key, value = pair.split(":", 1)
                            if key.strip() == domain:
                                dataset_id = value.strip()
                                break
                
                if not dataset_id:
                    raise ValueError(f"Domain '{domain}'에 대한 dataset 매핑이 없습니다.")
            
            # 각 S3 URL에 대해 RAGFLOW ingest 요청
            ingest_ids = []
            for idx, s3_url in enumerate(s3_urls):
                try:
                    # S3 URL에서 파일명 추출
                    parsed = urlparse(s3_url)
                    file_name = unquote(parsed.path.split("/")[-1]) or f"document_{idx}.pdf"
                    
                    # doc_id는 파일명 사용 (확장자 제거)
                    doc_id = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
                    
                    # RAGFLOW ingest 요청
                    ingest_result = await ingest_client.ingest(
                        dataset_id=dataset_id,
                        doc_id=doc_id,
                        version=1,
                        file_url=s3_url,
                        rag_document_pk=f"{job.source_set_id}-{idx}",  # 임시 PK
                        domain=domain,
                        trace_id=job.trace_id or "",
                        request_id=job.request_id or "",
                        department=(metadata or {}).get("department"),
                    )
                    
                    ingest_id = ingest_result.get("ingestId")
                    if ingest_id:
                        ingest_ids.append(ingest_id)
                        logger.info(
                            f"RAGFLOW ingest started: source_set_id={job.source_set_id}, "
                            f"doc_id={doc_id}, ingest_id={ingest_id}"
                        )
                    
                    # 진행률 업데이트
                    job.progress = int((idx + 1) / len(s3_urls) * 30)  # 0-30% (전처리 단계)
                    await self._job_store.save(job)
                    
                except Exception as e:
                    logger.error(
                        f"RAGFLOW ingest failed for document {idx}: source_set_id={job.source_set_id}, "
                        f"s3_url={s3_url}, error={e}",
                        exc_info=True
                    )
                    # 개별 문서 실패는 전체 실패로 처리하지 않고 계속 진행
                    # (RAGFLOW 콜백에서 실패 처리)
            
            if not ingest_ids:
                raise ValueError("모든 문서의 RAGFLOW ingest가 실패했습니다.")
            
            logger.info(
                f"RAGFLOW preprocessing started: source_set_id={job.source_set_id}, "
                f"documents={len(s3_urls)}, ingest_ids={len(ingest_ids)}"
            )
            # 전처리는 RAGFLOW 콜백으로 완료 통지됨
            
        except Exception as e:
            logger.error(f"RAGFLOW preprocessing failed: source_set_id={job.source_set_id}, error={e}", exc_info=True)
            job.source_set_status = SourceSetStatus.FAILED
            job.fail_reason = f"RAGFLOW preprocessing failed: {str(e)}"
            job.progress = 0
            await self._job_store.save(job)
            await self._notify_backend_preprocess_failed(job)
    
    async def handle_ragflow_callback(
        self,
        source_set_id: str,
        status: str,
        progress: int,
        fail_reason: Optional[str] = None,
        milvus_collection: Optional[str] = None,
        milvus_partition: Optional[str] = None,
        video_id: Optional[str] = None,
        education_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        RAGFLOW 콜백 수신 (전처리 완료/실패).
        
        Args:
            source_set_id: 소스셋 ID
            status: 상태 (COMPLETED, FAILED)
            progress: 진행률 (0-100)
            fail_reason: 실패 사유
            milvus_collection: Milvus 컬렉션명
            milvus_partition: Milvus 파티션명
            video_id: 영상 ID (job이 없을 때 자동 생성용)
            education_id: 교육 ID (job이 없을 때 자동 생성용)
            metadata: 메타데이터 (job이 없을 때 자동 생성용)
        """
        job = await self._job_store.get(source_set_id)
        if not job:
            # Job이 없으면 자동 생성 (백엔드가 /pipeline/script/start를 호출하지 않은 경우)
            logger.warning(
                f"Job not found for RAGFLOW callback: source_set_id={source_set_id}. "
                f"Creating job automatically from callback."
            )
            
            # 최소한의 정보로 job 생성
            job = PipelineJob(
                source_set_id=source_set_id,
                video_id=video_id or source_set_id,  # video_id가 없으면 source_set_id 사용
                education_id=education_id,
                source_set_status=SourceSetStatus.PREPROCESSING_COMPLETED if status == "COMPLETED" else SourceSetStatus.FAILED,
                script_status=ScriptStatus.PENDING,
                progress=progress,
                fail_reason=fail_reason,
                metadata=metadata or {},
                request_id=str(uuid.uuid4()),
                trace_id=f"trace-{source_set_id}",
            )
            await self._job_store.save(job)
            logger.info(
                f"Auto-created job from RAGFLOW callback: source_set_id={source_set_id}, "
                f"video_id={job.video_id}, status={status}"
            )
        
        if status == "COMPLETED":
            job.source_set_status = SourceSetStatus.PREPROCESSING_COMPLETED
            job.progress = progress
            await self._job_store.save(job)
            
            # 스크립트 생성 시작
            asyncio.create_task(self._generate_scripts(job, milvus_collection, milvus_partition))
        else:
            job.source_set_status = SourceSetStatus.FAILED
            job.fail_reason = fail_reason or "RAGFLOW preprocessing failed"
            job.progress = 0
            await self._job_store.save(job)
            await self._notify_backend_preprocess_failed(job)
    
    async def _generate_scripts(
        self,
        job: PipelineJob,
        milvus_collection: Optional[str] = None,
        milvus_partition: Optional[str] = None,
    ) -> None:
        """스크립트 2종 생성 (백엔드용 + Heygen용)."""
        try:
            job.script_status = ScriptStatus.GENERATING
            job.source_set_status = SourceSetStatus.SCRIPT_GENERATING
            job.progress = 30
            await self._job_store.save(job)
            
            # RAG 검색을 통해 source_set_id 기준으로 청크 검색
            # Note: source_set_id는 RAGFLOW가 메타데이터에 저장했을 것으로 가정
            # 실제로는 doc_id나 다른 필드로 필터링할 수도 있음
            logger.info(f"Searching RAG for source_set_id: {job.source_set_id}")
            
            # 대표 쿼리로 검색 (전체 문서 요약용)
            sample_query = "교육 영상 스크립트 생성"
            
            # RagHandler를 사용하여 검색 수행
            retrieval_result = await self._rag_handler.perform_search_with_fallback(
                query=sample_query,
                domain="EDUCATION",  # metadata에서 가져올 수도 있음
                req=None,
                top_k=50,  # 충분한 청크 수 확보
            )
            
            sources = retrieval_result.sources
            
            # 검색 결과를 document_chunks 형식으로 변환
            # ChatSource를 원래 형식으로 변환
            document_chunks: Dict[str, List[Dict[str, Any]]] = {}
            for source in sources:
                doc_id = source.doc_id or source.title or "unknown"
                if doc_id not in document_chunks:
                    document_chunks[doc_id] = []
                
                document_chunks[doc_id].append({
                    "chunk_index": len(document_chunks[doc_id]),
                    "chunk_text": source.snippet or "",
                    "score": source.score or 0.0,
                    "metadata": {
                        "doc_id": source.doc_id,
                        "title": source.title,
                        "page": source.page,
                        "article_label": source.article_label,
                        "article_path": source.article_path,
                    },
                })
            
            logger.info(
                f"Milvus search completed: source_set_id={job.source_set_id}, "
                f"documents={len(document_chunks)}, total_chunks={sum(len(chunks) for chunks in document_chunks.values())}"
            )
            
            if not document_chunks:
                raise ValueError("Milvus에서 검색 결과가 없습니다. RAGFLOW 전처리가 완료되었는지 확인하세요.")
            
            # 문서 정보 준비
            documents = [
                {
                    "document_id": doc_id,
                    "title": doc_id,  # 실제로는 메타데이터에서 가져와야 함
                    "domain": "EDUCATION",
                }
                for doc_id in document_chunks.keys()
            ]
            
            # 스크립트 생성 (씬 단위 RAG 방식)
            # Note: SceneBasedScriptGenerator는 allowed 파일이므로 milvus_client=None을 전달하면
            # 내부에서 자동으로 get_milvus_client()를 호출함
            generator = SceneBasedScriptGenerator(
                milvus_client=None,
                model="LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct",
                top_k=5,
            )
            
            # 스크립트 생성
            generated_script = await generator.generate_script(
                source_set_id=job.source_set_id,
                video_id=job.video_id,
                education_id=job.education_id,
                documents=documents,
                document_chunks=document_chunks,
            )
            
            # 백엔드용 스크립트 (GeneratedScript 모델을 dict로 변환, alias 사용)
            script_backend = generated_script.model_dump(by_alias=True, exclude={"heygen_script"})
            
            # Heygen용 스크립트 변환
            script_heygen = self._convert_to_heygen_format(script_backend)
            
            # 백엔드용 스크립트에 heygen_script 필드 추가
            script_backend["heygenScript"] = script_heygen
            
            # S3에 저장 (백엔드용 + Heygen용 포함)
            script_s3_key = f"scripts/{job.source_set_id}/script.json"
            script_json = json.dumps(script_backend, ensure_ascii=False, indent=2)
            await self._storage_provider.put_object(
                data=script_json.encode("utf-8"),
                key=script_s3_key,
                content_type="application/json",
            )
            
            job.script_backend = script_backend
            job.script_heygen = script_heygen
            job.script_s3_key = script_s3_key
            job.script_status = ScriptStatus.COMPLETED
            job.source_set_status = SourceSetStatus.SCRIPT_READY
            job.progress = 100
            await self._job_store.save(job)
            
            logger.info(
                f"Script generation completed: source_set_id={job.source_set_id}, "
                f"chapters={len(script_backend.get('chapters', []))}, "
                f"s3_key={script_s3_key}"
            )
            
            # 백엔드 콜백
            await self._notify_backend_script_completed(job)
            
        except Exception as e:
            logger.error(f"Script generation failed: source_set_id={job.source_set_id}, error={e}", exc_info=True)
            job.script_status = ScriptStatus.FAILED
            job.source_set_status = SourceSetStatus.FAILED
            job.fail_reason = f"Script generation failed: {str(e)}"
            await self._job_store.save(job)
            await self._notify_backend_script_failed(job)
    
    def _convert_to_heygen_format(self, script_backend: Dict[str, Any]) -> Dict[str, Any]:
        """백엔드용 스크립트를 Heygen 형식으로 변환."""
        from app.adapters.heygen_script_adapter import convert_script_to_heygen_format
        from app.core.config import get_settings
        
        settings = get_settings()
        
        # Heygen 어댑터 사용
        heygen_script = convert_script_to_heygen_format(
            script_data=script_backend,
            voice_id=getattr(settings, "HEYGEN_VOICE_ID", None),
            avatar_id=getattr(settings, "HEYGEN_AVATAR_ID", None),
            width=getattr(settings, "HEYGEN_DIM_W", 1280),
            height=getattr(settings, "HEYGEN_DIM_H", 720),
            background_color=getattr(settings, "HEYGEN_BG_VALUE", "#FFFFFF"),
        )
        
        return heygen_script
    
    async def start_video_generation(
        self,
        video_id: str,
        source_set_id: str,
        education_id: str,
        script_id: Optional[str] = None,
    ) -> PipelineJob:
        """
        영상 생성 파이프라인 시작.
        
        Args:
            video_id: 영상 ID
            source_set_id: 소스셋 ID
            education_id: 교육 ID
            script_id: 스크립트 ID (선택)
        
        Returns:
            PipelineJob: Job 상태
        """
        job = await self._job_store.get(source_set_id)
        if not job:
            raise ValueError(f"Job not found: source_set_id={source_set_id}")
        
        if job.source_set_status != SourceSetStatus.SCRIPT_READY:
            raise ValueError(f"Script not ready: status={job.source_set_status.value}")
        
        if not job.script_heygen:
            raise ValueError("Heygen script not available")
        
        # Idempotency: 이미 영상 생성 중이면 반환
        if job.video_job_status == VideoJobStatus.PROCESSING:
            logger.info(f"Video generation already in progress: video_id={video_id}")
            return job
        
        job.video_job_status = VideoJobStatus.PROCESSING
        await self._job_store.save(job)
        
        # Heygen 영상 생성 시작 (비동기)
        asyncio.create_task(self._generate_video_with_heygen(job))
        
        logger.info(f"Video generation started: video_id={video_id}, source_set_id={source_set_id}")
        
        return job
    
    async def _generate_video_with_heygen(self, job: PipelineJob) -> None:
        """Heygen으로 영상 생성 → S3 업로드."""
        if not self._heygen_client:
            job.video_job_status = VideoJobStatus.FAILED
            job.fail_reason = "Heygen client not configured"
            await self._job_store.save(job)
            return
        
        try:
            # Heygen payload 구성
            video_inputs = build_heygen_video_inputs(
                job.script_heygen,
                avatar_id=getattr(get_settings(), "HEYGEN_AVATAR_ID", ""),
                voice_id=getattr(get_settings(), "HEYGEN_VOICE_ID", ""),
            )
            payload = build_heygen_generate_payload(video_inputs)
            
            # Heygen job 생성
            heygen_video_id = await self._heygen_client.generate_video(payload)
            job.heygen_video_id = heygen_video_id
            await self._job_store.save(job)
            
            # 폴링
            status_data = await self._heygen_client.poll_video_status(heygen_video_id)
            
            # 비디오 URL 추출
            video_url = None
            if "data" in status_data:
                video_url = status_data["data"].get("video_url") or status_data["data"].get("url")
            
            if not video_url:
                raise ValueError("Video URL not found in Heygen response")
            
            # 다운로드
            temp_path = Path(f"/tmp/heygen_{heygen_video_id}.mp4")
            await self._heygen_client.download_video(video_url, temp_path)
            
            # S3 업로드 (서버가 직접)
            s3_key = f"videos/{job.video_id}/{job.video_id}.mp4"
            result = await self._storage_provider.put_object(
                data=temp_path,
                key=s3_key,
                content_type="video/mp4",
            )
            
            # 임시 파일 삭제
            temp_path.unlink(missing_ok=True)
            
            job.video_s3_key = s3_key
            job.video_url = result.url if hasattr(result, "url") else None
            job.video_job_status = VideoJobStatus.COMPLETED
            job.progress = 100
            await self._job_store.save(job)
            
            # 백엔드 콜백
            await self._notify_backend_video_completed(job)
            
        except HeyGenError as e:
            logger.error(f"Heygen video generation failed: source_set_id={job.source_set_id}, error={e}", exc_info=True)
            job.video_job_status = VideoJobStatus.FAILED
            job.fail_reason = f"Heygen error: {str(e)}"
            await self._job_store.save(job)
            await self._notify_backend_video_failed(job)
        except Exception as e:
            logger.error(f"Video generation failed: source_set_id={job.source_set_id}, error={e}", exc_info=True)
            job.video_job_status = VideoJobStatus.FAILED
            job.fail_reason = f"Video generation error: {str(e)}"
            await self._job_store.save(job)
            await self._notify_backend_video_failed(job)
    
    async def get_job_status(
        self,
        source_set_id: Optional[str] = None,
        video_id: Optional[str] = None,
    ) -> Optional[PipelineJob]:
        """Job 상태 조회."""
        if source_set_id:
            return await self._job_store.get(source_set_id)
        elif video_id:
            jobs = await self._job_store.list_by_video_id(video_id)
            return jobs[0] if jobs else None
        return None
    
    # =========================================================================
    # 백엔드 콜백
    # =========================================================================
    
    async def _notify_backend_preprocess_failed(self, job: PipelineJob) -> None:
        """전처리 실패 백엔드 통지."""
        try:
            from app.models.source_set import SourceSetCompleteRequest
            
            request = SourceSetCompleteRequest(
                videoId=job.video_id,
                status="FAILED",
                sourceSetStatus="FAILED",
                documents=[],
                errorCode="PREPROCESSING_FAILED",
                errorMessage=job.fail_reason or "RAGFLOW preprocessing failed",
            )
            await self._backend_client.notify_source_set_complete(job.source_set_id, request)
        except Exception as e:
            logger.error(f"Backend preprocess failure callback failed: {e}", exc_info=True)
    
    async def _notify_backend_script_completed(self, job: PipelineJob) -> None:
        """스크립트 생성 완료 백엔드 통지."""
        try:
            # 기존 notify_source_set_complete 재사용
            from app.models.source_set import SourceSetCompleteRequest, GeneratedScript
            
            # script_backend가 dict이므로 GeneratedScript 모델로 변환
            script_model = GeneratedScript(**job.script_backend) if isinstance(job.script_backend, dict) else job.script_backend
            
            request = SourceSetCompleteRequest(
                videoId=job.video_id,
                status="COMPLETED",
                sourceSetStatus="SCRIPT_READY",
                documents=[],
                script=script_model,
            )
            await self._backend_client.notify_source_set_complete(job.source_set_id, request)
        except Exception as e:
            logger.error(f"Backend script completion callback failed: {e}", exc_info=True)
    
    async def _notify_backend_script_failed(self, job: PipelineJob) -> None:
        """스크립트 생성 실패 백엔드 통지."""
        try:
            from app.models.source_set import SourceSetCompleteRequest
            
            request = SourceSetCompleteRequest(
                videoId=job.video_id,
                status="FAILED",
                sourceSetStatus="FAILED",
                documents=[],
                errorCode="SCRIPT_GENERATION_FAILED",
                errorMessage=job.fail_reason,
            )
            await self._backend_client.notify_source_set_complete(job.source_set_id, request)
        except Exception as e:
            logger.error(f"Backend script failure callback failed: {e}", exc_info=True)
    
    async def _notify_backend_video_completed(self, job: PipelineJob) -> None:
        """영상 생성 완료 백엔드 통지."""
        try:
            # 기존 notify_job_complete 재사용
            await self._backend_client.notify_job_complete(
                job_id=job.video_id,  # TODO: 실제 job_id 사용
                video_url=job.video_url or job.video_s3_key,
                duration=0,  # TODO: 실제 duration 계산
                status="COMPLETED",
            )
        except Exception as e:
            logger.error(f"Backend video completion callback failed: {e}", exc_info=True)
    
    async def _notify_backend_video_failed(self, job: PipelineJob) -> None:
        """영상 생성 실패 백엔드 통지."""
        try:
            await self._backend_client.notify_job_complete(
                job_id=job.video_id,
                video_url=None,
                duration=0,
                status="FAILED",
            )
        except Exception as e:
            logger.error(f"Backend video failure callback failed: {e}", exc_info=True)


# 싱글톤 인스턴스
_pipeline_service: Optional[EducationPipelineService] = None


def get_edu_pipeline_service() -> EducationPipelineService:
    """교육 영상 파이프라인 서비스 싱글톤 인스턴스 반환."""
    global _pipeline_service
    if _pipeline_service is None:
        _pipeline_service = EducationPipelineService()
    return _pipeline_service

