import os
import uuid
import json
import time
import asyncio
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

# LLM Orchestration Layer(검사/변호인/판사)에서 사용하는 모델
JUDGE_MODEL = "gemini-3.8-flash"

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


def format_items_for_prompt(items: list) -> str:
    if not items:
        return "(추출된 품목 없음)"
    return "\n".join(f"- {i['item_name']}: {i['item_price']}원" for i in items)


async def call_gemini_text(prompt: str) -> str:
    """텍스트 프롬프트로 Gemini를 비동기 호출합니다 (503 등 일시적 오류는 최대 3회 재시도)."""
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 2
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.aio.models.generate_content(
                model=JUDGE_MODEL,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    http_options=types.HttpOptions(timeout=60000)
                ),
            )
            return response.text.strip()
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
                await asyncio.sleep(RETRY_DELAY_SECONDS)
                continue
            break

    raise last_error


# 검사 Agent: 기소/논고
async def run_prosecutor_agent(
    items: list, total_price: int, user_balance: int, urgency_score: int, convictions: int
) -> str:
    prompt = f"""당신은 '장바구니 재판소'의 검사입니다. 피고인(사용자)의 소비 내역을 근거로 기소 논고문을 작성하세요.

[장바구니 품목]
{format_items_for_prompt(items)}

[총 결제 금액] {total_price}원
[사용자 잔액] {user_balance}원
[긴급도 점수(1~5, 낮을수록 안 급한 소비)] {urgency_score}
[전과 횟수(과거 충동구매 판결 횟수)] {convictions}

[지침]
- 톤앤매너: 냉정하고 팩트 위주. 숫자를 집요하게 파고들며 유죄를 강하게 주장할 것.
- 잔액 대비 지출 비중, 낮은 긴급도, 전과 이력 등을 구체적인 논거로 삼을 것.
- 다른 설명이나 머리말 없이, 2~4문장 분량의 논고문 본문만 응답할 것."""

    try:
        return await call_gemini_text(prompt)
    except Exception as e:
        return f"(검사 논고 생성 실패: {e})"


# 변호인 Agent: 변론
async def run_defense_agent(
    items: list, total_price: int, urgency_score: int, user_excuse: str
) -> str:
    prompt = f"""당신은 '장바구니 재판소'의 변호인입니다. 피고인(사용자)을 위한 변론문을 작성하세요.

[장바구니 품목]
{format_items_for_prompt(items)}

[총 결제 금액] {total_price}원
[긴급도 점수(1~5, 높을수록 급한 소비)] {urgency_score}
[피고인의 변명(심문 답변)] {user_excuse or "(제출된 변명 없음)"}

[지침]
- 톤앤매너: 사용자의 입장을 철저히 옹호.
- 필수품 여부, 감가상각/일 단가 분할 논리("하루 몇백 원꼴") 등을 활용해 선처를 요청할 것.
- 다른 설명이나 머리말 없이, 2~4문장 분량의 변론문 본문만 응답할 것."""

    try:
        return await call_gemini_text(prompt)
    except Exception as e:
        return f"(변호인 변론 생성 실패: {e})"


# 판사 Agent: 최종 선고 (sentence_code는 Scoring Engine 값을 그대로 채택)
async def run_judge_agent(
    sentence_code: str, prosecutor_text: str, defense_text: str, items: list, total_price: int
) -> dict:
    prompt = f"""당신은 '장바구니 재판소'의 판사입니다. 아래 정보를 바탕으로 최종 선고를 내리세요.

[중요 규칙] sentence_code는 Scoring Engine이 이미 결정론적으로 확정한 값입니다. 절대 다른 값으로 바꾸지 말고 아래 값을 그대로 사용하세요.
[확정된 sentence_code] {sentence_code}

[장바구니 품목]
{format_items_for_prompt(items)}

[총 결제 금액] {total_price}원

[검사의 논고]
{prosecutor_text}

[변호인의 변론]
{defense_text}

[지침]
- 톤앤매너: 건조하고 엄격한 법률 문어체. 내용은 위트 있게 쓰되 어투는 진지함을 유지할 것.
- 검사와 변호인의 주장을 종합해 판결 이유를 작성할 것.
- 반드시 아래 형식 그대로, 마크다운 코드블록이나 다른 설명 없이 순수 JSON 객체 하나만 응답할 것.

{{"verdict": "선고형 요약 문구", "sentence_code": "{sentence_code}", "reasoning": "판결 이유 종합", "quote": "한 줄 판결 명언"}}"""

    fallback = {
        "verdict": "판결문 생성 실패",
        "sentence_code": sentence_code,
        "reasoning": "판사 AI 응답을 생성하거나 파싱하지 못했습니다.",
        "quote": "",
    }

    try:
        response_text = await call_gemini_text(prompt)

        if response_text.startswith("```"):
            response_text = response_text.strip("`")
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()

        parsed = json.loads(response_text)

        # 원칙 1: sentence_code는 절대 불변 — LLM 출력과 무관하게 Scoring Engine 값으로 강제 고정
        parsed["sentence_code"] = sentence_code
        return parsed

    except Exception as e:
        fallback["reasoning"] = f"판사 AI 응답을 생성하거나 파싱하지 못했습니다: {e}"
        return fallback


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
    user_excuse: str = Form(""),
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

    # 1. Scoring Engine: 결정론적 규칙으로 sentence_code를 먼저 확정 (이후 LLM은 이 값을 바꿀 수 없음)
    sentence_code = calculate_sentence(
        item_price=total_price,
        user_balance=user_balance,
        urgency_score=urgency_score,
        convictions=convictions,
    )

    # 2. 검사 / 변호인 Agent를 asyncio.gather로 병렬 호출
    prosecutor_text, defense_text = await asyncio.gather(
        run_prosecutor_agent(items, total_price, user_balance, urgency_score, convictions),
        run_defense_agent(items, total_price, urgency_score, user_excuse),
    )

    # 3. 판사 Agent: 검사/변호인 발언을 모두 받은 뒤 최종 선고 (sentence_code는 그대로 채택)
    verdict = await run_judge_agent(
        sentence_code, prosecutor_text, defense_text, items, total_price
    )

    return {
        "sentence_code": sentence_code,
        "items": items,
        "total_price": total_price,
        "prosecutor": prosecutor_text,
        "defense": defense_text,
        "verdict": verdict,
    }