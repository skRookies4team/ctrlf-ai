# core/cleaner.py
"""
core.cleaner
통합 전처리기 (PyMuPDF text extract + 이미지 OCR + Smart Chunk)

Exports:
    - preprocess_text(raw_text_or_file_path) -> 문자열 또는 파일 경로 입력
    - process_file_pipeline(file_path, ...) -> 파일 단위 통합 파이프라인 (report dict 반환)
    - safe_smart_chunk(text, max_len=1200) -> 의미 단위 Smart Chunk
    - clean_text(text) -> 문자열 전처리 핵심
"""

import os
import re
import logging
from typing import List, Tuple, Optional, Any, Dict
from uuid import uuid4

try:
    from PIL import Image, ImageFilter, ImageEnhance
except ImportError:
    Image = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import fitz
except ImportError:
    fitz = None

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# ---------------------------
# 안전 문자열 변환
# ---------------------------
def safe_text(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="ignore")
    return str(x)


# ---------------------------
# 이미지 전처리 (OCR 품질 개선)
# ---------------------------
def preprocess_image_for_ocr(
    img: "Image.Image",
    do_bw: bool = True,
    enhance_contrast: float = 1.3,
    enhance_sharpness: float = 1.2,
    threshold: int = 150
) -> "Image.Image":
    if Image is None:
        raise RuntimeError("Pillow not installed")

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    try:
        img = img.filter(ImageFilter.MedianFilter(size=3))
        img = ImageEnhance.Contrast(img).enhance(enhance_contrast)
        img = ImageEnhance.Sharpness(img).enhance(enhance_sharpness)
        if do_bw:
            img = img.convert("L")
            img = img.point(lambda p: 255 if p > threshold else 0)
    except Exception:
        pass
    return img


# ---------------------------
# OCR 실행
# ---------------------------
def ocr_image_page(img: "Image.Image", lang: str = "kor+eng", psm: int = 3) -> str:
    if pytesseract is None or Image is None:
        return ""
    try:
        pre = preprocess_image_for_ocr(img)
        config = f"--psm {psm} -c preserve_interword_spaces=1"
        return safe_text(pytesseract.image_to_string(pre, lang=lang, config=config))
    except Exception as e:
        logger.warning("OCR failed: %s", e)
        return ""


# ---------------------------
# PDF 텍스트 추출 + OCR fallback
# ---------------------------
def pdf_extract_text_and_ocrs(file_path: str, min_text_len_for_skip: int = 20) -> Tuple[List[str], List[str]]:
    py_texts, ocr_texts = [], []
    if fitz is None:
        return [], []

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        logger.error("Failed to open PDF: %s", e)
        return [], []

    for page in doc:
        try:
            txt = page.get_text().strip()
        except Exception:
            txt = ""
        py_texts.append(safe_text(txt))

        ocr_txt = ""
        if len(txt.strip()) < min_text_len_for_skip and Image is not None:
            try:
                pix = page.get_pixmap(dpi=300, alpha=False)
                mode = "RGB" if pix.n < 4 else "RGBA"
                img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
                ocr_txt = ocr_image_page(img)
            except Exception as e:
                logger.warning("OCR failed for page: %s", e)
        ocr_texts.append(safe_text(ocr_txt))
    return py_texts, ocr_texts


# ---------------------------
# 페이지 병합
# ---------------------------
def merge_page_texts(py_text: str, ocr_text: str) -> str:
    py_lines = [l.strip() for l in py_text.splitlines() if l.strip()]
    ocr_lines = [l.strip() for l in ocr_text.splitlines() if l.strip()]
    seen, merged = set(), []

    for line in py_lines + ocr_lines:
        if line not in seen:
            merged.append(line)
            seen.add(line)
    return "\n".join(merged)


def merge_pdf_texts(py_texts: List[str], ocr_texts: List[str]) -> str:
    pages = [merge_page_texts(py, ocr) for py, ocr in zip(py_texts, ocr_texts)]
    return "\n\n===PAGE_BREAK===\n\n".join([p for p in pages if p.strip()])


# ---------------------------
# 문자열 정제 (핵심)
# ---------------------------
def _clean_text_core(raw: str) -> str:
    text = safe_text(raw)
    if not text:
        return ""

    text = re.sub(r'[\x00-\x1f]', ' ', text)

    # 반복 라인 제거
    lines = [l.rstrip() for l in text.splitlines()]
    freq = {}
    for l in lines:
        s = l.strip()
        if 0 < len(s) <= 120:
            freq[s] = freq.get(s, 0) + 1
    repeated = {k for k, v in freq.items() if v > 2}
    filtered = [l for l in lines if l.strip() not in repeated]
    text = "\n".join(filtered)

    # page number 제거
    text = re.sub(r'Page\s*\d+(\s*/\s*\d+)?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b\d+\s*/\s*\d+\b', '', text)

    # 기타 정규화
    text = re.sub(r'[-\u2010-\u2014]+', '-', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r',\s*,+', ',', text)

    # 라인 이어붙이기
    out_lines, buf = [], ""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            if buf:
                out_lines.append(buf.strip())
                buf = ""
            continue
        if not buf:
            buf = s
        elif re.search(r'[.?!\u3002\uFF01\uFF1F]$', buf):
            out_lines.append(buf.strip())
            buf = s
        else:
            buf = buf + " " + s
    if buf:
        out_lines.append(buf.strip())

    cleaned = "\n\n".join(out_lines)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned


# ---------------------------
# 하위호환 래퍼 (파일 or 문자열)
# ---------------------------
def clean_text_wrapper(input_data: Any) -> str:
    s = safe_text(input_data)
    if isinstance(input_data, str) and os.path.exists(input_data):
        try:
            with open(input_data, "rb") as f:
                raw = f.read().decode("utf-8", errors="ignore")
        except Exception:
            raw = s
    else:
        raw = s
    return _clean_text_core(raw)


# ---------------------------
# Smart Chunk
# ---------------------------
def safe_smart_chunk(text: str, max_len: int = 1200) -> List[str]:
    text = _clean_text_core(text)
    if not text:
        return []

    pages = [p.strip() for p in re.split(r'\n{2,}===PAGE_BREAK===\n{2,}', text) if p.strip()]
    paras: List[str] = []
    for p in pages:
        paras.extend([s.strip() for s in re.split(r'\n{2,}', p) if s.strip()])

    chunks: List[str] = []
    buf = ""
    for para in paras:
        para_clean = re.sub(r'[^\w\s가-힣.,\-]', ' ', para).strip()
        if not para_clean:
            continue
        if len(para_clean) >= max_len:
            pieces = [para_clean[i:i+max_len] for i in range(0, len(para_clean), max_len)]
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(pieces[:-1])
            buf = pieces[-1]
            continue

        if not buf:
            buf = para_clean
        elif len(buf) + len(para_clean) + 2 <= max_len:
            buf += "\n\n" + para_clean
        else:
            chunks.append(buf)
            buf = para_clean
    if buf:
        chunks.append(buf)
    return chunks


# ---------------------------
# 파일 단위 전처리
# ---------------------------
def process_file_pipeline(
    file_path: str,
    file_name: Optional[str] = None,
    embedding_fn: Optional[Any] = None,
    vector_store: Optional[Any] = None,
    always_run_ocr: bool = False,
    min_text_len_for_skip: int = 30,
    chunk_max_len: int = 1200,
) -> Dict[str, Any]:

    file_name = file_name or os.path.basename(file_path)
    raw_text, merged_text = "", ""

    if file_path.lower().endswith(".pdf") and fitz is not None:
        py_texts, ocr_texts = pdf_extract_text_and_ocrs(file_path, min_text_len_for_skip)
        if always_run_ocr and pytesseract is not None:
            try:
                doc = fitz.open(file_path)
                ocr_texts = []
                for page in doc:
                    pix = page.get_pixmap(dpi=300, alpha=False)
                    mode = "RGB" if pix.n < 4 else "RGBA"
                    img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
                    ocr_texts.append(ocr_image_page(img))
            except Exception as e:
                logger.warning("Full OCR fallback failed: %s", e)
        merged_text = merge_pdf_texts(py_texts, ocr_texts)
        raw_text = "\n\n".join(py_texts) if any(py_texts) else "\n\n".join(ocr_texts)
    else:
        try:
            with open(file_path, "rb") as f:
                raw_text = f.read().decode("utf-8", errors="ignore")
        except Exception as e:
            logger.warning("Failed reading text file: %s", e)
        merged_text = raw_text

    cleaned = _clean_text_core(merged_text or raw_text)
    chunks = safe_smart_chunk(cleaned, max_len=chunk_max_len)

    # optional embedding
    inserted = 0
    try:
        if embedding_fn and vector_store and chunks:
            vectors = [embedding_fn(c) for c in chunks]
            metadatas = [{"file_name": file_name, "chunk_index": i, "text_preview": c[:200]} for i, c in enumerate(chunks)]
            if hasattr(vector_store, "add_vectors"):
                vector_store.add_vectors(vectors, metadatas)
                inserted = len(vectors)
            elif hasattr(vector_store, "add_item"):
                for i, v in enumerate(vectors):
                    vector_store.add_item(id=str(uuid4()), vector=v, metadata=metadatas[i])
                inserted = len(vectors)
    except Exception as e:
        logger.error("Embedding error: %s", e)

    return {
        "file_name": file_name,
        "raw_text_length": len(raw_text),
        "merged_text_length": len(merged_text),
        "cleaned_length": len(cleaned),
        "num_chunks": len(chunks),
        "inserted_vectors": inserted,
        "chunks_preview": [c[:400] for c in chunks[:10]]
    }


# ---------------------------
# 외부 노출
# ---------------------------
preprocess_text = clean_text_wrapper  # 파일/문자열 호환
clean_text = _clean_text_core         # 순수 문자열용

__all__ = [
    "safe_text",
    "preprocess_text",
    "clean_text",
    "pdf_extract_text_and_ocrs",
    "ocr_image_page",
    "preprocess_image_for_ocr",
    "merge_pdf_texts",
    "safe_smart_chunk",
    "process_file_pipeline",
]
