"""
백엔드 설정 확인 스크립트

BACKEND_BASE_URL이 올바르게 설정되어 있는지 확인합니다.
"""

import os
from dotenv import load_dotenv

load_dotenv()

print("=" * 80)
print("백엔드 설정 확인")
print("=" * 80)
print()

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL")
BACKEND_BASE_URL_REAL = os.getenv("BACKEND_BASE_URL_REAL")
BACKEND_API_TOKEN = os.getenv("BACKEND_API_TOKEN")
BACKEND_INTERNAL_TOKEN = os.getenv("BACKEND_INTERNAL_TOKEN")

print("환경 변수:")
print(f"  BACKEND_BASE_URL: {BACKEND_BASE_URL or '(설정되지 않음)'}")
print(f"  BACKEND_BASE_URL_REAL: {BACKEND_BASE_URL_REAL or '(설정되지 않음)'}")
print(f"  BACKEND_API_TOKEN: {'설정됨' if BACKEND_API_TOKEN else '(설정되지 않음)'}")
print(f"  BACKEND_INTERNAL_TOKEN: {'설정됨' if BACKEND_INTERNAL_TOKEN else '(설정되지 않음)'}")
print()

# 설정에서 실제로 사용될 URL 계산
if BACKEND_BASE_URL:
    effective_url = BACKEND_BASE_URL
    source = "BACKEND_BASE_URL (직접 설정)"
elif BACKEND_BASE_URL_REAL:
    effective_url = BACKEND_BASE_URL_REAL
    source = "BACKEND_BASE_URL_REAL"
else:
    effective_url = None
    source = "(미설정)"

print("=" * 80)
print("실제 사용될 백엔드 URL")
print("=" * 80)
print(f"출처: {source}")
print(f"URL: {effective_url or '(설정되지 않음 - 백엔드로 로그 전송 안 됨)'}")
print()

if not effective_url:
    print("[ERROR] 백엔드 URL이 설정되지 않았습니다!")
    print()
    print("해결 방법:")
    print("  1. .env 파일에 다음을 추가:")
    print("     BACKEND_BASE_URL=http://localhost:9003")
    print("     또는")
    print("     BACKEND_BASE_URL_REAL=http://localhost:9003")
    print()
    print("  2. AI Gateway를 재시작하세요")
else:
    print("[OK] 백엔드 URL이 설정되어 있습니다.")
    print()
    print("백엔드가 실행 중인지 확인:")
    print(f"  curl {effective_url}/actuator/health")
    print()
    print("백엔드 로그 전송 테스트:")
    print("  1. 채팅을 보내세요")
    print("  2. AI Gateway 로그에서 다음 메시지를 확인하세요:")
    print("     - '[AI_LOG] Backend saved' (성공)")
    print("     - '[AI_LOG] Backend save failed' (실패)")
