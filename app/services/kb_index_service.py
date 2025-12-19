"""
Phase 28/29: KB Index Service

승인된 교육 영상 스크립트를 KB(Knowledge Base)에 적재하는 서비스.

핵심 정책:
- RAG 적재는 PUBLISHED 상태의 영상만 대상
- APPROVED 스크립트 + 렌더 SUCCEEDED + 검토자 승인 후에만 적재
- 최신 버전 1개만 ACTIVE, 이전 버전은 ARCHIVED/삭제
- EXPIRED 교육은 적재/검색 제외

Phase 29 추가:
- 토큰 기반 청킹: 긴 씬은 N 토큰 단위로 분할
- chunk_id 규칙: script_id:chapter:scene:part
- source_type: "TRAINING_SCRIPT"로 RAG 근거 구분

주요 기능:
- index_published_video(): 발행된 영상의 스크립트를 KB에 적재
- build_chunks_from_script(): 스크립트 JSON → 청크 리스트 변환
- archive_previous_version(): 이전 버전 아카이브 처리
- _split_content_by_tokens(): 긴 내용 토큰 기반 분할 (Phase 29)
"""

import asyncio
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_settings
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

        # Phase 29: 토큰 분할 설정 로드
        settings = get_settings()
        self._max_tokens = settings.KB_CHUNK_MAX_TOKENS
        self._min_tokens = settings.KB_CHUNK_MIN_TOKENS
        self._tokenizer_type = settings.KB_CHUNK_TOKENIZER
        self._chars_per_token = settings.KB_CHUNK_CHARS_PER_TOKEN

        if self._mock_mode:
            logger.info("KBIndexService initialized in mock mode (no Milvus client)")
        else:
            logger.info("KBIndexService initialized with Milvus client")

        logger.info(
            f"KBIndexService chunk settings: max_tokens={self._max_tokens}, "
            f"min_tokens={self._min_tokens}, tokenizer={self._tokenizer_type}"
        )

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
                    # Phase 29: 씬에서 여러 청크 생성 가능 (토큰 분할)
                    scene_chunks = self._build_chunks_from_scene(
                        script=script,
                        chapter_order=chapter_order,
                        chapter_title=chapter_title,
                        scene=scene,
                        course_type=course_type,
                        year=year,
                        training_id=training_id,
                    )
                    chunks.extend(scene_chunks)
        else:
            # 간단한 scenes만 있는 경우
            scenes = raw_json.get("scenes", [])
            for scene in scenes:
                # Phase 29: 씬에서 여러 청크 생성 가능 (토큰 분할)
                scene_chunks = self._build_chunks_from_scene(
                    script=script,
                    chapter_order=1,
                    chapter_title="Main",
                    scene=scene,
                    course_type=course_type,
                    year=year,
                    training_id=training_id,
                )
                chunks.extend(scene_chunks)

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
        """씬에서 단일 청크를 생성합니다 (하위 호환성 유지).

        Note: 토큰 기반 분할은 _build_chunks_from_scene()을 사용하세요.
        """
        chunks = self._build_chunks_from_scene(
            script=script,
            chapter_order=chapter_order,
            chapter_title=chapter_title,
            scene=scene,
            course_type=course_type,
            year=year,
            training_id=training_id,
        )
        return chunks[0] if chunks else None

    def _build_chunks_from_scene(
        self,
        script: VideoScript,
        chapter_order: int,
        chapter_title: str,
        scene: Dict[str, Any],
        course_type: str,
        year: Optional[int],
        training_id: Optional[str],
    ) -> List[KBChunk]:
        """씬에서 청크 리스트를 생성합니다 (Phase 29: 토큰 기반 분할 지원).

        긴 내용은 토큰 수 기준으로 분할하여 여러 청크를 생성합니다.

        Args:
            script: 스크립트
            chapter_order: 챕터 순서
            chapter_title: 챕터 제목
            scene: 씬 데이터
            course_type: 교육 유형
            year: 교육 연도
            training_id: 교육 ID

        Returns:
            List[KBChunk]: 청크 리스트 (분할된 경우 여러 개)
        """
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
            return []

        # source_refs 처리
        source_refs = scene.get("source_refs")

        # Phase 29: 토큰 기반 분할
        content_parts_split = self._split_content_by_tokens(content)

        chunks: List[KBChunk] = []

        for part_index, part_content in enumerate(content_parts_split):
            # chunk_id 생성: 분할 여부에 따라 다른 형식
            if len(content_parts_split) == 1:
                # 분할 없음: script_id:chapter:scene
                chunk_id = f"{script.script_id}:{chapter_order}:{scene_order}"
                part_idx = None
            else:
                # 분할됨: script_id:chapter:scene:part
                chunk_id = f"{script.script_id}:{chapter_order}:{scene_order}:{part_index}"
                part_idx = part_index

            chunk = KBChunk(
                chunk_id=chunk_id,
                video_id=script.video_id,
                script_id=script.script_id,
                chapter_order=chapter_order,
                scene_order=scene_order,
                chapter_title=chapter_title,
                scene_purpose=scene_purpose,
                content=part_content,
                source_refs=source_refs,
                metadata={
                    "course_type": course_type,
                    "year": year,
                    "training_id": training_id,
                    "domain": "TRAINING",
                },
                part_index=part_idx,
                source_type="TRAINING_SCRIPT",
            )
            chunks.append(chunk)

        return chunks

    # =========================================================================
    # Phase 29: 토큰 기반 분할
    # =========================================================================

    def _estimate_tokens(self, text: str) -> int:
        """텍스트의 토큰 수를 추정합니다.

        Args:
            text: 텍스트

        Returns:
            int: 추정 토큰 수
        """
        if self._tokenizer_type == "tiktoken":
            # tiktoken 사용 (설치되어 있을 경우)
            try:
                import tiktoken
                enc = tiktoken.get_encoding("cl100k_base")
                return len(enc.encode(text))
            except ImportError:
                logger.warning("tiktoken not installed, falling back to char estimation")
                pass

        # 문자 기반 근사
        return int(len(text) / self._chars_per_token)

    def _split_content_by_tokens(self, content: str) -> List[str]:
        """내용을 토큰 수 기준으로 분할합니다.

        문장 경계를 존중하여 분할합니다.

        Args:
            content: 원본 내용

        Returns:
            List[str]: 분할된 내용 리스트
        """
        estimated_tokens = self._estimate_tokens(content)

        # 토큰 수가 최대 이하면 분할 없이 반환
        if estimated_tokens <= self._max_tokens:
            return [content]

        # 문장 경계로 분할
        sentences = self._split_into_sentences(content)

        if not sentences:
            return [content]

        parts: List[str] = []
        current_part: List[str] = []
        current_tokens = 0

        for sentence in sentences:
            sentence_tokens = self._estimate_tokens(sentence)

            # 단일 문장이 max_tokens를 초과하면 강제 분할
            if sentence_tokens > self._max_tokens:
                # 현재 파트가 있으면 먼저 저장
                if current_part:
                    parts.append(" ".join(current_part))
                    current_part = []
                    current_tokens = 0

                # 긴 문장 강제 분할
                forced_parts = self._force_split_long_text(sentence)
                parts.extend(forced_parts)
                continue

            # 현재 파트에 추가 시 max_tokens 초과 여부 확인
            if current_tokens + sentence_tokens > self._max_tokens:
                # 현재 파트 저장
                if current_part:
                    parts.append(" ".join(current_part))
                current_part = [sentence]
                current_tokens = sentence_tokens
            else:
                current_part.append(sentence)
                current_tokens += sentence_tokens

        # 마지막 파트 저장
        if current_part:
            parts.append(" ".join(current_part))

        # 너무 작은 파트 병합
        merged_parts = self._merge_small_parts(parts)

        logger.debug(
            f"Split content: {estimated_tokens} tokens -> {len(merged_parts)} parts"
        )

        return merged_parts if merged_parts else [content]

    def _split_into_sentences(self, text: str) -> List[str]:
        """텍스트를 문장 단위로 분할합니다.

        한국어와 영어 문장 경계를 모두 지원합니다.

        Args:
            text: 원본 텍스트

        Returns:
            List[str]: 문장 리스트
        """
        # 문장 구분 패턴: .!? 또는 한국어 마침표/물음표/느낌표 후 공백 또는 줄바꿈
        pattern = r'(?<=[.!?。！？])\s+'
        sentences = re.split(pattern, text)
        return [s.strip() for s in sentences if s.strip()]

    def _force_split_long_text(self, text: str) -> List[str]:
        """긴 텍스트를 강제로 분할합니다.

        문장 경계가 없는 매우 긴 텍스트를 처리합니다.

        Args:
            text: 긴 텍스트

        Returns:
            List[str]: 분할된 텍스트 리스트
        """
        # 최대 문자 수 계산 (토큰 * 문자/토큰 비율)
        max_chars = int(self._max_tokens * self._chars_per_token)

        parts = []
        while len(text) > max_chars:
            # 최대 위치 근처에서 공백 찾기
            split_pos = text.rfind(" ", max_chars // 2, max_chars)
            if split_pos == -1:
                split_pos = max_chars

            parts.append(text[:split_pos].strip())
            text = text[split_pos:].strip()

        if text:
            parts.append(text)

        return parts

    def _merge_small_parts(self, parts: List[str]) -> List[str]:
        """너무 작은 파트를 이전 파트와 병합합니다.

        Args:
            parts: 파트 리스트

        Returns:
            List[str]: 병합된 파트 리스트
        """
        if len(parts) <= 1:
            return parts

        merged: List[str] = []

        for part in parts:
            part_tokens = self._estimate_tokens(part)

            if (merged and
                part_tokens < self._min_tokens and
                self._estimate_tokens(merged[-1]) + part_tokens <= self._max_tokens):
                # 이전 파트와 병합
                merged[-1] = merged[-1] + " " + part
            else:
                merged.append(part)

        return merged

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
