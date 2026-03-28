import os
import re
import sqlite3
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

try:
    import fitz  # pymupdf
except Exception:
    fitz = None


APP_TITLE = "BoConcept Ops App"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
INCOMING_DIR = DATA_DIR / "incoming"
DB_PATH = DATA_DIR / "ops_app.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
INCOMING_DIR.mkdir(parents=True, exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT UNIQUE,
                customer_name TEXT,
                customer_email TEXT,
                phone TEXT,
                sales_order TEXT,
                order_date TEXT,
                total_amount REAL,
                prepayment REAL,
                balance_due REAL,
                payment_link TEXT,
                envelope_id TEXT,
                status TEXT DEFAULT 'Ready',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sms_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT,
                phone TEXT,
                sales_order TEXT,
                total_amount REAL,
                balance_due REAL,
                message TEXT,
                status TEXT DEFAULT 'Ready',
                sms_message_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def upsert_order(order: dict):
    cols = [
        "source_file",
        "customer_name",
        "customer_email",
        "phone",
        "sales_order",
        "order_date",
        "total_amount",
        "prepayment",
        "balance_due",
        "payment_link",
        "envelope_id",
        "status",
    ]
    vals = [order.get(c) for c in cols]
    placeholders = ",".join(["?"] * len(cols))
    updates = ",".join([f"{c}=excluded.{c}" for c in cols[1:]]) + ", updated_at=CURRENT_TIMESTAMP"

    with get_conn() as conn:
        conn.execute(
            f"""
            INSERT INTO orders ({",".join(cols)})
            VALUES ({placeholders})
            ON CONFLICT(source_file) DO UPDATE SET {updates}
            """,
            vals,
        )


def get_orders():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY updated_at DESC, id DESC").fetchall()
        return [dict(r) for r in rows]


def update_order(source_file: str, **fields):
    if not fields:
        return
    parts = [f"{k}=?" for k in fields.keys()]
    sql = f"UPDATE orders SET {', '.join(parts)}, updated_at=CURRENT_TIMESTAMP WHERE source_file=?"
    params = list(fields.values()) + [source_file]
    with get_conn() as conn:
        conn.execute(sql, params)


def insert_sms_job(job: dict):
    cols = [
        "customer_name",
        "phone",
        "sales_order",
        "total_amount",
        "balance_due",
        "message",
        "status",
        "sms_message_id",
    ]
    vals = [job.get(c) for c in cols]
    placeholders = ",".join(["?"] * len(cols))
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO sms_jobs ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )


def get_sms_jobs():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM sms_jobs ORDER BY updated_at DESC, id DESC").fetchall()
        return [dict(r) for r in rows]


def update_sms_job(job_id: int, **fields):
    if not fields:
        return
    parts = [f"{k}=?" for k in fields.keys()]
    sql = f"UPDATE sms_jobs SET {', '.join(parts)}, updated_at=CURRENT_TIMESTAMP WHERE id=?"
    params = list(fields.values()) + [job_id]
    with get_conn() as conn:
        conn.execute(sql, params)


def parse_money(text: str):
    if text is None:
        return None

    text = str(text).strip()
    if not text:
        return None

    text = text.replace("$", "").replace("kr", "").replace("DKK", "").strip()
    text = re.sub(r"[^\d,.\-]", "", text)

    if not text:
        return None

    if "," in text:
        text = text.replace(".", "")
        text = text.replace(",", ".")
    else:
        if text.count(".") > 1:
            text = text.replace(".", "")

    try:
        return float(text)
    except ValueError:
        return None


def clean_text(value: str):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def find_value(pattern: str, text: str, group: int = 1):
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(group).strip() if m else ""


def extract_amount_after_label(label: str, text: str):
    pattern = rf"{re.escape(label)}\s*([\d]{{1,3}}(?:\.[\d]{{3}})*(?:,\d{{2}})|[\d]+,\d{{2}}|[\d]+(?:\.\d{{2}})?)"
    return find_value(pattern, text)


def extract_totals_block(text: str):
    money_pattern = r"[\d]{1,3}(?:\.[\d]{3})*(?:,\d{2})|[\d]+,\d{2}|[\d]+(?:\.\d{2})?"

    total_raw = extract_amount_after_label("Total", text)
    prepayment_raw = extract_amount_after_label("Prepayment", text)
    balance_raw = extract_amount_after_label("Balance due", text)

    if total_raw and prepayment_raw and balance_raw:
        return total_raw, prepayment_raw, balance_raw

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    total_candidates = []
    prepayment_candidates = []
    balance_candidates = []

    for line in lines:
        if re.search(r"\bTotal\b", line, re.IGNORECASE):
            total_candidates.extend(re.findall(money_pattern, line))
        if re.search(r"\bPrepayment\b", line, re.IGNORECASE):
            prepayment_candidates.extend(re.findall(money_pattern, line))
        if re.search(r"\bBalance\s+due\b", line, re.IGNORECASE):
            balance_candidates.extend(re.findall(money_pattern, line))

    if not total_raw and total_candidates:
        total_raw = total_candidates[-1]
    if not prepayment_raw and prepayment_candidates:
        prepayment_raw = prepayment_candidates[-1]
    if not balance_raw and balance_candidates:
        balance_raw = balance_candidates[-1]

    if total_raw and prepayment_raw and not balance_raw:
        total_num = parse_money(total_raw)
        prepay_num = parse_money(prepayment_raw)
        if total_num is not None and prepay_num is not None:
            balance_raw = f"{(total_num - prepay_num):.2f}"

    return total_raw, prepayment_raw, balance_raw


def parse_sales_order_pdf(pdf_path: Path):
    default_result = {
        "source_file": str(pdf_path),
        "customer_name": "",
        "customer_email": "",
        "phone": "",
        "sales_order": "",
        "order_date": "",
        "total_amount": None,
        "prepayment": None,
        "balance_due": None,
        "payment_link": "",
        "envelope_id": "",
        "status": "Ready",
    }

    if fitz is None:
        return default_result

    try:
        doc = fitz.open(pdf_path)
        text = "\n".join(page.get_text("text") for page in doc)
        first_page = doc[0].get_text("text") if doc.page_count else ""
    except Exception:
        return default_result

    lines = [clean_text(x) for x in first_page.splitlines() if clean_text(x)]

    customer_name = ""
    for line in lines[:12]:
        if not re.search(r"sales order|date|phone|email|misc\. charges|gst|total|prepayment|balance due", line, re.IGNORECASE):
            customer_name = line
            break

    total_raw, prepayment_raw, balance_raw = extract_totals_block(text)

    total_amount = parse_money(total_raw)
    prepayment = parse_money(prepayment_raw)
    balance_due = parse_money(balance_raw)

    if balance_due is None and total_amount is not None and prepayment is not None:
        balance_due = total_amount - prepayment

    return {
        "source_file": str(pdf_path),
        "customer_name": customer_name,
        "customer_email": find_value(r"(?:E-?mail|Email)\s*:?\s*([^\s]+@[^\s]+)", text),
        "phone": find_value(r"(?:Mobile phone|Mobile|Phone|Telephone)\s*:?\s*([+\d][\d\s]+)", text),
        "sales_order": find_value(r"Sales order\s*:?\s*([A-Za-z0-9\-\/]+)", text),
        "order_date": find_value(r"Date\s*:?\s*([\d]{1,2}[\/\-.][\d]{1,2}[\/\-.][\d]{2,4})", text),
        "total_amount": total_amount,
        "prepayment": prepayment,
        "balance_due": balance_due,
        "payment_link": "",
        "envelope_id": "",
        "status": "Ready",
    }


def create_fake_payment_link(order_no: str, amount: float):
    safe_order = order_no or "order"
    amount = float(amount or 0)
    return f"https://pay.example.com/{safe_order}?amount={amount:.2f}"


def create_fake_envelope_id():
    return f"ENV-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def create_fake_sms_id():
    return f"SMS-{datetime.now().strftime('%Y%m%d%H%M%S')}"


init_db()
st.set_page_config(page_title=APP_TITLE, layout="wide")

st.title(APP_TITLE)

tab1, tab2, tab3, tab4 = st.tabs(
    ["Pending Orders", "Pending Quotes", "Ready SMS", "History"]
)

with tab1:
    st.subheader("Pending Orders")

    uploaded_pdf = st.file_uploader("Upload sales order PDF", type=["pdf"], key="orders_pdf")
    if uploaded_pdf is not None:
        save_path = INCOMING_DIR / uploaded_pdf.name
        with open(save_path, "wb") as f:
            f.write(uploaded_pdf.getbuffer())

        order = parse_sales_order_pdf(save_path)
        upsert_order(order)
        st.success(f"Loaded {uploaded_pdf.name}")

        st.write("Detected values")
        col1, col2, col3 = st.columns(3)
        col1.metric("Total", f"{order['total_amount']:,.2f}" if order["total_amount"] is not None else "-")
        col2.metric("Prepayment", f"{order['prepayment']:,.2f}" if order["prepayment"] is not None else "-")
        col3.metric("Balance due", f"{order['balance_due']:,.2f}" if order["balance_due"] is not None else "-")

    orders = get_orders()
    if orders:
        df = pd.DataFrame(orders)
        display_cols = [
            "source_file",
            "customer_name",
            "customer_email",
            "sales_order",
            "total_amount",
            "prepayment",
            "balance_due",
            "status",
        ]
        existing_cols = [c for c in display_cols if c in df.columns]
        st.dataframe(df[existing_cols], use_container_width=True)

        selected_file = st.selectbox("Select order", df["source_file"].tolist(), key="select_order")
        current = next(x for x in orders if x["source_file"] == selected_file)

        with st.form("order_form"):
            customer_name = st.text_input("Customer name", value=current.get("customer_name") or "")
            customer_email = st.text_input("Customer email", value=current.get("customer_email") or "")
            phone = st.text_input("Phone", value=current.get("phone") or "")
            sales_order = st.text_input("Sales order", value=current.get("sales_order") or "")
            order_date = st.text