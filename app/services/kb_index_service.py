"""
Phase 28: KB Index Service

승인된 교육 영상 스크립트를 KB(Knowledge Base)에 적재하는 서비스.

핵심 정책:
- RAG 적재는 PUBLISHED 상태의 영상만 대상
- APPROVED 스크립트 + 렌더 SUCCEEDED + 검토자 승인 후에만 적재
- 최신 버전 1개만 ACTIVE, 이전 버전은 ARCHIVED/삭제
- EXPIRED 교육은 적재/검색 제외

주요 기능:
- index_published_video(): 발행된 영상의 스크립트를 KB에 적재
- build_chunks_from_script(): 스크립트 JSON → 청크 리스트 변환
- archive_previous_version(): 이전 버전 아카이브 처리
"""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.models.video_render import (
    KBChunk,
    KBDocumentStatus,
    KBIndexStatus,
    ScriptStatus,
    VideoScript,
)

logger = get_logger(__name__)


class KBIndexService:
    """KB 인덱스 서비스.

    승인된 스크립트를 벡터 DB에 적재합니다.

    Usage:
        service = KBIndexService(milvus_client)

        # 발행된 영상 인덱싱
        status = await service.index_published_video(
            video_id="video-001",
            script=approved_script,
            course_type="FOUR_MANDATORY",
            year=2025
        )
    """

    def __init__(self, milvus_client=None):
        """서비스 초기화.

        Args:
            milvus_client: Milvus 클라이언트 (None이면 Mock 모드)
        """
        self._milvus = milvus_client
        self._mock_mode = milvus_client is None

        if self._mock_mode:
            logger.info("KBIndexService initialized in mock mode (no Milvus client)")
        else:
            logger.info("KBIndexService initialized with Milvus client")

    # =========================================================================
    # Main Entry Point
    # =========================================================================

    async def index_published_video(
        self,
        video_id: str,
        script: VideoScript,
        course_type: str = "TRAINING",
        year: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        training_id: Optional[str] = None,
    ) -> KBIndexStatus:
        """발행된 영상의 스크립트를 KB에 적재합니다.

        Args:
            video_id: 비디오 ID
            script: 승인된 스크립트
            course_type: 교육 유형 (FOUR_MANDATORY, JOB, TRAINING)
            year: 교육 연도
            expires_at: 만료 시각
            training_id: 교육 ID

        Returns:
            KBIndexStatus: 인덱싱 결과 상태

        Raises:
            ValueError: 스크립트가 승인되지 않은 경우
        """
        logger.info(
            f"index_published_video: video_id={video_id}, script_id={script.script_id}, "
            f"course_type={course_type}"
        )

        # 1. 스크립트 상태 검증
        if not script.is_approved():
            raise ValueError(
                f"Script is not approved: {script.script_id} (status={script.status.value})"
            )

        try:
            # 2. 스크립트 → 청크 변환
            chunks = self.build_chunks_from_script(
                script=script,
                course_type=course_type,
                year=year,
                expires_at=expires_at,
                training_id=training_id,
            )

            if not chunks:
                logger.warning(f"No chunks generated from script: {script.script_id}")
                return KBIndexStatus.FAILED

            logger.info(f"Generated {len(chunks)} chunks from script {script.script_id}")

            # 3. 이전 버전 아카이브/삭제
            await self.archive_previous_version(video_id, script.script_id)

            # 4. 벡터 DB에 upsert
            await self.upsert_chunks(
                doc_id=script.script_id,
                chunks=chunks,
                metadata={
                    "video_id": video_id,
                    "script_id": script.script_id,
                    "course_type": course_type,
                    "year": year,
                    "training_id": training_id,
                    "status": KBDocumentStatus.ACTIVE.value,
                    "domain": "TRAINING",
                },
            )

            logger.info(
                f"KB indexing succeeded: video_id={video_id}, "
                f"script_id={script.script_id}, chunks={len(chunks)}"
            )
            return KBIndexStatus.SUCCEEDED

        except Exception as e:
            logger.exception(f"KB indexing failed: video_id={video_id}, error={e}")
            return KBIndexStatus.FAILED

    # =========================================================================
    # Chunk Building
    # =========================================================================

    def build_chunks_from_script(
        self,
        script: VideoScript,
        course_type: str = "TRAINING",
        year: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        training_id: Optional[str] = None,
    ) -> List[KBChunk]:
        """스크립트 JSON을 KB 청크 리스트로 변환합니다.

        MVP 청킹 규칙: 씬 단위로 1개 청크 생성

        스크립트 JSON 예상 구조:
        {
            "chapters": [
                {
                    "chapter_id": 1,
                    "title": "개요",
                    "scenes": [
                        {
                            "scene_id": 1,
                            "purpose": "인사",
                            "narration": "안녕하세요.",
                            "caption": "환영합니다",
                            "source_refs": {"doc_id": "...", "chunk_id": "..."}
                        }
                    ]
                }
            ]
        }

        또는 간단한 형태:
        {
            "scenes": [
                {"scene_id": 1, "text": "내용..."}
            ]
        }

        Args:
            script: 승인된 스크립트
            course_type: 교육 유형
            year: 교육 연도
            expires_at: 만료 시각
            training_id: 교육 ID

        Returns:
            List[KBChunk]: 청크 리스트
        """
        raw_json = script.raw_json
        chunks: List[KBChunk] = []

        # 챕터/씬 구조 처리
        chapters = raw_json.get("chapters", [])

        if chapters:
            # 챕터가 있는 경우
            for chapter in chapters:
                chapter_order = chapter.get("chapter_id", chapter.get("order", 0))
                chapter_title = chapter.get("title", f"Chapter {chapter_order}")

                scenes = chapter.get("scenes", [])
                for scene in scenes:
                    chunk = self._build_chunk_from_scene(
                        script=script,
                        chapter_order=chapter_order,
                        chapter_title=chapter_title,
                        scene=scene,
                        course_type=course_type,
                        year=year,
                        training_id=training_id,
                    )
                    if chunk:
                        chunks.append(chunk)
        else:
            # 간단한 scenes만 있는 경우
            scenes = raw_json.get("scenes", [])
            for scene in scenes:
                chunk = self._build_chunk_from_scene(
                    script=script,
                    chapter_order=1,
                    chapter_title="Main",
                    scene=scene,
                    course_type=course_type,
                    year=year,
                    training_id=training_id,
                )
                if chunk:
                    chunks.append(chunk)

        return chunks

    def _build_chunk_from_scene(
        self,
        script: VideoScript,
        chapter_order: int,
        chapter_title: str,
        scene: Dict[str, Any],
        course_type: str,
        year: Optional[int],
        training_id: Optional[str],
    ) -> Optional[KBChunk]:
        """씬에서 청크를 생성합니다."""
        scene_order = scene.get("scene_id", scene.get("order", 0))
        scene_purpose = scene.get("purpose", "")

        # 내용 구성: narration + caption 또는 text
        narration = scene.get("narration", "")
        caption = scene.get("caption", "")
        text = scene.get("text", "")

        # 중복 제거하여 content 생성
        content_parts = []
        if narration:
            content_parts.append(narration)
        if caption and caption != narration:
            content_parts.append(caption)
        if text and text not in content_parts:
            content_parts.append(text)

        content = "\n".join(content_parts).strip()

        if not content:
            return None

        # chunk_id 생성: script_id:chapter:scene
        chunk_id = f"{script.script_id}:{chapter_order}:{scene_order}"

        # source_refs 처리
        source_refs = scene.get("source_refs")

        return KBChunk(
            chunk_id=chunk_id,
            video_id=script.video_id,
            script_id=script.script_id,
            chapter_order=chapter_order,
            scene_order=scene_order,
            chapter_title=chapter_title,
            scene_purpose=scene_purpose,
            content=content,
            source_refs=source_refs,
            metadata={
                "course_type": course_type,
                "year": year,
                "training_id": training_id,
                "domain": "TRAINING",
            },
        )

    # =========================================================================
    # Vector DB Operations
    # =========================================================================

    async def upsert_chunks(
        self,
        doc_id: str,
        chunks: List[KBChunk],
        metadata: Dict[str, Any],
    ) -> int:
        """청크들을 벡터 DB에 upsert합니다.

        Args:
            doc_id: 문서 ID (script_id)
            chunks: 청크 리스트
            metadata: 문서 메타데이터

        Returns:
            int: upsert된 청크 수
        """
        if self._mock_mode:
            logger.info(f"[MOCK] upsert_chunks: doc_id={doc_id}, count={len(chunks)}")
            return len(chunks)

        try:
            # Milvus 클라이언트를 통해 upsert
            # 각 청크를 Milvus 스키마에 맞게 변환
            milvus_chunks = []
            for chunk in chunks:
                milvus_chunk = {
                    "document_id": doc_id,
                    "version_no": 1,  # 버전 관리 시 증가
                    "domain": metadata.get("domain", "TRAINING"),
                    "title": chunk.chapter_title,
                    "chunk_id": hash(chunk.chunk_id) % (10 ** 9),  # int로 변환
                    "chunk_text": chunk.content,
                    "page": chunk.scene_order,
                    "section_path": f"{chunk.chapter_title}/{chunk.scene_purpose}",
                }
                milvus_chunks.append(milvus_chunk)

            # 임베딩 생성 및 upsert
            for milvus_chunk in milvus_chunks:
                embedding = await self._milvus.generate_embedding(milvus_chunk["chunk_text"])
                milvus_chunk["embedding"] = embedding

            result = await self._milvus.upsert_chunks(milvus_chunks)
            logger.info(f"Upserted {result} chunks to Milvus for doc_id={doc_id}")
            return result

        except Exception as e:
            logger.exception(f"Failed to upsert chunks: doc_id={doc_id}, error={e}")
            raise

    async def delete_doc(self, doc_id: str) -> bool:
        """문서를 벡터 DB에서 삭제합니다.

        Args:
            doc_id: 문서 ID (script_id)

        Returns:
            bool: 삭제 성공 여부
        """
        if self._mock_mode:
            logger.info(f"[MOCK] delete_doc: doc_id={doc_id}")
            return True

        try:
            await self._milvus.delete_by_document(doc_id)
            logger.info(f"Deleted document from Milvus: doc_id={doc_id}")
            return True

        except Exception as e:
            logger.exception(f"Failed to delete doc: doc_id={doc_id}, error={e}")
            return False

    async def archive_previous_version(
        self,
        video_id: str,
        current_script_id: str,
    ) -> int:
        """이전 버전의 문서를 아카이브/삭제합니다.

        같은 video_id에 대해 새 승인본이 발행되면
        이전 ACTIVE 스크립트를 ARCHIVED로 처리합니다.

        MVP: 벡터 DB에서 삭제 (검색 제외 확실)

        Args:
            video_id: 비디오 ID
            current_script_id: 현재(새) 스크립트 ID

        Returns:
            int: 아카이브/삭제된 문서 수
        """
        if self._mock_mode:
            logger.info(
                f"[MOCK] archive_previous_version: video_id={video_id}, "
                f"current={current_script_id}"
            )
            return 0

        # 실제 구현: video_id로 이전 스크립트 조회 후 삭제
        # 현재는 비디오 렌더 서비스와 연동 필요
        logger.info(
            f"archive_previous_version: video_id={video_id}, "
            f"current_script_id={current_script_id}"
        )
        return 0


# =============================================================================
# Singleton Instance
# =============================================================================

_kb_index_service: Optional[KBIndexService] = None


def get_kb_index_service(milvus_client=None) -> KBIndexService:
    """KBIndexService 싱글톤 인스턴스 반환."""
    global _kb_index_service
    if _kb_index_service is None:
        _kb_index_service = KBIndexService(milvus_client)
    return _kb_index_service


def clear_kb_index_service() -> None:
    """KBIndexService 싱글톤 인스턴스 제거 (테스트용)."""
    global _kb_index_service
    _kb_index_service = None
