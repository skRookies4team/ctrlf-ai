import asyncio
import httpx
from typing import Dict, Any


class HeyGenClient:
    """
    HeyGen API Client
    """

    BASE_URL = "https://api.heygen.com"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("HeyGen API key is required")
        self.api_key = api_key

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------
    # Video Generate
    # ------------------------------------------------------------
    async def generate_video(self, payload: Dict[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{self.BASE_URL}/v2/video/generate",
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()

            data = r.json()
            if "data" not in data or "video_id" not in data["data"]:
                raise RuntimeError(f"Unexpected response: {data}")

            return data["data"]["video_id"]

    # ------------------------------------------------------------
    # Video Status (timeout 안전 처리)
    # ------------------------------------------------------------
    async def get_video_status(
        self,
        video_id: str,
        retries: int = 3,
        retry_delay_sec: int = 5,
    ) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/v1/video_status.get"

        for i in range(retries):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    r = await client.get(
                        url,
                        headers=self._headers(),
                        params={"video_id": video_id},
                    )
                    r.raise_for_status()
                    return r.json()

            except httpx.ReadTimeout:
                if i == retries - 1:
                    raise
                await asyncio.sleep(retry_delay_sec)
