"""
HWP 텍스트 변환 어댑터

여러 HWP 변환 방법을 제공하고 환경에 따라 자동 선택합니다.
"""
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ========================================
# HWP 변환 방법 체크
# ========================================

def _check_hwp5txt_available() -> bool:
    """hwp5txt CLI 도구 사용 가능 여부 확인"""
    try:
        result = subprocess.run(
            ["hwp5txt", "--version"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_libreoffice_available() -> bool:
    """LibreOffice CLI 도구 사용 가능 여부 확인"""
    try:
        result = subprocess.run(
            ["soffice", "--version"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ========================================
# 변환 방법 1: hwp5txt (세희 방식, Linux 권장)
# ========================================

def convert_hwp_with_hwp5txt(hwp_path: str) -> str:
    """
    hwp5txt CLI 도구로 HWP → 텍스트 변환

    ⚠️ 세희 코드에서 가져온 방식 (prompt.txt:65-76)
    ⚠️ Linux/Mac 전용 (hwp5 패키지 필요)

    설치:
        - Ubuntu: sudo apt-get install hwp5 && pip install hwp5
        - Mac: brew install hwp5 && pip install hwp5

    Args:
        hwp_path: HWP 파일 경로

    Returns:
        str: 추출된 텍스트

    Raises:
        FileNotFoundError: hwp5txt 명령어가 없을 때
        subprocess.CalledProcessError: 변환 실패 시
    """
    hwp_path = Path(hwp_path).resolve()

    if not hwp_path.exists():
        raise FileNotFoundError(f"HWP file not found: {hwp_path}")

    logger.info(f"[hwp5txt] Converting HWP: {hwp_path.name}")

    try:
        result = subprocess.run(
            ["hwp5txt", str(hwp_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60  # 60초 타임아웃
        )

        text = result.stdout
        logger.info(f"[hwp5txt] Extracted {len(text)} chars from {hwp_path.name}")
        return text

    except FileNotFoundError:
        logger.error("hwp5txt not found. Install: pip install hwp5 (Linux/Mac only)")
        raise

    except subprocess.CalledProcessError as e:
        logger.error(f"hwp5txt conversion failed: {e.stderr}")
        raise

    except subprocess.TimeoutExpired:
        logger.error(f"hwp5txt timeout (>60s) for {hwp_path.name}")
        raise


# ========================================
# 변환 방법 2: LibreOffice CLI (크로스 플랫폼)
# ========================================

def convert_hwp_with_libreoffice(hwp_path: str) -> str:
    """
    LibreOffice CLI로 HWP → 텍스트 변환

    ✅ Windows/Linux/Mac 모두 지원
    ⚠️ LibreOffice 설치 필요 (약 300MB)

    설치:
        - Windows: choco install libreoffice
        - Ubuntu: sudo apt-get install libreoffice
        - Mac: brew install libreoffice

    Args:
        hwp_path: HWP 파일 경로

    Returns:
        str: 추출된 텍스트

    Raises:
        FileNotFoundError: soffice 명령어가 없을 때
        RuntimeError: 변환 실패 시
    """
    hwp_path = Path(hwp_path).resolve()

    if not hwp_path.exists():
        raise FileNotFoundError(f"HWP file not found: {hwp_path}")

    logger.info(f"[LibreOffice] Converting HWP: {hwp_path.name}")

    # 임시 출력 디렉토리 생성
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # HWP → TXT 변환
            result = subprocess.run(
                [
                    "soffice",  # LibreOffice CLI
                    "--headless",  # GUI 없이 실행
                    "--convert-to", "txt:Text",
                    "--outdir", tmpdir,
                    str(hwp_path)
                ],
                capture_output=True,
                text=True,
                timeout=120  # 120초 타임아웃
            )

            # 변환된 TXT 파일 경로
            txt_file = Path(tmpdir) / f"{hwp_path.stem}.txt"

            if txt_file.exists():
                text = txt_file.read_text(encoding="utf-8")
                logger.info(f"[LibreOffice] Extracted {len(text)} chars from {hwp_path.name}")
                return text
            else:
                logger.error(f"LibreOffice conversion failed: {result.stderr}")
                raise RuntimeError(f"Conversion failed: {result.stderr}")

        except FileNotFoundError:
            logger.error("soffice (LibreOffice) not found. Install LibreOffice first.")
            raise

        except subprocess.TimeoutExpired:
            logger.error(f"LibreOffice timeout (>120s) for {hwp_path.name}")
            raise RuntimeError("LibreOffice conversion timeout")


# ========================================
# 변환 방법 3: pyhwp (Python 2 전용, Deprecated)
# ========================================

def convert_hwp_with_pyhwp(hwp_path: str) -> str:
    """
    pyhwp 라이브러리로 HWP → 텍스트 변환

    ❌ Python 2 전용 (Python 3에서 설치 실패)
    ⚠️ Deprecated: hwp5txt 또는 LibreOffice 사용 권장

    Args:
        hwp_path: HWP 파일 경로

    Returns:
        str: 추출된 텍스트

    Raises:
        ImportError: pyhwp 설치 안되어 있을 때
    """
    try:
        import pyhwp
    except ImportError:
        logger.error("pyhwp not installed (Python 2 only)")
        raise ImportError("pyhwp requires Python 2. Use hwp5txt or LibreOffice instead.")

    hwp_path = Path(hwp_path).resolve()

    if not hwp_path.exists():
        raise FileNotFoundError(f"HWP file not found: {hwp_path}")

    logger.info(f"[pyhwp] Converting HWP: {hwp_path.name}")

    text = ""
    try:
        doc = pyhwp.HWPDocument(str(hwp_path))
        for para in doc.bodytext.paragraphs:
            for run in para.text:
                text += run.text
            text += "\n"

        logger.info(f"[pyhwp] Extracted {len(text)} chars from {hwp_path.name}")
        return text

    except Exception as e:
        logger.error(f"pyhwp conversion failed: {e}")
        raise RuntimeError(f"pyhwp conversion failed: {e}")


# ========================================
# 통합 변환 함수 (자동 선택)
# ========================================

def convert_hwp_to_text(hwp_path: str, method: Optional[str] = None) -> str:
    """
    HWP 파일을 텍스트로 변환 (여러 방법 중 자동 선택)

    우선순위:
    1. method 파라미터 지정 시: 해당 방법 사용
    2. hwp5txt 사용 가능 시: hwp5txt (세희 방식, 가장 안정적)
    3. LibreOffice 사용 가능 시: LibreOffice (크로스 플랫폼)
    4. 모두 실패 시: 빈 문자열 반환 (graceful fallback)

    Args:
        hwp_path: HWP 파일 경로
        method: 변환 방법 ("hwp5txt" | "libreoffice" | "pyhwp" | None)

    Returns:
        str: 추출된 텍스트 (실패 시 빈 문자열)
    """
    hwp_path = Path(hwp_path).resolve()

    if not hwp_path.exists():
        logger.error(f"HWP file not found: {hwp_path}")
        return ""

    # 1. 지정된 방법 사용
    if method:
        logger.info(f"Using specified method: {method}")
        try:
            if method == "hwp5txt":
                return convert_hwp_with_hwp5txt(str(hwp_path))
            elif method == "libreoffice":
                return convert_hwp_with_libreoffice(str(hwp_path))
            elif method == "pyhwp":
                return convert_hwp_with_pyhwp(str(hwp_path))
            else:
                logger.warning(f"Unknown method: {method}, falling back to auto")
        except Exception as e:
            logger.error(f"Specified method '{method}' failed: {e}")
            return ""

    # 2. hwp5txt 시도 (세희 방식, 최우선)
    if _check_hwp5txt_available():
        logger.info("Using hwp5txt (preferred method)")
        try:
            return convert_hwp_with_hwp5txt(str(hwp_path))
        except Exception as e:
            logger.warning(f"hwp5txt failed: {e}, trying next method")

    # 3. LibreOffice 시도
    if _check_libreoffice_available():
        logger.info("Using LibreOffice")
        try:
            return convert_hwp_with_libreoffice(str(hwp_path))
        except Exception as e:
            logger.warning(f"LibreOffice failed: {e}")

    # 4. pyhwp 시도 (Deprecated)
    try:
        logger.info("Using pyhwp (deprecated)")
        return convert_hwp_with_pyhwp(str(hwp_path))
    except Exception as e:
        logger.warning(f"pyhwp failed: {e}")

    # 5. 모두 실패 - graceful fallback
    logger.error(f"All HWP conversion methods failed for {hwp_path.name}")
    logger.error("Please install one of: hwp5txt (Linux), LibreOffice (any OS)")
    return ""


# ========================================
# 유틸리티 함수
# ========================================

def get_available_methods() -> list[str]:
    """사용 가능한 HWP 변환 방법 목록 반환"""
    methods = []

    if _check_hwp5txt_available():
        methods.append("hwp5txt")

    if _check_libreoffice_available():
        methods.append("libreoffice")

    try:
        import pyhwp
        methods.append("pyhwp")
    except ImportError:
        pass

    return methods


def get_recommended_method() -> Optional[str]:
    """환경에 맞는 권장 HWP 변환 방법 반환"""
    if _check_hwp5txt_available():
        return "hwp5txt"  # 세희 방식, 가장 안정적
    elif _check_libreoffice_available():
        return "libreoffice"  # 크로스 플랫폼
    else:
        return None


if __name__ == "__main__":
    # 사용 가능한 변환 방법 출력
    print("Available HWP conversion methods:")
    methods = get_available_methods()
    if methods:
        for method in methods:
            print(f"  - {method}")
    else:
        print("  (none)")

    recommended = get_recommended_method()
    if recommended:
        print(f"\nRecommended: {recommended}")
    else:
        print("\n⚠️ No HWP converter available. Install hwp5 or LibreOffice.")
