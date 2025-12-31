"""
RAGFlow 서버 연결 테스트 스크립트

사용법:
    python scripts/test_ragflow_connection.py

환경변수:
    RAGFLOW_HOST: RAGFlow 서버 주소 (기본: 58.127.241.84:8765)
    RAGFLOW_EMAIL: 로그인 이메일
    RAGFLOW_PASSWORD: 로그인 비밀번호
"""

import os
import sys
import httpx
import asyncio
from typing import Optional

# RAGFlow 서버 설정
RAGFLOW_HOST = os.getenv("RAGFLOW_HOST", "http://58.127.241.84:8765")
RAGFLOW_EMAIL = os.getenv("RAGFLOW_EMAIL", "lulla1613@gmail.com")
RAGFLOW_PASSWORD = os.getenv("RAGFLOW_PASSWORD", "asdf1234*")


class RAGFlowTestClient:
    """RAGFlow API 테스트 클라이언트"""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = None
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def health_check(self) -> dict:
        """서버 상태 확인"""
        try:
            resp = await self.client.get(f"{self.base_url}/")
            return {
                "status": "ok" if resp.status_code == 200 else "error",
                "status_code": resp.status_code,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def login(self, email: str, password: str) -> dict:
        """RAGFlow 로그인"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/v1/user/login",
                json={"email": email, "password": password},
                headers={"Content-Type": "application/json"},
            )
            data = resp.json()

            if data.get("code") == 0 and data.get("data"):
                # 로그인 성공 - 토큰 저장
                self.token = data["data"].get("access_token") or data["data"].get("token")
                return {"status": "success", "data": data["data"]}
            else:
                return {"status": "error", "code": data.get("code"), "message": data.get("message")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_datasets(self) -> dict:
        """데이터셋 목록 조회"""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            resp = await self.client.get(
                f"{self.base_url}/v1/dataset",
                headers=headers,
            )
            return resp.json()
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def test_retrieval(self, dataset_id: str, query: str) -> dict:
        """RAG 검색 테스트"""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            resp = await self.client.post(
                f"{self.base_url}/v1/retrieval",
                headers=headers,
                json={
                    "dataset_ids": [dataset_id],
                    "question": query,
                    "top_k": 5,
                },
            )
            return resp.json()
        except Exception as e:
            return {"status": "error", "error": str(e)}


async def main():
    print("=" * 60)
    print("RAGFlow 연결 테스트")
    print("=" * 60)
    print(f"서버: {RAGFLOW_HOST}")
    print(f"이메일: {RAGFLOW_EMAIL}")
    print()

    client = RAGFlowTestClient(RAGFLOW_HOST)

    try:
        # 1. 서버 연결 확인
        print("[1] 서버 연결 테스트...")
        health = await client.health_check()
        print(f"    결과: {health}")

        if health.get("status") != "ok":
            print("    ❌ 서버 연결 실패!")
            return
        print("    ✅ 서버 연결 성공!")

        # 2. 로그인 테스트
        print()
        print("[2] 로그인 테스트...")
        login_result = await client.login(RAGFLOW_EMAIL, RAGFLOW_PASSWORD)
        print(f"    결과: {login_result}")

        if login_result.get("status") != "success":
            print("    ❌ 로그인 실패!")
            print(f"    메시지: {login_result.get('message', login_result.get('error'))}")
            print()
            print("    해결 방법:")
            print(f"    1. 브라우저에서 {RAGFLOW_HOST} 접속 후 회원가입")
            print("    2. 또는 올바른 이메일/비밀번호 확인")
            return

        print("    ✅ 로그인 성공!")

        # 3. 데이터셋 조회
        print()
        print("[3] 데이터셋 목록 조회...")
        datasets = await client.get_datasets()
        print(f"    결과: {datasets}")

        if datasets.get("code") == 0:
            print("    ✅ 데이터셋 조회 성공!")
            if datasets.get("data"):
                print("    데이터셋 목록:")
                for ds in datasets["data"]:
                    print(f"      - {ds.get('name')} (ID: {ds.get('id')})")

        # 4. 검색 테스트 (데이터셋이 있을 경우)
        if datasets.get("code") == 0 and datasets.get("data"):
            print()
            print("[4] RAG 검색 테스트...")
            first_dataset = datasets["data"][0]
            search_result = await client.test_retrieval(
                first_dataset["id"],
                "정보보안 관련 규정"
            )
            print(f"    결과: {search_result}")

        print()
        print("=" * 60)
        print("테스트 완료!")
        print("=" * 60)

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
