# 장바구니 재판소 백엔드 (Cart Tribunal Backend)

내 장바구니를 법정에 세우는 AI 재판 서비스 백엔드 API 서버입니다.

## 기술 스택

- FastAPI
- Uvicorn
- Supabase
- Google Gemini (google-genai)
- python-dotenv

## 환경 변수

프로젝트 루트에 `.env` 파일을 만들고 아래 변수들을 채워주세요.

```env
SUPABASE_URL=
SUPABASE_KEY=
GEMINI_API_KEY=
```

## 설치

```bash
pip install -r requirements.txt
```

## 로컬 서버 실행

```bash
py -m uvicorn main:app --reload
```

서버가 실행되면 `http://127.0.0.1:8000/docs`에서 Swagger UI로 API를 테스트할 수 있습니다.

## API

- `GET /` — 서버 상태 확인
- `POST /users/onboarding` — 온보딩 정보(급여/잔액/고정지출 구간) 저장
- `POST /upload-cart` — 장바구니/영수증 이미지 업로드 → 상품 분석 및 판결
