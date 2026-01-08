"""
API 라우트 확인 스크립트

등록된 API 엔드포인트를 확인합니다.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app

print("=" * 60)
print("등록된 API 라우트")
print("=" * 60)

# 모든 라우트 수집
routes = []
for route in app.routes:
    if hasattr(route, "path") and hasattr(route, "methods"):
        for method in route.methods:
            if method != "HEAD":  # HEAD는 제외
                routes.append((method, route.path))

# 정렬 및 출력
routes.sort(key=lambda x: x[1])

print("\n스크립트 생성 관련:")
for method, path in routes:
    if "script" in path.lower() or "generate" in path.lower():
        print(f"  {method:6} {path}")

print("\n렌더링 관련:")
for method, path in routes:
    if "render" in path.lower() or "job" in path.lower():
        print(f"  {method:6} {path}")

print("\n전체 라우트:")
for method, path in routes:
    print(f"  {method:6} {path}")

print(f"\n총 {len(routes)}개의 라우트가 등록되어 있습니다.")

