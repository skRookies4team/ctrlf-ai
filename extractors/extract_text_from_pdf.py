# langflow/extractors/extract_text_from_pdf.py
from PyPDF2 import PdfReader

def extract_text_from_pdf(file_bytes: bytes, is_bytes=True):
    """
    PDF 파일에서 텍스트 추출
    file_bytes: 업로드된 파일 데이터
    """
    from io import BytesIO

    if is_bytes:
        reader = PdfReader(BytesIO(file_bytes))
    else:
        reader = PdfReader(file_bytes)

    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"

    return text
