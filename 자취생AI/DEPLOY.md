# 배포 가이드

## 1단계: Supabase 설정

1. [supabase.com](https://supabase.com) → 새 프로젝트 생성
2. 좌측 메뉴 **SQL Editor** → 아래 SQL 실행:

```sql
create table transactions (
  id bigint generated always as identity primary key,
  user_id text not null,
  tx_date date,
  tx_type text,
  category text,
  amount integer,
  memo text,
  created_at timestamptz default now()
);

create table receipt_records (
  id bigint generated always as identity primary key,
  user_id text not null,
  merchant text,
  estimated_amount integer,
  category text,
  ocr_text text,
  created_at timestamptz default now()
);
```

3. 좌측 메뉴 **Project Settings → API** 에서
   - `Project URL` → secrets.toml의 `supabase.url`
   - `service_role` 키(secret) → secrets.toml의 `supabase.service_key`

---

## 2단계: Google OAuth 설정

1. [console.cloud.google.com](https://console.cloud.google.com) 접속
2. 새 프로젝트 생성
3. **API 및 서비스 → OAuth 동의 화면** 설정
   - 사용자 유형: 외부 / 앱 이름·이메일 입력 후 저장
4. **사용자 인증 정보 → + 만들기 → OAuth 클라이언트 ID**
   - 애플리케이션 유형: **웹 애플리케이션**
   - 승인된 리디렉션 URI:
     - 로컬: `http://localhost:8501`
     - 배포 후: `https://앱이름.streamlit.app`
5. 생성된 **클라이언트 ID**, **클라이언트 보안 비밀번호** → `.streamlit/secrets.toml`에 입력

---

## 3단계: secrets.toml 작성

`.streamlit/secrets.toml` 파일을 채우세요:

```toml
[google]
client_id = "xxx.apps.googleusercontent.com"
client_secret = "GOCSPX-xxx"
redirect_uri = "http://localhost:8501"

[supabase]
url = "https://xxx.supabase.co"
service_key = "eyJxxx..."
```

> ⚠️ secrets.toml은 절대 GitHub에 올리지 마세요. .gitignore에 추가하세요.

---

## 4단계: 로컬 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## 5단계: Streamlit Cloud 배포

1. [share.streamlit.io](https://share.streamlit.io) → GitHub 저장소 연결
2. Main file: `app.py`
3. **Advanced settings → Secrets** 에 secrets.toml 내용 붙여넣기
4. Google Cloud Console에서 배포 URL도 리디렉션 URI에 추가
5. secrets.toml의 `redirect_uri`를 배포 URL로 변경

---

## 주의 사항 (OCR)

- OCR은 `pytesseract` + 로컬 Tesseract 엔진 기반입니다.
- Streamlit Cloud에서는 OCR이 동작하지 않을 수 있습니다.
- 클라우드에서 OCR을 쓰려면 Google Vision API 방식으로 전환이 필요합니다.
