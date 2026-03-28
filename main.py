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
    if not text:
        return None
    text = text.strip().replace("$", "").replace(" ", "")
    text = text.replace(",", "")
    try:
        return float(text)
    except Exception:
        return None


def find_value(pattern: str, text: str, group: int = 1):
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(group).strip() if m else ""


def parse_sales_order_pdf(pdf_path: Path):
    if fitz is None:
        return {
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

    doc = fitz.open(pdf_path)
    text = "\n".join(page.get_text("text") for page in doc)
    first_page = doc[0].get_text("text") if doc.page_count else ""
    lines = [x.strip() for x in first_page.splitlines() if x.strip()]
    customer_name = lines[0] if lines else ""

    total_raw = find_value(r"Total\s+([\d,]+\.\d{2})", text)
    prepayment_raw = find_value(r"Prepayment\s+([\d,]+\.\d{2})", text)
    balance_raw = find_value(r"Balance\s+due\s+([\d,]+\.\d{2})", text)

    return {
        "source_file": str(pdf_path),
        "customer_name": customer_name,
        "customer_email": find_value(r"E-?Mail\s+([^\s]+)", text),
        "phone": find_value(r"(?:Mobile phone|Phone)\s+([+\d\s]+)", text),
        "sales_order": find_value(r"Sales order\s+([A-Za-z0-9\-]+)", text),
        "order_date": find_value(r"Date\s+([\d/\-]+)", text),
        "total_amount": parse_money(total_raw),
        "prepayment": parse_money(prepayment_raw),
        "balance_due": parse_money(balance_raw),
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

    orders = get_orders()
    if orders:
        df = pd.DataFrame(orders)
        st.dataframe(
            df[
                [
                    "source_file",
                    "customer_name",
                    "customer_email",
                    "sales_order",
                    "total_amount",
                    "prepayment",
                    "balance_due",
                    "status",
                ]
            ],
            use_container_width=True,
        )

        selected_file = st.selectbox("Select order", df["source_file"].tolist(), key="select_order")
        current = next(x for x in orders if x["source_file"] == selected_file)

        with st.form("order_form"):
            customer_name = st.text_input("Customer name", value=current.get("customer_name") or "")
            customer_email = st.text_input("Customer email", value=current.get("customer_email") or "")
            phone = st.text_input("Phone", value=current.get("phone") or "")
            sales_order = st.text_input("Sales order", value=current.get("sales_order") or "")
            order_date = st.text_input("Order date", value=current.get("order_date") or "")
            total_amount = st.number_input("Total amount", min_value=0.0, value=float(current.get("total_amount") or 0))
            prepayment = st.number_input("Prepayment", min_value=0.0, value=float(current.get("prepayment") or 0))
            balance_due = st.number_input("Balance due", min_value=0.0, value=float(current.get("balance_due") or 0))
            generate_link = st.checkbox("Generate payment link")
            submit_to_docusign = st.form_submit_button("Save / Submit")

        if submit_to_docusign:
            payment_link = current.get("payment_link") or ""
            if generate_link and not payment_link:
                payment_link = create_fake_payment_link(sales_order, balance_due)

            envelope_id = create_fake_envelope_id()

            update_order(
                selected_file,
                customer_name=customer_name,
                customer_email=customer_email,
                phone=phone,
                sales_order=sales_order,
                order_date=order_date,
                total_amount=total_amount,
                prepayment=prepayment,
                balance_due=balance_due,
                payment_link=payment_link,
                envelope_id=envelope_id,
                status="Submitted",
            )
            st.success(f"Saved. Envelope ID: {envelope_id}")
            if payment_link:
                st.code(payment_link)

with tab2:
    st.subheader("Pending Quotes")

    uploaded_quote = st.file_uploader("Upload quote PDF", type=["pdf"], key="quotes_pdf")
    if uploaded_quote is not None:
        save_path = INCOMING_DIR / uploaded_quote.name
        with open(save_path, "wb") as f:
            f.write(uploaded_quote.getbuffer())

        order = parse_sales_order_pdf(save_path)
        order["status"] = "Quote"
        upsert_order(order)
        st.success(f"Loaded {uploaded_quote.name}")

    quote_rows = [x for x in get_orders() if x.get("status") == "Quote"]
    if quote_rows:
        st.dataframe(pd.DataFrame(quote_rows), use_container_width=True)
    else:
        st.info("No quotes loaded.")

with tab3:
    st.subheader("Ready SMS")

    uploaded_excel = st.file_uploader("Upload ready-for-delivery Excel", type=["xlsx", "xls"], key="sms_excel")
    if uploaded_excel is not None:
        excel_path = DATA_DIR / uploaded_excel.name
        with open(excel_path, "wb") as f:
            f.write(uploaded_excel.getbuffer())

        df = pd.read_excel(excel_path)
        df.columns = [str(c).strip() for c in df.columns]

        for _, row in df.iterrows():
            customer_name = str(row.get("customer_name", "")).strip()
            phone = str(row.get("phone", "")).strip()
            sales_order = str(row.get("sales_order", "")).strip()
            total_amount = float(row.get("total_amount", 0) or 0)
            balance_due = float(row.get("balance_due", 0) or 0)

            msg = (
                f"Hi {customer_name}, your BoConcept order {sales_order} is ready. "
                f"Balance payable is ${balance_due:,.2f}. Please contact the store to arrange delivery."
            )

            insert_sms_job(
                {
                    "customer_name": customer_name,
                    "phone": phone,
                    "sales_order": sales_order,
                    "total_amount": total_amount,
                    "balance_due": balance_due,
                    "message": msg,
                    "status": "Ready",
                    "sms_message_id": "",
                }
            )

        st.success("SMS jobs loaded.")

    sms_jobs = get_sms_jobs()
    if sms_jobs:
        sms_df = pd.DataFrame(sms_jobs)
        st.dataframe(
            sms_df[["id", "customer_name", "phone", "sales_order", "balance_due", "status"]],
            use_container_width=True,
        )

        selected_sms_id = st.selectbox("Select SMS job", sms_df["id"].tolist(), key="select_sms")
        sms_row = next(x for x in sms_jobs if x["id"] == selected_sms_id)

        with st.form("sms_form"):
            edited_message = st.text_area("Message", value=sms_row.get("message") or "", height=120)
            send_sms = st.form_submit_button("Mark as Sent")

        if send_sms:
            sms_id = create_fake_sms_id()
            update_sms_job(selected_sms_id, message=edited_message, sms_message_id=sms_id, status="Sent")
            st.success(f"SMS marked sent: {sms_id}")
    else:
        st.info("No SMS jobs loaded.")

with tab4:
    st.subheader("History")

    orders_df = pd.DataFrame(get_orders())
    sms_df = pd.DataFrame(get_sms_jobs())

    st.markdown("#### Orders")
    if not orders_df.empty:
        st.dataframe(orders_df, use_container_width=True)
    else:
        st.info("No order history.")

    st.markdown("#### SMS Jobs")
    if not sms_df.empty:
        st.dataframe(sms_df, use_container_width=True)
    else:
        st.info("No SMS history.")