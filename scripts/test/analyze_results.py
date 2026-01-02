# -*- coding: utf-8 -*-
import pandas as pd
import re
import sys

# Windows console encoding fix
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

df = pd.read_excel('질답리스트.xlsx')

print(f"Total rows: {len(df)}")

# Column layout:
# 4: 질문, 5: 응답, 9: 라우트, 15: 에러

# 성공 = 에러 없음 (column 15 is NaN)
success_mask = df.iloc[:, 15].isna()
fail_mask = df.iloc[:, 15].notna()

success_df = df[success_mask]
fail_df = df[fail_mask]

print(f"Success: {len(success_df)}, Fail: {len(fail_df)}")

# 한국어/영어 감지 함수
def is_korean(text):
    if pd.isna(text) or not isinstance(text, str):
        return False
    korean_chars = len(re.findall(r'[\u3131-\u3163\uac00-\ud7a3]', text))
    return korean_chars > 10

def is_english_dominant(text):
    if pd.isna(text) or not isinstance(text, str):
        return False
    english_words = len(re.findall(r'[A-Za-z]{4,}', text))
    korean_chars = len(re.findall(r'[\u3131-\u3163\uac00-\ud7a3]', text))
    return english_words > 20 and korean_chars < 50

korean_count = 0
english_count = 0
english_samples = []

for idx in success_df.index:
    response = df.iloc[idx, 5]  # 응답 컬럼
    question = df.iloc[idx, 4]  # 질문 컬럼
    route = df.iloc[idx, 9]     # 라우트 컬럼

    if is_english_dominant(response):
        english_count += 1
        if len(english_samples) < 5:
            english_samples.append((question, response, route))
    elif is_korean(response):
        korean_count += 1

total = len(success_df)
if total > 0:
    print(f"\n=== Language Analysis (Success: {total}) ===")
    print(f"Korean responses: {korean_count} ({korean_count/total*100:.1f}%)")
    print(f"English responses: {english_count} ({english_count/total*100:.1f}%)")

    # 이전 vs 현재 비교
    print(f"\n=== Before vs After ===")
    print(f"Before (Phase 52): 61.3% English (396/646)")
    print(f"After (Phase 53):  {english_count/total*100:.1f}% English ({english_count}/{total})")

    improvement = 61.3 - (english_count/total*100)
    print(f"Improvement: -{improvement:.1f}% English responses")

# 영어 응답 샘플 확인
if english_count > 0:
    print(f"\n=== English Response Samples (max 5) ===")
    for q, a, r in english_samples:
        print(f"[Route: {r}]")
        print(f"Q: {str(q)[:50]}...")
        print(f"A: {str(a)[:150]}...")
        print("---")

# 실패 원인 분석
print(f"\n=== Failure Analysis ({len(fail_df)}) ===")
for idx in fail_df.index:
    question = df.iloc[idx, 4]
    error = df.iloc[idx, 15]
    print(f"Q: {str(question)[:40]}...")
    print(f"Error: {str(error)[:100]}...")
    print("---")
