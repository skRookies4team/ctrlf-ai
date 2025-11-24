"""
OCR - PDF에서 텍스트 추출 실패 시 OCR fallback
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def run_ocr(pdf_path: str) -> Optional[str]:
    """
    PDF 파일에 대해 OCR 수행 (Tesseract + pdf2image)

    - pdf2image로 PDF를 이미지로 변환
    - pytesseract로 각 페이지에서 텍스트 추출
    - 모든 페이지의 텍스트를 합쳐서 반환

    Args:
        pdf_path: PDF 파일 경로

    Returns:
        Optional[str]: 추출된 텍스트 (실패 시 None)
    """
    try:
        # pdf2image와 pytesseract import
        from pdf2image import convert_from_path
        import pytesseract

        logger.info(f"Running OCR on {pdf_path}")

        # PDF를 이미지로 변환
        try:
            images = convert_from_path(pdf_path)
            logger.info(f"Converted PDF to {len(images)} images")
        except Exception as e:
            logger.error(f"Failed to convert PDF to images: {e}")
            return None

        # 각 페이지에서 텍스트 추출
        all_text = []
        for i, image in enumerate(images):
            try:
                # Tesseract OCR 수행 (한국어 + 영어)
                text = pytesseract.image_to_string(image, lang='kor+eng')
                if text.strip():
                    all_text.append(text)
                    logger.info(f"Extracted text from page {i+1}: {len(text)} characters")
                else:
                    logger.warning(f"No text extracted from page {i+1}")
            except Exception as e:
                logger.error(f"OCR failed on page {i+1}: {e}")
                # 페이지 하나 실패해도 계속 진행
                continue

        # 모든 페이지 텍스트 합치기
        if all_text:
            combined_text = "\n\n".join(all_text)
            logger.info(f"OCR completed: {len(combined_text)} total characters")
            return combined_text
        else:
            logger.warning("OCR completed but no text extracted")
            return None

    except ImportError as e:
        logger.error(f"OCR dependencies not installed: {e}")
        logger.error("Please install: pip install pytesseract pdf2image")
        logger.error("Also install Tesseract-OCR system package")
        return None

    except Exception as e:
        logger.error(f"Unexpected OCR error: {e}", exc_info=True)
        return None


# 간단한 테스트 코드
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python core/ocr.py <pdf_file_path>")
        sys.exit(1)

    test_pdf_path = sys.argv[1]

    logging.basicConfig(level=logging.INFO)

    print(f"\nTesting OCR with: {test_pdf_path}\n")
    result_text = run_ocr(test_pdf_path)

    if result_text:
        print("\n===== OCR Result =====")
        print(f"Extracted text length: {len(result_text)}")
        print(f"First 500 characters:\n{result_text[:500]}")
        print("======================\n")
    else:
        print("\n===== OCR Failed =====\n")
