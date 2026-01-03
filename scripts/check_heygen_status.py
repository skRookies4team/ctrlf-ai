import sys
import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# 🔥 프로젝트 루트 강제 등록
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.clients.heygen_client import HeyGenClient

load_dotenv(ROOT_DIR / ".env")

VIDEO_ID = "227e35bbfd084c23b5709d5c65b389f3"

async def main():
    client = HeyGenClient(api_key=os.getenv("HEYGEN_API_KEY"))
    status = await client.get_video_status(VIDEO_ID)
    print(status)

if __name__ == "__main__":
    asyncio.run(main())
