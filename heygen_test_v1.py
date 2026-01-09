import os
import requests

url = "https://api.heygen.com/v1/video/generate"
api_key = os.getenv("HEYGEN_API_KEY", "").strip()
if not api_key:
    raise RuntimeError("HEYGEN_API_KEY가 설정되지 않았습니다. .env 또는 환경변수로 설정해주세요.")

payload = {
    "video_inputs": [
        {
            "character": {
                "type": "avatar",
                "avatar_id": "Jin_Blue_Casual_Side_public"
            },
            "voice": {
                "voice_id": "04515ba5ae2e431386807be5df246e72"
            },
            "background": {
                "type": "color",
                "value": "#FFFFFF"
            },
            "script": [
                {
                    "type": "text",
                    "input": "안녕하세요. 테스트 영상입니다."
                }
            ]
        }
    ],
    "dimension": {
        "width": 1280,
        "height": 720
    }
}

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

r = requests.post(url, headers=headers, json=payload)

print("STATUS:", r.status_code)
print("RESPONSE:")
print(r.text)
