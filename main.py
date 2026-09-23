from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from supabase import create_client, Client
import os
import uuid
from dotenv import load_dotenv

# .env 파일에서 키 값들을 불러옵니다
load_dotenv()

# 환경 변수 가져오기
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")

# Supabase 클라이언트 연결
supabase: Client = create_client(url, key)

app = FastAPI()


# 온보딩 요청 바디 스키마
class OnboardingRequest(BaseModel):
    salary_bucket: str
    balance_bucket: str
    fixed_expense_bucket: str


# Scoring Engine: 소비 판결 점수를 계산합니다
def calculate_sentence(
    item_price: int, user_balance: int, urgency_score: int, convictions: int
) -> str:
    # 가격 타격도: 잔액 대비 지출 비중이 클수록 감점 (최대 40점)
    price_ratio = item_price / user_balance if user_balance > 0 else 1.0
    price_penalty = min(40, price_ratio * 100 * 0.6)

    # 시급성 감점: urgency_score(1~5)가 낮을수록(안 급할수록) 감점 (최대 16점)
    urgency_penalty = (5 - urgency_score) * 4

    # 전과 감점: 전과(과거 충동구매 판결 횟수)가 많을수록 감점 (최대 30점)
    conviction_penalty = min(30, convictions * 10)

    score = 100 - price_penalty - urgency_penalty - conviction_penalty
    score = max(0, min(100, round(score)))

    if score >= 80:
        return "INNOCENT"
    elif score >= 60:
        return "PROBATION"
    elif score >= 40:
        return "FINE"
    else:
        return "GUILTY"


# 기본 라우트 (서버가 켜져있는지 확인용)
@app.get("/")
def read_root():
    return {"Hello": "장바구니 재판소 백엔드에 오신 것을 환영합니다!"}


# 온보딩 정보 저장 라우트
@app.post("/users/onboarding")
def create_onboarding(payload: OnboardingRequest):
    try:
        response = (
            supabase.table("users")
            .insert(
                {
                    "salary_bucket": payload.salary_bucket,
                    "balance_bucket": payload.balance_bucket,
                    "fixed_expense_bucket": payload.fixed_expense_bucket,
                }
            )
            .execute()
        )
        
        # Supabase가 자동으로 생성한 숫자형 id를 가져옵니다
        generated_id = response.data[0]["id"]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "message": "온보딩 정보가 저장되었습니다.",
        "user_id": generated_id,
        "data": response.data,
    }


# 기소(재판 청구) API 뼈대
@app.post("/upload-cart")
async def upload_cart(
    image: UploadFile = File(...),
    user_id: int = Form(...),
    urgency_score: int = Form(...),
):
    # TODO: Vision API로 상품명, 가격 추출
    item_name = "테스트 상품"
    item_price = 50000  # TODO: DB에서 조회할 값
    user_balance = 200000  # TODO: DB에서 조회할 값
    convictions = 0  # TODO: DB에서 조회할 값 (사용자 전과 횟수)

    sentence_code = calculate_sentence(
        item_price=item_price,
        user_balance=user_balance,
        urgency_score=urgency_score,
        convictions=convictions,
    )

    return {
        "sentence_code": sentence_code,
        "item_name": item_name,
        "item_price": item_price,
    }