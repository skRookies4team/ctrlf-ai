"""
Document Processor Service (Phase 25)

파일 다운로드, 텍스트 추출, 청킹을 담당하는 서비스입니다.
RAGFlow를 우회하여 AI 서버가 직접 문서를 처리합니다.

지원 파일 형식:
- PDF (.pdf)
- 텍스트 (.txt)
- Word (.docx, .doc)
- 한글 (.hwp) - 추후 지원

처리 플로우:
1. URL에서 파일 다운로드
2. 파일 형식에 따라 텍스트 추출
3. 텍스트를 청크로 분할
4. DocumentChunk 리스트 반환
"""

import io
import os
import re
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.internal_rag import DocumentChunk

logger = get_logger(__name__)


# =============================================================================
# 예외 클래스
# =============================================================================


class DocumentProcessingError(Exception):
    """문서 처리 중 발생하는 예외."""

    def __init__(self, message: str, stage: str, original_error: Optional[Exception] = None):
        super().__init__(message)
        self.message = message
        self.stage = stage  # downloading, extracting, chunking
        self.original_error = original_error


class FileDownloadError(DocumentProcessingError):
    """파일 다운로드 실패."""

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        super().__init__(message, "downloading", original_error)


class TextExtractionError(DocumentProcessingError):
    """텍스트 추출 실패."""

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        super().__init__(message, "extracting", original_error)


class ChunkingError(DocumentProcessingError):
    """청킹 실패."""

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        super().__init__(message, "chunking", original_error)


# =============================================================================
# Document Processor
# =============================================================================


class DocumentProcessor:
    """
    문서 처리 서비스.

    파일 다운로드 → 텍스트 추출 → 청킹 파이프라인을 수행합니다.

    Example:
        processor = DocumentProcessor()
        chunks = await processor.process(
            file_url="https://example.com/doc.pdf",
            document_id="DOC-001",
            version_no=1,
            domain="POLICY",
            title="인사규정"
        )
    """

    def __init__(
        self,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
    ) -> None:
        """
        DocumentProcessor 초기화.

        Args:
            chunk_size: 청크 크기. None이면 settings에서 로드.
            chunk_overlap: 청크 오버랩. None이면 settings에서 로드.
        """
        settings = get_settings()
        self._chunk_size = chunk_size or settings.CHUNK_SIZE
        self._chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP
        self._download_timeout = settings.FILE_DOWNLOAD_TIMEOUT_SEC
        self._max_file_size_bytes = settings.FILE_MAX_SIZE_MB * 1024 * 1024
        self._supported_extensions = set(
            ext.strip().lower()
            for ext in settings.SUPPORTED_FILE_EXTENSIONS.split(",")
        )

        logger.info(
            f"DocumentProcessor initialized: chunk_size={self._chunk_size}, "
            f"overlap={self._chunk_overlap}, supported={self._supported_extensions}"
        )

    async def process(
        self,
        file_url: str,
        document_id: str,
        version_no: int,
        domain: str,
        title: Optional[str] = None,
    ) -> List[DocumentChunk]:
        """
        문서를 처리하여 청크 리스트를 반환합니다.

        Args:
            file_url: 파일 URL
            document_id: 문서 ID
            version_no: 버전 번호
            domain: 도메인
            title: 문서 제목 (없으면 파일명 사용)

        Returns:
            List[DocumentChunk]: 청크 리스트

        Raises:
            DocumentProcessingError: 처리 실패 시
        """
        logger.info(
            f"Processing document: document_id={document_id}, "
            f"version_no={version_no}, url={file_url[:50]}..."
        )

        # 1. 파일 다운로드
        file_content, file_ext, inferred_title = await self._download_file(file_url)
        doc_title = title or inferred_title or f"Document {document_id}"

        # 2. 텍스트 추출
        text, page_texts = self._extract_text(file_content, file_ext)

        # 3. 청킹
        chunks = self._create_chunks(
            text=text,
            page_texts=page_texts,
            document_id=document_id,
            version_no=version_no,
            domain=domain,
            title=doc_title,
        )

        logger.info(
            f"Document processed: document_id={document_id}, "
            f"chunks={len(chunks)}, total_chars={len(text)}"
        )

        return chunks

    async def _download_file(self, url: str) -> Tuple[bytes, str, Optional[str]]:
        """
        URL에서 파일을 다운로드합니다.

        Args:
            url: 파일 URL

        Returns:
            Tuple[bytes, str, Optional[str]]: (파일 내용, 확장자, 추론된 제목)

        Raises:
            FileDownloadError: 다운로드 실패 시
        """
        try:
            # URL에서 파일명/확장자 추론
            parsed_filename = self._parse_filename_from_url(url)
            file_ext = Path(parsed_filename).suffix.lower() if parsed_filename else ""

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    timeout=self._download_timeout,
                    follow_redirects=True,
                )

                if response.status_code != 200:
                    raise FileDownloadError(
                        f"HTTP {response.status_code}: {response.text[:200]}"
                    )

                content = response.content

                # 파일 크기 검증
                if len(content) > self._max_file_size_bytes:
                    raise FileDownloadError(
                        f"File too large: {len(content)} bytes > {self._max_file_size_bytes} bytes"
                    )

                # Content-Type에서 확장자 보정
                content_type = response.headers.get("content-type", "")
                if not file_ext:
                    file_ext = self._guess_extension_from_content_type(content_type)

                # 확장자 검증
                if file_ext and file_ext not in self._supported_extensions:
                    raise FileDownloadError(
                        f"Unsupported file type: {file_ext}. "
                        f"Supported: {self._supported_extensions}"
                    )

                # 파일명에서 제목 추론
                inferred_title = None
                if parsed_filename:
                    inferred_title = Path(parsed_filename).stem

                logger.debug(
                    f"File downloaded: size={len(content)}, ext={file_ext}, "
                    f"title={inferred_title}"
                )

                return content, file_ext, inferred_title

        except httpx.TimeoutException as e:
            raise FileDownloadError(f"Download timeout: {url}", original_error=e)

        except httpx.RequestError as e:
            raise FileDownloadError(f"Download error: {e}", original_error=e)

        except FileDownloadError:
            raise

        except Exception as e:
            raise FileDownloadError(f"Unexpected error: {e}", original_error=e)

    def _parse_filename_from_url(self, url: str) -> Optional[str]:
        """URL에서 파일명을 추론합니다."""
        try:
            from urllib.parse import unquote, urlparse

            parsed = urlparse(url)
            path = unquote(parsed.path)
            if path:
                return Path(path).name
            return None
        except Exception:
            return None

    def _guess_extension_from_content_type(self, content_type: str) -> str:
        """Content-Type에서 확장자를 추론합니다."""
        content_type = content_type.lower()
        mapping = {
            "application/pdf": ".pdf",
            "text/plain": ".txt",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/msword": ".doc",
            "application/x-hwp": ".hwp",
        }
        for ct, ext in mapping.items():
            if ct in content_type:
                return ext
        return ""

    def _extract_text(
        self, content: bytes, file_ext: str
    ) -> Tuple[str, Optional[List[Tuple[int, str]]]]:
        """
        파일에서 텍스트를 추출합니다.

        Args:
            content: 파일 내용
            file_ext: 파일 확장자

        Returns:
            Tuple[str, Optional[List[Tuple[int, str]]]]:
                (전체 텍스트, 페이지별 텍스트 리스트 [(page_no, text), ...])

        Raises:
            TextExtractionError: 추출 실패 시
        """
        try:
            if file_ext == ".pdf":
                return self._extract_from_pdf(content)
            elif file_ext == ".txt":
                return self._extract_from_txt(content)
            elif file_ext in (".docx", ".doc"):
                return self._extract_from_docx(content)
            elif file_ext == ".hwp":
                return self._extract_from_hwp(content)
            else:
                # 확장자 없으면 텍스트로 시도
                return self._extract_from_txt(content)

        except TextExtractionError:
            raise
        except Exception as e:
            raise TextExtractionError(f"Failed to extract text: {e}", original_error=e)

    def _extract_from_pdf(
        self, content: bytes
    ) -> Tuple[str, Optional[List[Tuple[int, str]]]]:
        """PDF에서 텍스트 추출."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise TextExtractionError(
                "PyMuPDF not installed. Install with: pip install pymupdf"
            )

        try:
            doc = fitz.open(stream=content, filetype="pdf")
            page_texts: List[Tuple[int, str]] = []
            all_text_parts: List[str] = []

            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                cleaned = self._clean_text(text)
                if cleaned:
                    page_texts.append((page_num + 1, cleaned))
                    all_text_parts.append(cleaned)

            doc.close()

            full_text = "\n\n".join(all_text_parts)
            return full_text, page_texts

        except Exception as e:
            raise TextExtractionError(f"PDF extraction failed: {e}", original_error=e)

    def _extract_from_txt(
        self, content: bytes
    ) -> Tuple[str, Optional[List[Tuple[int, str]]]]:
        """텍스트 파일에서 텍스트 추출."""
        try:
            # UTF-8 시도, 실패 시 CP949 (한국어 Windows)
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("cp949", errors="replace")

            cleaned = self._clean_text(text)
            return cleaned, None

        except Exception as e:
            raise TextExtractionError(f"Text extraction failed: {e}", original_error=e)

    def _extract_from_docx(
        self, content: bytes
    ) -> Tuple[str, Optional[List[Tuple[int, str]]]]:
        """DOCX에서 텍스트 추출."""
        try:
            from docx import Document
        except ImportError:
            raise TextExtractionError(
                "python-docx not installed. Install with: pip install python-docx"
            )

        try:
            doc = Document(io.BytesIO(content))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            full_text = "\n\n".join(paragraphs)
            cleaned = self._clean_text(full_text)
            return cleaned, None

        except Exception as e:
            raise TextExtractionError(f"DOCX extraction failed: {e}", original_error=e)

    def _extract_from_hwp(
        self, content: bytes
    ) -> Tuple[str, Optional[List[Tuple[int, str]]]]:
        """HWP에서 텍스트 추출 (제한적 지원)."""
        # HWP 파싱은 복잡하므로 기본적인 시도만 수행
        # 실제 프로덕션에서는 hwp5txt 또는 LibreOffice 변환 사용 권장
        try:
            # olefile로 시도
            import olefile
        except ImportError:
            raise TextExtractionError(
                "olefile not installed for HWP support. Install with: pip install olefile"
            )

        try:
            ole = olefile.OleFileIO(io.BytesIO(content))

            # HWP 내부 스트림에서 텍스트 추출 시도
            if ole.exists("PrvText"):
                text_stream = ole.openstream("PrvText")
                text = text_stream.read().decode("utf-16", errors="replace")
                cleaned = self._clean_text(text)
                ole.close()
                return cleaned, None

            ole.close()
            raise TextExtractionError("HWP file does not contain extractable text")

        except TextExtractionError:
            raise
        except Exception as e:
            raise TextExtractionError(f"HWP extraction failed: {e}", original_error=e)

    def _clean_text(self, text: str) -> str:
        """텍스트 정제."""
        # 연속 공백/줄바꿈 정리
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" +\n", "\n", text)
        text = re.sub(r"\n +", "\n", text)
        return text.strip()

    def _create_chunks(
        self,
        text: str,
        page_texts: Optional[List[Tuple[int, str]]],
        document_id: str,
        version_no: int,
        domain: str,
        title: str,
    ) -> List[DocumentChunk]:
        """
        텍스트를 청크로 분할합니다.

        Args:
            text: 전체 텍스트
            page_texts: 페이지별 텍스트 (있으면 페이지 정보 활용)
            document_id: 문서 ID
            version_no: 버전 번호
            domain: 도메인
            title: 문서 제목

        Returns:
            List[DocumentChunk]: 청크 리스트
        """
        try:
            chunks: List[DocumentChunk] = []
            chunk_id = 0

            if page_texts:
                # 페이지별로 청킹 (페이지 정보 유지)
                for page_no, page_text in page_texts:
                    page_chunks = self._split_text_into_chunks(page_text)
                    for chunk_text in page_chunks:
                        chunks.append(
                            DocumentChunk(
                                document_id=document_id,
                                version_no=version_no,
                                domain=domain,
                                title=title,
                                chunk_id=chunk_id,
                                chunk_text=chunk_text,
                                page=page_no,
                                section_path=None,
                            )
                        )
                        chunk_id += 1
            else:
                # 전체 텍스트 청킹
                text_chunks = self._split_text_into_chunks(text)
                for chunk_text in text_chunks:
                    chunks.append(
                        DocumentChunk(
                            document_id=document_id,
                            version_no=version_no,
                            domain=domain,
                            title=title,
                            chunk_id=chunk_id,
                            chunk_text=chunk_text,
                            page=None,
                            section_path=None,
                        )
                    )
                    chunk_id += 1

            return chunks

        except Exception as e:
            raise ChunkingError(f"Failed to create chunks: {e}", original_error=e)

    def _split_text_into_chunks(self, text: str) -> List[str]:
        """
        텍스트를 오버랩 있는 청크로 분할합니다.

        Returns:
            List[str]: 청크 텍스트 리스트
        """
        if not text:
            return []

        chunks: List[str] = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + self._chunk_size, text_len)

            # 단어 경계에서 자르기 시도
            if end < text_len:
                # 마지막 공백 또는 줄바꿈 찾기
                last_space = text.rfind(" ", start, end)
                last_newline = text.rfind("\n", start, end)
                boundary = max(last_space, last_newline)

                if boundary > start:
                    end = boundary

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            # 다음 시작점 (오버랩 적용)
            start = end - self._chunk_overlap
            if start < 0:
                start = 0
            if start >= end:
                start = end  # 무한 루프 방지

        return chunks


# =============================================================================
# 싱글턴 인스턴스
# =============================================================================

_document_processor: Optional[DocumentProcessor] = None


def get_document_processor() -> DocumentProcessor:
    """DocumentProcessor 싱글턴 인스턴스를 반환합니다."""
    global _document_processor
    if _document_processor is None:
        _document_processor = DocumentProcessor()
    return _document_processor


def clear_document_processor() -> None:
    """DocumentProcessor 싱글턴 인스턴스를 제거합니다 (테스트용)."""
    global _document_processor
    _document_processor = None
