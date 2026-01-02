from __future__ import annotations
from typing import Any, Dict
import httpx


class HeyGenClient:
    """
    HeyGen API client
    """

    BASE_URL = "https://api.heygen.com"

    def __init__(self, api_key: str, timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    async def generate_video(self, payload: Dict[str, Any]) -> str:
        url = f"{self.BASE_URL}/v2/video/generate"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(url, headers=self._headers(), json=payload)
            r.raise_for_status()
            data = r.json()

        if "data" not in data or "video_id" not in data["data"]:
            raise RuntimeError(f"Unexpected HeyGen response: {data}")

        return data["data"]["video_id"]

    async def get_video_status(self, video_id: str) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/v1/video_status.get"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(
                url,
                headers=self._headers(),
                params={"video_id": video_id},
            )
            r.raise_for_status()
            return r.json()
