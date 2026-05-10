import streamlit as st
import pandas as pd
import altair as alt
import requests
import urllib.parse
from supabase import create_client
from typing import List
from io import BytesIO
import re
from PIL import Image
from datetime import date


# ─── Supabase client ──────────────────────────────────────────────────────────

@st.cache_resource
def get_supabase():
    return create_client(
        st.secrets["supabase"]["url"],
        st.secrets["supabase"]["service_key"],
    )


# ─── Google OAuth ─────────────────────────────────────────────────────────────

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USER_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def google_auth_url() -> str:
    params = {
        "client_id": st.secrets["google"]["client_id"],
        "redirect_uri": st.secrets["google"]["redirect_uri"],
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{_GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def google_exchange_code(code: str) -> str:
    resp = requests.post(
        _GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": st.secrets["google"]["client_id"],
            "client_secret": st.secrets["google"]["client_secret"],
            "redirect_uri": st.secrets["google"]["redirect_uri"],
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def google_get_user(token: str) -> dict:
    resp = requests.get(
        _GOOGLE_USER_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "id": data["id"],
        "name": data.get("name", "사용자"),
        "email": data.get("email", ""),
        "picture": data.get("picture", ""),
    }


# ─── Supabase data helpers ────────────────────────────────────────────────────

TX_COLS = ["날짜", "구분", "카테고리", "금액", "메모"]
RC_COLS = ["가맹점", "추정금액", "카테고리", "OCR원문"]
TX_RENAME = {"tx_date": "날짜", "tx_type": "구분", "category": "카테고리", "amount": "금액", "memo": "메모"}
RC_RENAME = {"merchant": "가맹점", "estimated_amount": "추정금액", "category": "카테고리", "ocr_text": "OCR원문"}


def load_transactions(user_id: str) -> pd.DataFrame:
    resp = (
        get_supabase()
        .table("transactions")
        .select("tx_date,tx_type,category,amount,memo")
        .eq("user_id", user_id)
        .execute()
    )
    if not resp.data:
        return pd.DataFrame(columns=TX_COLS)
    return pd.DataFrame(resp.data).rename(columns=TX_RENAME)[TX_COLS]


def add_transaction(user_id: str, row: dict):
    get_supabase().table("transactions").insert({
        "user_id": user_id,
        "tx_date": str(row["날짜"])[:10],
        "tx_type": row["구분"],
        "category": row["카테고리"],
        "amount": int(row["금액"]),
        "memo": row["메모"],
    }).execute()


def clear_transactions(user_id: str):
    get_supabase().table("transactions").delete().eq("user_id", user_id).execute()


def load_receipts(user_id: str) -> pd.DataFrame:
    resp = (
        get_supabase()
        .table("receipt_records")
        .select("merchant,estimated_amount,category,ocr_text")
        .eq("user_id", user_id)
        .execute()
    )
    if not resp.data:
        return pd.DataFrame(columns=RC_COLS)
    return pd.DataFrame(resp.data).rename(columns=RC_RENAME)[RC_COLS]


def add_receipt(user_id: str, row: dict):
    get_supabase().table("receipt_records").insert({
        "user_id": user_id,
        "merchant": row["가맹점"],
        "estimated_amount": int(row["추정금액"]),
        "category": row["카테고리"],
        "ocr_text": row["OCR원문"][:4000],
    }).execute()


# ─── OCR helpers ──────────────────────────────────────────────────────────────

def extract_receipt_text(image_file) -> str:
    try:
        import pytesseract
    except ImportError:
        raise RuntimeError("pytesseract_not_installed")
    if hasattr(image_file, "seek"):
        image_file.seek(0)
    image_bytes = image_file.read()
    image = Image.open(BytesIO(image_bytes)).convert("L")
    return pytesseract.image_to_string(image, lang="kor+eng").strip()


def guess_category(text: str) -> str:
    normalized = text.lower()
    rules = {
        "식비": ["스타벅스", "커피", "카페", "음식", "배달", "치킨", "식당", "편의점"],
        "교통": ["버스", "택시", "지하철", "교통", "주유", "주차"],
        "생활": ["다이소", "쿠팡", "마트", "올리브영", "생활", "세제"],
        "구독": ["넷플릭스", "유튜브", "멜론", "구독", "정기결제"],
    }
    for cat, keywords in rules.items():
        if any(kw.lower() in normalized for kw in keywords):
            return cat
    return "기타"


def parse_receipt_info(text: str):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    merchant = lines[0] if lines else "알 수 없음"
    amounts = []
    for m in re.findall(r"\d[\d,]{2,}", text):
        try:
            amounts.append(int(m.replace(",", "")))
        except ValueError:
            continue
    return merchant, max(amounts) if amounts else 0, guess_category(text)


def parse_receipt_items(text: str) -> pd.DataFrame:
    items = []
    for line in [l.strip() for l in text.splitlines() if l.strip()]:
        if len(line) < 4 or any(t in line for t in ["합계", "총액", "부가세", "카드", "현금", "vat", "TOTAL"]):
            continue
        m = re.search(r"(.+?)\s+(\d[\d,]{1,})$", line)
        if not m or len(m.group(1).strip()) < 2:
            continue
        try:
            amount = int(m.group(2).replace(",", ""))
        except ValueError:
            continue
        if amount > 0:
            items.append({"품목": m.group(1).strip(), "금액": amount})
    return pd.DataFrame(items) if items else pd.DataFrame(columns=["품목", "금액"])


# ─── Chatbot ──────────────────────────────────────────────────────────────────

def local_finance_chatbot(user_input: str, total_income: int, total_expense: int, total_balance: int) -> str:
    text = user_input.strip().lower()
    tips: List[str] = []

    if total_income == 0 and total_expense == 0:
        return (
            "아직 소비 데이터가 없어요. 먼저 수입/지출을 입력해주세요.\n\n"
            "- 시작 팁: 고정비(월세/통신비)와 변동비(식비/배달비)를 나눠서 기록하면 관리가 쉬워요."
        )

    if "분석" in text or "상태" in text or "어때" in text:
        if total_balance < 0:
            return (
                f"현재는 적자 상태예요. (잔액 {total_balance:,}원)\n\n"
                "- 1순위: 식비/배달비 상한을 정해서 즉시 지출을 줄여보세요.\n"
                "- 2순위: 구독/자동결제 항목을 점검해서 불필요한 항목을 해지하세요."
            )
        if total_balance < max(10000, int(total_income * 0.1)):
            return (
                f"현재 잔액은 {total_balance:,}원으로 여유가 크지 않아요.\n\n"
                "- 다음 지출 전 '필수/선택' 체크를 하고 결제하세요.\n"
                "- 이번 주 예산을 미리 정하면 과소비를 줄일 수 있어요."
            )
        return (
            f"좋아요. 현재 잔액은 {total_balance:,}원으로 비교적 안정적이에요.\n\n"
            "- 남는 금액의 일부를 비상금으로 따로 분리해보세요.\n"
            "- 다음 달 목표 저축액을 정하면 소비가 더 안정됩니다."
        )

    if "절약" in text or "아껴" in text or "돈" in text:
        tips.extend([
            "배달/카페는 주간 횟수 제한을 두면 체감 절약이 큽니다.",
            "장보기 전 목록을 고정하면 충동구매를 줄일 수 있어요.",
            "소액 결제도 하루 합계를 확인하면 지출 통제가 쉬워집니다.",
        ])

    if "식비" in text or "밥" in text:
        tips.extend([
            "식비는 '외식/배달/장보기' 3개로 나눠 관리해보세요.",
            "주 1회 밀프렙(미리 식사 준비)만 해도 배달비를 크게 줄일 수 있어요.",
        ])

    if "고정비" in text or "구독" in text or "통신비" in text:
        tips.extend([
            "고정비는 한 달 1회 점검 루틴을 만들어 자동결제를 정리해보세요.",
            "통신/구독은 사용 빈도 기준으로 유지 여부를 결정하면 좋아요.",
        ])

    if not tips:
        tips = [
            f"현재 요약: 수입 {total_income:,}원 / 지출 {total_expense:,}원 / 잔액 {total_balance:,}원",
            "질문 예시: '내 소비 상태 분석해줘', '식비 줄이는 방법 알려줘', '고정비 절약 팁 줘'",
        ]

    return "\n".join(f"- {tip}" for tip in tips)


def mobile_table(df: pd.DataFrame) -> None:
    html = df.to_html(index=False, border=0, escape=True, classes="m-tbl")
    st.markdown(f'<div class="m-tbl-wrap">{html}</div>', unsafe_allow_html=True)


# ─── Page config & CSS ────────────────────────────────────────────────────────

st.set_page_config(page_title="자취생 소비 관리", page_icon="💸", layout="wide")
st.write("✅ 새 버전 실행 중")

st.markdown(
    """
    <style>
        .block-container { padding-top: 2rem; padding-bottom: 2rem; max-width: 1180px; }
        .hero-card {
            background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
            color: white; border-radius: 18px; padding: 20px 24px; margin-bottom: 14px;
        }
        .section-card {
            background: #ffffff; border: 1px solid #e5e7eb;
            border-radius: 14px; padding: 14px 16px; margin-bottom: 14px;
        }
        .small-muted { font-size: 0.9rem; color: #6b7280; margin-top: -4px; }
        .m-tbl-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; width: 100%; }
        .m-tbl { border-collapse: collapse; font-size: .88em; min-width: 360px; width: 100%; }
        .m-tbl th { background: #f8fafc; padding: 6px 10px; border-bottom: 2px solid #e2e8f0; white-space: nowrap; text-align: left; }
        .m-tbl td { padding: 5px 10px; border-bottom: 1px solid #f1f5f9; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─── Auth ─────────────────────────────────────────────────────────────────────

if "user" not in st.session_state:
    st.session_state.user = None

params = st.query_params

# Google OAuth callback: exchange code → user info
if "code" in params and st.session_state.user is None:
    with st.spinner("로그인 중..."):
        try:
            token = google_exchange_code(params["code"])
            user = google_get_user(token)
            st.session_state.user = user
            st.session_state.transaction_records = load_transactions(user["id"])
            st.session_state.receipt_records = load_receipts(user["id"])
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"로그인에 실패했어요. 다시 시도해주세요. ({e})")
            st.query_params.clear()

elif "error" in params:
    st.warning("구글 로그인이 취소됐어요.")
    st.query_params.clear()

# 로그인 화면
if st.session_state.user is None:
    st.markdown(
        """
        <div style="max-width:420px;margin:80px auto;text-align:center">
            <h1>💸 자취생 소비 관리 AI</h1>
            <p style="color:#6b7280;margin-bottom:32px">
                구글 계정으로 로그인하면<br>어디서든 내 데이터를 유지할 수 있어요.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        auth_url = google_auth_url()
        st.markdown(
            f"""
            <a href="{auth_url}" target="_self" style="
                display:flex;align-items:center;justify-content:center;gap:10px;
                background:#fff;color:#3c4043;border:1px solid #dadce0;
                padding:12px 0;border-radius:10px;font-size:15px;
                font-weight:500;text-decoration:none;cursor:pointer;">
                <img src="https://www.google.com/favicon.ico" width="20">
                Google 계정으로 로그인
            </a>
            """,
            unsafe_allow_html=True,
        )
    st.stop()

# ─── Main app (로그인 후) ──────────────────────────────────────────────────────

user_id: str = st.session_state.user["id"]
user_name: str = st.session_state.user["name"]

if "transaction_records" not in st.session_state:
    st.session_state.transaction_records = load_transactions(user_id)
if "receipt_records" not in st.session_state:
    st.session_state.receipt_records = load_receipts(user_id)
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "last_ocr_result" not in st.session_state:
    st.session_state.last_ocr_result = None
if "last_df" not in st.session_state:
    st.session_state.last_df = pd.DataFrame(columns=["수입", "지출", "잔액"])

st.markdown(
    f"""
    <div class="hero-card">
        <h2 style="margin:0;">💸 자취생 소비 관리 AI</h2>
        <p style="margin:6px 0 0;opacity:0.95">
            안녕하세요, <b>{user_name}</b>님! 예산 상태를 빠르게 확인하고, 소비 상담 챗봇으로 맞춤 조언까지 받아보세요.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("설정")
    st.caption(f"👤 {user_name}  |  {st.session_state.user['email']}")
    st.markdown("### 월 예산 설정")
    budget_food = st.number_input("식비 예산", min_value=0, step=10000, value=300000)
    budget_transport = st.number_input("교통 예산", min_value=0, step=10000, value=100000)
    budget_life = st.number_input("생활 예산", min_value=0, step=10000, value=150000)
    budget_sub = st.number_input("구독 예산", min_value=0, step=10000, value=50000)
    save_goal = st.number_input("월 저축 목표", min_value=0, step=50000, value=300000)

    open_chat = st.toggle("💬 챗봇 열기", value=True)
    if st.button("대화 초기화", use_container_width=True):
        st.session_state.chat_history = []
        st.success("채팅 기록을 초기화했어요.")
    if st.button("입출금 내역 초기화", use_container_width=True):
        clear_transactions(user_id)
        st.session_state.transaction_records = pd.DataFrame(columns=TX_COLS)
        st.success("입출금 내역을 초기화했어요.")

    st.markdown("---")
    if st.button("로그아웃", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

left_col, right_col = st.columns([1.2, 1], gap="large")

with left_col:
    transactions = st.session_state.transaction_records.copy()
    if not transactions.empty:
        transactions["금액"] = pd.to_numeric(transactions["금액"], errors="coerce").fillna(0).astype(int)
    total_income = int(transactions[transactions["구분"] == "수입"]["금액"].sum()) if not transactions.empty else 0
    total_expense = int(transactions[transactions["구분"] == "지출"]["금액"].sum()) if not transactions.empty else 0
    total_balance = total_income - total_expense

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("📅 날짜별 입출금 내역")
    st.markdown(
        '<p class="small-muted">날짜 기준으로 수입/지출 흐름을 기록하고 확인할 수 있어요.</p>',
        unsafe_allow_html=True,
    )

    t1, t2 = st.columns(2)
    with t1:
        tx_date = st.date_input("거래 날짜", value=date.today())
        tx_type = st.selectbox("구분", options=["수입", "지출"])
    with t2:
        tx_category = st.selectbox(
            "카테고리",
            options=["식비", "교통", "생활", "구독", "월급", "용돈", "기타"],
        )
        tx_amount = st.number_input("금액 (원)", min_value=0, step=1000, format="%d", key="tx_amount")
    tx_memo = st.text_input("메모(선택)", placeholder="예: 점심, 카페, 알바비")

    if st.button("내역 추가", use_container_width=True):
        if tx_amount <= 0:
            st.warning("금액은 1원 이상 입력해주세요.")
        else:
            new_row = {
                "날짜": pd.to_datetime(tx_date),
                "구분": tx_type,
                "카테고리": tx_category,
                "금액": int(tx_amount),
                "메모": tx_memo.strip(),
            }
            add_transaction(user_id, new_row)
            st.session_state.transaction_records = pd.concat(
                [st.session_state.transaction_records, pd.DataFrame([new_row])],
                ignore_index=True,
            )
            st.success("입출금 내역이 추가됐어요.")

    tx_df = st.session_state.transaction_records.copy()
    if not tx_df.empty:
        tx_df["날짜"] = pd.to_datetime(tx_df["날짜"])
        tx_df["금액"] = pd.to_numeric(tx_df["금액"], errors="coerce").fillna(0).astype(int)
        daily = (
            tx_df.assign(
                수입금액=tx_df.apply(lambda r: r["금액"] if r["구분"] == "수입" else 0, axis=1),
                지출금액=tx_df.apply(lambda r: r["금액"] if r["구분"] == "지출" else 0, axis=1),
            )
            .groupby(tx_df["날짜"].dt.date)[["수입금액", "지출금액"]]
            .sum()
            .reset_index()
            .rename(columns={"날짜": "일자"})
        )
        daily["순변동"] = daily["수입금액"] - daily["지출금액"]
        daily["일자표시"] = pd.to_datetime(daily["일자"]).map(lambda d: f"{d.month}/{d.day}")

        st.write("### 일자별 요약")
        mobile_table(daily[["일자", "수입금액", "지출금액", "순변동"]])
        daily_chart = (
            alt.Chart(daily[["일자표시", "지출금액"]].copy())
            .mark_line(point=True)
            .encode(
                x=alt.X("일자표시:N", sort=list(daily["일자표시"]), axis=alt.Axis(labelAngle=0, title="일자")),
                y=alt.Y("지출금액:Q", title="지출 금액"),
                tooltip=["일자표시:N", "지출금액:Q"],
            )
        )
        st.altair_chart(daily_chart, use_container_width=True)

        tx_view = tx_df.sort_values("날짜", ascending=False).copy()
        tx_view["날짜"] = tx_view["날짜"].dt.strftime("%Y-%m-%d")
        st.write("### 거래 내역")
        mobile_table(tx_view)

        st.write("### 반복지출 감지")
        expense_df = tx_df[tx_df["구분"] == "지출"].copy()
        repeat = (
            expense_df.groupby(["카테고리"], dropna=False)
            .size()
            .reset_index(name="횟수")
            .sort_values("횟수", ascending=False)
        )
        repeat = repeat[repeat["횟수"] >= 3]
        if repeat.empty:
            st.info("반복지출 후보가 아직 없어요. 같은 카테고리 지출이 3회 이상 쌓이면 표시됩니다.")
        else:
            mobile_table(repeat.head(10))
    else:
        st.info("아직 등록된 입출금 내역이 없어요. 위에서 내역을 추가해보세요.")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("🎯 목표/예산 상태")
    b1, b2, b3 = st.columns(3)
    b1.metric("총 수입", f"{total_income:,}원")
    b2.metric("총 지출", f"{total_expense:,}원")
    b3.metric("총 잔액", f"{total_balance:,}원", delta=f"{total_balance:,}원")

    goal_progress = (total_balance / save_goal * 100) if save_goal > 0 else 0
    st.metric("월 저축 목표 달성률", f"{goal_progress:.1f}%")
    if save_goal > 0:
        if total_balance >= save_goal:
            st.success("목표 저축액을 달성했어요.")
        else:
            st.info(f"목표까지 {save_goal - total_balance:,}원 남았어요.")

    budgets = {"식비": budget_food, "교통": budget_transport, "생활": budget_life, "구독": budget_sub}
    tx_budget = st.session_state.transaction_records.copy()
    if not tx_budget.empty:
        tx_budget["금액"] = pd.to_numeric(tx_budget["금액"], errors="coerce").fillna(0).astype(int)
        tx_budget = tx_budget[tx_budget["구분"] == "지출"]
        budget_rows = []
        for cat, budget in budgets.items():
            used = int(tx_budget[tx_budget["카테고리"] == cat]["금액"].sum())
            ratio = (used / budget * 100) if budget > 0 else 0
            budget_rows.append({"카테고리": cat, "예산": budget, "사용": used, "사용률(%)": round(ratio, 1)})
        budget_df = pd.DataFrame(budget_rows)
        mobile_table(budget_df)
        for _, row in budget_df.iterrows():
            if row["사용률(%)"] >= 100:
                st.error(f"{row['카테고리']} 예산 초과: {int(row['사용']):,}원 / {int(row['예산']):,}원")
            elif row["사용률(%)"] >= 80:
                st.warning(f"{row['카테고리']} 예산 80% 이상 사용")
    else:
        st.info("예산 사용률을 보려면 거래 내역을 먼저 추가해주세요.")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("📷 영수증 OCR 분석")
    st.markdown(
        '<p class="small-muted">영수증 사진을 올리면 텍스트/금액/카테고리를 추정합니다.</p>',
        unsafe_allow_html=True,
    )
    receipt_file = st.file_uploader(
        "영수증 이미지 업로드",
        type=["png", "jpg", "jpeg", "webp"],
        key="receipt_uploader",
    )

    if receipt_file is not None:
        st.image(receipt_file, caption="업로드된 영수증", use_container_width=True)
        if st.button("OCR 분석 실행", use_container_width=True):
            try:
                ocr_text = extract_receipt_text(receipt_file)
                merchant, total_amount, category = parse_receipt_info(ocr_text)
                item_df = parse_receipt_items(ocr_text)

                new_rc = {
                    "가맹점": merchant,
                    "추정금액": total_amount,
                    "카테고리": category,
                    "OCR원문": ocr_text[:4000],
                }
                add_receipt(user_id, new_rc)
                st.session_state.receipt_records = pd.concat(
                    [st.session_state.receipt_records, pd.DataFrame([new_rc])],
                    ignore_index=True,
                )
                st.session_state.last_ocr_result = {"가맹점": merchant, "추정금액": int(total_amount), "카테고리": category}

                st.success("OCR 분석을 완료했어요.")
                c1, c2, c3 = st.columns(3)
                c1.metric("가맹점", merchant)
                c2.metric("추정금액", f"{total_amount:,}원")
                c3.metric("카테고리", category)
                st.text_area("추출 텍스트", ocr_text, height=180)
                if item_df.empty:
                    st.info("품목 라인 자동 인식이 어려웠어요.")
                else:
                    st.write("### 인식된 소비 품목")
                    mobile_table(item_df)

                if total_amount > 0 and st.button("이 영수증을 지출 내역에 추가", use_container_width=True):
                    ocr_row = {
                        "날짜": pd.to_datetime(date.today()),
                        "구분": "지출",
                        "카테고리": category,
                        "금액": int(total_amount),
                        "메모": f"OCR:{merchant}",
                    }
                    add_transaction(user_id, ocr_row)
                    st.session_state.transaction_records = pd.concat(
                        [st.session_state.transaction_records, pd.DataFrame([ocr_row])],
                        ignore_index=True,
                    )
                    st.success("영수증 지출이 거래 내역에 추가됐어요.")
            except RuntimeError as err:
                if str(err) == "pytesseract_not_installed":
                    st.error("pytesseract가 설치되지 않았어요. `pip install pytesseract pillow` 후 재시도해주세요.")
                    st.info("윈도우에 Tesseract OCR도 설치 필요: https://github.com/UB-Mannheim/tesseract/wiki")
                else:
                    st.error("OCR 처리 중 오류가 발생했어요.")
            except Exception:
                st.error("OCR 처리 중 오류가 발생했어요.")

    if not st.session_state.receipt_records.empty:
        st.write("### 영수증 분석 기록")
        mobile_table(st.session_state.receipt_records[["가맹점", "추정금액", "카테고리"]])
    st.markdown("</div>", unsafe_allow_html=True)

with right_col:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("🤖 소비 상담 챗봇")
    st.markdown('<p class="small-muted">소비 고민을 입력하면 AI가 현실적인 절약 팁을 제안해요.</p>', unsafe_allow_html=True)

    if not open_chat:
        st.info("사이드바에서 '챗봇 열기'를 켜면 사용할 수 있어요.")
    else:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        user_input = st.chat_input("메시지를 입력하세요")
        if user_input:
            st.session_state.chat_history.append({"role": "user", "content": user_input})
            with st.chat_message("user"):
                st.write(user_input)

            tx_for_chat = st.session_state.transaction_records.copy()
            if not tx_for_chat.empty:
                tx_for_chat["금액"] = pd.to_numeric(tx_for_chat["금액"], errors="coerce").fillna(0).astype(int)
            chat_income = int(tx_for_chat[tx_for_chat["구분"] == "수입"]["금액"].sum()) if not tx_for_chat.empty else 0
            chat_expense = int(tx_for_chat[tx_for_chat["구분"] == "지출"]["금액"].sum()) if not tx_for_chat.empty else 0

            answer = local_finance_chatbot(
                user_input=user_input,
                total_income=chat_income,
                total_expense=chat_expense,
                total_balance=chat_income - chat_expense,
            )
            st.session_state.chat_history.append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.write(answer)
    st.markdown("</div>", unsafe_allow_html=True)
