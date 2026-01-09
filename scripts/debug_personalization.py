"""
개인화 API 디버깅 스크립트

개인화 질문 처리 중 발생하는 오류를 진단하는 도구입니다.

사용법:
    python scripts/debug_personalization.py
    python scripts/debug_personalization.py --user-id "test-user-123" --query "내 연차 몇개 남음?"
"""

import asyncio
import os
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.clients.personalization_client import PersonalizationClient
from app.core.config import get_settings
from app.services.personalization_mapper import to_personalization_q


async def test_personalization_flow(
    user_id: str = "test-user-123",
    query: str = "내 연차 몇개 남음?",
    sub_intent_id: str = "HR_LEAVE_CHECK",
):
    """개인화 플로우를 테스트합니다."""
    settings = get_settings()
    
    print("=" * 80)
    print("개인화 API 디버깅")
    print("=" * 80)
    print(f"\n📋 설정 정보:")
    print(f"  - PERSONALIZATION_MODE: {settings.PERSONALIZATION_MODE}")
    print(f"  - BACKEND_BASE_URL: {settings.backend_base_url or '(설정되지 않음)'}")
    print(f"  - BACKEND_BASE_URL_REAL: {settings.BACKEND_BASE_URL_REAL or '(설정되지 않음)'}")
    
    print(f"\n🔍 테스트 파라미터:")
    print(f"  - User ID: {user_id}")
    print(f"  - Query: {query}")
    print(f"  - Sub Intent ID: {sub_intent_id}")
    
    # 1. Personalization Q 매핑 테스트
    print(f"\n[1단계] Personalization Q 매핑")
    print("-" * 80)
    personalization_q = to_personalization_q(sub_intent_id, query)
    if personalization_q:
        print(f"✅ 매핑 성공: {sub_intent_id} → {personalization_q}")
    else:
        print(f"❌ 매핑 실패: {sub_intent_id}에 대한 개인화 Q를 찾을 수 없습니다.")
        return
    
    # 2. PersonalizationClient 초기화 확인
    print(f"\n[2단계] PersonalizationClient 초기화")
    print("-" * 80)
    client = PersonalizationClient()
    if client.is_configured:
        print(f"✅ 백엔드 URL 설정됨: {client._base_url}")
    else:
        print(f"⚠️  백엔드 URL 미설정 (mock 모드로 동작할 수 있음)")
        if settings.PERSONALIZATION_MODE == "real":
            print(f"❌ PERSONALIZATION_MODE=real인데 BACKEND_BASE_URL이 설정되지 않았습니다!")
            print(f"   환경변수를 설정하거나 .env 파일에 BACKEND_BASE_URL을 추가하세요.")
            return
    
    # 3. Facts 조회 테스트
    print(f"\n[3단계] Facts 조회")
    print("-" * 80)
    try:
        facts = await client.resolve_facts(
            sub_intent_id=personalization_q,
            user_id=user_id,
            period=None,
        )
        
        if facts.error:
            print(f"❌ Facts 조회 실패:")
            print(f"   Error Type: {facts.error.type}")
            print(f"   Error Message: {facts.error.message}")
            
            if facts.error.type == "CONFIG_ERROR":
                print(f"\n💡 해결 방법:")
                print(f"   1. BACKEND_BASE_URL 환경변수를 설정하세요:")
                print(f"      export BACKEND_BASE_URL=http://localhost:8080")
                print(f"   2. 또는 .env 파일에 추가하세요:")
                print(f"      BACKEND_BASE_URL=http://localhost:8080")
            elif facts.error.type == "NETWORK_ERROR":
                print(f"\n💡 해결 방법:")
                print(f"   1. 백엔드 서버가 실행 중인지 확인하세요")
                print(f"   2. 네트워크 연결을 확인하세요")
                print(f"   3. BACKEND_BASE_URL이 올바른지 확인하세요")
            elif facts.error.type == "HTTP_ERROR":
                print(f"\n💡 해결 방법:")
                print(f"   1. 백엔드 API 엔드포인트가 올바른지 확인하세요")
                print(f"   2. 백엔드 서버 로그를 확인하세요")
            elif facts.error.type == "NOT_IMPLEMENTED":
                print(f"\n💡 해결 방법:")
                print(f"   - 이 인텐트는 아직 백엔드에서 구현되지 않았습니다.")
        else:
            print(f"✅ Facts 조회 성공:")
            print(f"   - Metrics: {list(facts.metrics.keys()) if facts.metrics else '없음'}")
            print(f"   - Items: {len(facts.items) if facts.items else 0}개")
            if facts.period_start:
                print(f"   - Period: {facts.period_start} ~ {facts.period_end}")
            
            # 메트릭 값 샘플 출력
            if facts.metrics:
                print(f"\n   📊 Metrics 샘플:")
                for key, value in list(facts.metrics.items())[:5]:
                    print(f"      - {key}: {value}")
            
            # Items 샘플 출력
            if facts.items:
                print(f"\n   📋 Items 샘플 (최대 3개):")
                for i, item in enumerate(facts.items[:3], 1):
                    print(f"      [{i}] {item}")
        
    except Exception as e:
        print(f"❌ 예외 발생:")
        print(f"   Error Type: {type(e).__name__}")
        print(f"   Error Message: {str(e)}")
        import traceback
        print(f"\n   Stack Trace:")
        traceback.print_exc()
    
    print("\n" + "=" * 80)


async def main():
    """메인 함수"""
    import argparse
    
    parser = argparse.ArgumentParser(description="개인화 API 디버깅 스크립트")
    parser.add_argument("--user-id", default="test-user-123", help="테스트할 사용자 ID")
    parser.add_argument("--query", default="내 연차 몇개 남음?", help="테스트할 질문")
    parser.add_argument("--sub-intent-id", default="HR_LEAVE_CHECK", help="Sub Intent ID")
    
    args = parser.parse_args()
    
    await test_personalization_flow(
        user_id=args.user_id,
        query=args.query,
        sub_intent_id=args.sub_intent_id,
    )


if __name__ == "__main__":
    asyncio.run(main())

