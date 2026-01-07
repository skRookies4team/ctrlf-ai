# app/clients/shotstack_client.py
import httpx
import os

SHOTSTACK_API_KEY = os.getenv("SHOTSTACK_API_KEY")

SHOTSTACK_ENDPOINT = "https://api.shotstack.io/v1/render"

async def enhance_education_video(
    input_video_url: str,
    title: str,
    subtitles: list[dict],  # [{start, end, text}]
):
    payload = {
        "timeline": {
            "background": "#000000",
            "tracks": [
                {
                    "clips": [
                        {
                            "asset": {
                                "type": "title",
                                "text": title,
                                "style": "minimal",
                                "size": "medium"
                            },
                            "start": 0,
                            "length": 4
                        }
                    ]
                },
                {
                    "clips": [
                        {
                            "asset": {
                                "type": "video",
                                "src": input_video_url
                            },
                            "start": 0,
                            "length": 999
                        }
                    ]
                }
            ]
        },
        "output": {
            "format": "mp4",
            "resolution": "hd"
        }
    }

    headers = {
        "x-api-key": SHOTSTACK_API_KEY,
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(SHOTSTACK_ENDPOINT, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()
