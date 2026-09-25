import os
import uuid
import json
import time
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from supabase import create_client, Client
from google import genai
from google.genai import types
from dotenv import load_dotenv

# .env 파일에서 키 값들을 불러옵니다
load_dotenv()

url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")
gemini_api_key: str = os.getenv("GEMINI_API_KEY")

supabase: Client = create_client(url, key)
# 구글 최신 라이브러리(google-genai) 클라이언트 연결
client = genai.Client(api_key=gemini_api_key)

app = FastAPI()

class OnboardingRequest(BaseModel):
    salary_bucket: str
    balance_bucket: str
    fixed_expense_bucket: str

def calculate_sentence(item_price: int, user_balance: int, urgency_score: int, convictions: int) -> str:
    price_ratio = item_price / user_balance if user_balance > 0 else 1.0
    price_penalty = min(40, price_ratio * 100 * 0.6)
    urgency_penalty = (5 - urgency_score) * 4
    conviction_penalty = min(30, convictions * 10)

    score = 100 - price_penalty - urgency_penalty - conviction_penalty
    score = max(0, min(100, round(score)))

    if score >= 80: return "INNOCENT"
    elif score >= 60: return "PROBATION"
    elif score >= 40: return "FINE"
    else: return "GUILTY"

@app.get("/")
def read_root():
    return {"Hello": "장바구니 재판소 백엔드에 오신 것을 환영합니다!"}

@app.post("/users/onboarding")
def create_onboarding(payload: OnboardingRequest):
    try:
        response = (
            supabase.table("users")
            .insert({
                "salary_bucket": payload.salary_bucket,
                "balance_bucket": payload.balance_bucket,
                "fixed_expense_bucket": payload.fixed_expense_bucket,
            })
            .execute()
        )
        generated_id = response.data[0]["id"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "message": "온보딩 정보가 저장되었습니다.",
        "user_id": generated_id,
        "data": response.data,
    }

@app.post("/upload-cart")
async def upload_cart(
    image: UploadFile = File(...),
    user_id: int = Form(...),
    urgency_score: int = Form(...),
):
    image_bytes = await image.read()
    items = []
    total_price = 0

    prompt = (
        "이 장바구니/영수증 이미지에 있는 모든 상품명과 각각의 결제 금액을 추출해서 "
        "JSON 배열(Array) 형태로 응답해. "
        '예시: [{"item_name": "풍요로운삶쌀20KG", "item_price": 30900}, '
        '{"item_name": "쓰레기봉투가정용20L", "item_price": 3600}]\n'
        "다른 설명이나 마크다운 코드블록 없이 순수 JSON 배열 하나만 응답해."
    )

    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 2
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[
                    prompt,
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type=image.content_type or "image/jpeg"
                    )
                ],
                config=types.GenerateContentConfig(
                    # 타임아웃: 60초(밀리초 단위) 동안 응답이 없으면 요청을 포기
                    http_options=types.HttpOptions(timeout=60000)
                ),
            )

            response_text = response.text.strip()

            if response_text.startswith("```"):
                response_text = response_text.strip("`")
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            parsed = json.loads(response_text)
            items = [
                {"item_name": str(entry["item_name"]), "item_price": int(entry["item_price"])}
                for entry in parsed
            ]
            total_price = sum(entry["item_price"] for entry in items)
            last_error = None
            break  # 성공했으므로 재시도 루프 탈출

        except Exception as e:
            last_error = e
            error_str = str(e)
            is_retryable = (
                "503" in error_str
                or "UNAVAILABLE" in error_str
                or "overloaded" in error_str.lower()
            )

            if is_retryable and attempt < MAX_RETRIES:
                print(f"[Gemini 재시도 {attempt}/{MAX_RETRIES}] {error_str}")
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            else:
                break

    if last_error is not None:
        # 재시도 후에도 실패하거나 JSON 파싱에 실패하면 빈 목록/0원으로 처리
        print(f"Gemini Vision 분석 실패, 기본값 사용: {last_error}")
        items = []
        total_price = 0

    user_balance = 200000
    convictions = 0

    sentence_code = calculate_sentence(
        item_price=total_price,
        user_balance=user_balance,
        urgency_score=urgency_score,
        convictions=convictions,
    )

    return {
        "sentence_code": sentence_code,
        "items": items,
        "total_price": total_price,
    }