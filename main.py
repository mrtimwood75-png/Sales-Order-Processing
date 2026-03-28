import re
import sqlite3
from pathlib import Path
from datetime import datetime
import os

import pandas as pd
import streamlit as st

try:
    import fitz  # pymupdf
except Exception:
    fitz = None

try:
    import stripe
except Exception:
    stripe = None


APP_TITLE = "BoConcept Ops App"

# ============================================================
# SET YOUR SHAREPOINT-SYNCED FOLDER HERE
# ============================================================
SHAREPOINT_ROOT = Path(r"C:\Users\YourName\OneDrive - Your Company\BoConcept Orders")

DATA_DIR = SHAREPOINT_ROOT
INCOMING_DIR = DATA_DIR / "incoming"
STAMPED_DIR = DATA_DIR / "stamped"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
DB_PATH = DATA_DIR / "ops_app.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
INCOMING_DIR.mkdir(parents=True, exist_ok=True)
STAMPED_DIR.mkdir(parents=True, exist_ok=True)
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://example.com/success").strip()
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "https://example.com/cancel").strip()
STRIPE_CURRENCY = os.getenv("STRIPE_CURRENCY", "aud").strip().lower()

BASE_DIR = Path(__file__).resolve().parent
LOGO_PATH = Path(os.getenv("BOCONCEPT_LOGO_PATH", str(BASE_DIR / "assets" / "boconcept_logo.png")))


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
                stamped_pdf_path TEXT,
                customer_name TEXT,
                customer_email TEXT,
                phone TEXT,
                sales_order TEXT,
                order_date TEXT,
                total_amount REAL,
                prepayment REAL,
                balance_due REAL,
                payment_mode TEXT,
                payment_amount REAL,
                payment_label TEXT,
                payment_link TEXT,
                stripe_session_id TEXT,
                envelope_id TEXT,
                status TEXT DEFAULT 'Ready',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        existing_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(orders)").fetchall()
        }

        required_additions = {
            "stamped_pdf_path": "TEXT",
            "payment_mode": "TEXT",
            "payment_amount": "REAL",
            "payment_label": "TEXT",
            "stripe_session_id": "TEXT",
        }

        for col, col_type in required_additions.items():
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {col_type}")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT,
                attachment_path TEXT,
                original_name TEXT,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
        "stamped_pdf_path",
        "customer_name",
        "customer_email",
        "phone",
        "sales_order",
        "order_date",
        "total_amount",
        "prepayment",
        "balance_due",
        "payment_mode",
        "payment_amount",
        "payment_label",
        "payment_link",
        "stripe_session_id",
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


def get_latest_order():
    orders = get_orders()
    return orders[0] if orders else None


def get_order_by_source_file(source_file: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE source_file = ?", (source_file,)).fetchone()
        return dict(row) if row else None


def update_order(source_file: str, **fields):
    if not fields:
        return
    parts = [f"{k}=?" for k in fields.keys()]
    sql = f"UPDATE orders SET {', '.join(parts)}, updated_at=CURRENT_TIMESTAMP WHERE source_file=?"
    params = list(fields.values()) + [source_file]
    with get_conn() as conn:
        conn.execute(sql, params)


def add_attachment(source_file: str, attachment_path: str, original_name: str):
    with get_conn() as conn:
        max_row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM order_attachments WHERE source_file = ?",
            (source_file,),
        ).fetchone()
        next_sort = int(max_row["max_sort"] or 0) + 1
        conn.execute(
            """
            INSERT INTO order_attachments (source_file, attachment_path, original_name, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (source_file, attachment_path, original_name, next_sort),
        )


def get_attachments(source_file: str):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM order_attachments
            WHERE source_file = ?
            ORDER BY sort_order, id
            """,
            (source_file,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_attachment(attachment_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT attachment_path FROM order_attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
        if row:
            path = Path(row["attachment_path"])
            if path.exists():
                path.unlink()
        conn.execute("DELETE FROM order_attachments WHERE id = ?", (attachment_id,))


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


def clean_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_money(text):
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


def format_money(value):
    if value is None:
        return "-"
    return f"{value:,.2f}"


def find_value(pattern, text, group=1):
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match.group(group).strip() if match else ""


def extract_amount_after_label(label, text):
    pattern = (
        rf"{re.escape(label)}\s*"
        r"([\d]{1,3}(?:\.[\d]{3})*(?:,\d{2})|[\d]+,\d{2}|[\d]+(?:\.\d{2})?)"
    )
    return find_value(pattern, text)


def extract_totals_block(text):
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
        "stamped_pdf_path": "",
        "customer_name": "",
        "customer_email": "",
        "phone": "",
        "sales_order": "",
        "order_date": "",
        "total_amount": None,
        "prepayment": None,
        "balance_due": None,
        "payment_mode": "balance",
        "payment_amount": None,
        "payment_label": "Pay Balance Now",
        "payment_link": "",
        "stripe_session_id": "",
        "envelope_id": "",
        "status": "Ready",
    }

    if fitz is None:
        return default_result

    try:
        doc = fitz.open(pdf_path)
        text = "\n".join(page.get_text("text") for page in doc)
        first_page = doc[0].get_text("text") if doc.page_count else ""
        doc.close()
    except Exception:
        return default_result

    lines = [clean_text(x) for x in first_page.splitlines() if clean_text(x)]

    customer_name = ""
    for line in lines[:12]:
        if not re.search(
            r"sales order|date|phone|email|misc\. charges|gst|total|prepayment|balance due",
            line,
            re.IGNORECASE,
        ):
            customer_name = line
            break

    total_raw, prepayment_raw, balance_raw = extract_totals_block(text)

    total_amount = parse_money(total_raw)
    prepayment = parse_money(prepayment_raw)
    balance_due = parse_money(balance_raw)

    if balance_due is None and total_amount is not None and prepayment is not None:
        balance_due = total_amount - prepayment

    default_payment_amount = balance_due if balance_due is not None else 0.0

    return {
        "source_file": str(pdf_path),
        "stamped_pdf_path": "",
        "customer_name": customer_name,
        "customer_email": find_value(r"(?:E-?mail|Email)\s*:?\s*([^\s]+@[^\s]+)", text),
        "phone": find_value(r"(?:Mobile phone|Mobile|Phone|Telephone)\s*:?\s*([+\d][\d\s]+)", text),
        "sales_order": find_value(r"Sales order\s*:?\s*([A-Za-z0-9\-\/]+)", text),
        "order_date": find_value(r"Date\s*:?\s*([\d]{1,2}[\/\-.][\d]{1,2}[\/\-.][\d]{2,4})", text),
        "total_amount": total_amount,
        "prepayment": prepayment,
        "balance_due": balance_due,
        "payment_mode": "balance",
        "payment_amount": default_payment_amount,
        "payment_label": "Pay Balance Now",
        "payment_link": "",
        "stripe_session_id": "",
        "envelope_id": "",
        "status": "Ready",
    }


def create_fake_envelope_id():
    return f"ENV-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def create_fake_sms_id():
    return f"SMS-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def ensure_stripe_ready():
    if stripe is None:
        raise RuntimeError("Stripe package not installed. Add 'stripe' to requirements.txt")
    if not STRIPE_SECRET_KEY:
        raise RuntimeError("Missing STRIPE_SECRET_KEY environment variable")
    stripe.api_key = STRIPE_SECRET_KEY


def payment_choice_to_values(choice: str, balance_due: float):
    bal = float(balance_due or 0)
    if bal <= 0:
        raise RuntimeError("Balance due must be greater than 0")

    if choice == "deposit":
        return {
            "payment_mode": "deposit",
            "payment_amount": round(bal * 0.50, 2),
            "payment_label": "Pay 50% Deposit Now",
        }

    return {
        "payment_mode": "balance",
        "payment_amount": round(bal, 2),
        "payment_label": "Pay Balance Now",
    }


def create_stripe_checkout_link(customer_name, customer_email, sales_order, amount, payment_label):
    ensure_stripe_ready()

    amount_value = float(amount or 0)
    if amount_value <= 0:
        raise RuntimeError("Payment amount must be greater than 0")

    unit_amount = int(round(amount_value * 100))
    order_ref = sales_order or "Order"

    session = stripe.checkout.Session.create(
        mode="payment",
        success_url=STRIPE_SUCCESS_URL,
        cancel_url=STRIPE_CANCEL_URL,
        customer_email=customer_email or None,
        client_reference_id=order_ref,
        payment_method_types=["card"],
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": STRIPE_CURRENCY,
                    "unit_amount": unit_amount,
                    "product_data": {
                        "name": payment_label,
                        "description": f"BoConcept order {order_ref}",
                    },
                },
            }
        ],
        metadata={
            "sales_order": order_ref,
            "customer_name": customer_name or "",
            "customer_email": customer_email or "",
            "payment_label": payment_label,
            "payment_amount": f"{amount_value:.2f}",
        },
    )

    return {
        "url": session.url,
        "session_id": session.id,
    }


def add_logo_and_optional_payment_button(source_pdf_path, output_pdf_path, logo_path, button_label=None, button_url=None):
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed. Add 'pymupdf' to requirements.txt")

    source_pdf_path = Path(source_pdf_path)
    output_pdf_path = Path(output_pdf_path)
    logo_path = Path(logo_path) if logo_path else None

    if not source_pdf_path.exists():
        raise RuntimeError(f"Source PDF not found: {source_pdf_path}")

    doc = fitz.open(source_pdf_path)

    for page in doc:
        page_width = page.rect.width

        if logo_path and logo_path.exists():
            logo_rect = fitz.Rect(24, 18, 144, 58)
            page.insert_image(logo_rect, filename=str(logo_path), keep_proportion=True, overlay=True)

        if button_label and button_url:
            button_width = 170
            button_height = 28
            x1 = page_width - 24 - button_width
            y1 = 20
            button_rect = fitz.Rect(x1, y1, x1 + button_width, y1 + button_height)

            shape = page.new_shape()
            shape.draw_rect(button_rect)
            shape.finish(color=(0.0, 0.0, 0.0), fill=(0.0, 0.0, 0.0), width=1)
            shape.commit()

            page.insert_textbox(
                button_rect,
                button_label,
                fontsize=10,
                fontname="helv",
                color=(1, 1, 1),
                align=1,
                overlay=True,
            )

            page.insert_link(
                {
                    "kind": fitz.LINK_URI,
                    "from": button_rect,
                    "uri": button_url,
                }
            )

    doc.save(output_pdf_path, garbage=4, deflate=True)
    doc.close()


def append_file_to_pdf(bundle_doc, attachment_path: Path):
    ext = attachment_path.suffix.lower()

    if ext == ".pdf":
        src = fitz.open(attachment_path)
        bundle_doc.insert_pdf(src)
        src.close()
        return

    if ext in [".png", ".jpg", ".jpeg", ".webp"]:
        img_doc = fitz.open(attachment_path)
        pdf_bytes = img_doc.convert_to_pdf()
        img_doc.close()

        img_pdf = fitz.open("pdf", pdf_bytes)
        bundle_doc.insert_pdf(img_pdf)
        img_pdf.close()
        return

    raise RuntimeError(f"Unsupported attachment type: {attachment_path.name}")


def build_bundle_pdf(source_pdf_path, output_pdf_path, logo_path, attachments, button_label=None, button_url=None):
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed. Add 'pymupdf' to requirements.txt")

    output_pdf_path = Path(output_pdf_path)
    temp_main_pdf = output_pdf_path.with_name(output_pdf_path.stem + "_main.pdf")

    add_logo_and_optional_payment_button(
        source_pdf_path=source_pdf_path,
        output_pdf_path=temp_main_pdf,
        logo_path=logo_path,
        button_label=button_label,
        button_url=button_url,
    )

    bundle = fitz.open()

    main_doc = fitz.open(temp_main_pdf)
    bundle.insert_pdf(main_doc)
    main_doc.close()

    for att in attachments:
        att_path = Path(att["attachment_path"])
        if not att_path.exists():
            raise RuntimeError(f"Attachment not found: {att_path}")
        append_file_to_pdf(bundle, att_path)

    bundle.save(output_pdf_path, garbage=4, deflate=True)
    bundle.close()

    if temp_main_pdf.exists():
        temp_main_pdf.unlink()

    return output_pdf_path


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
        st.session_state["current_order_file"] = str(save_path)
        st.success(f"Loaded {uploaded_pdf.name}")
        st.rerun()

    current = None
    if "current_order_file" in st.session_state:
        current = get_order_by_source_file(st.session_state["current_order_file"])

    if current is None:
        latest = get_latest_order()
        if latest:
            st.session_state["current_order_file"] = latest["source_file"]
            current = latest

    if current:
        selected_file = current["source_file"]

        st.markdown("### Current Order")
        st.caption(Path(selected_file).name)

        col1, col2, col3 = st.columns(3)
        col1.metric("Total", format_money(current.get("total_amount")))
        col2.metric("Prepayment", format_money(current.get("prepayment")))
        col3.metric("Balance due", format_money(current.get("balance_due")))

        current_mode = current.get("payment_mode") or "balance"
        payment_index = 0 if current_mode == "balance" else 1

        with st.form("order_form"):
            customer_name = st.text_input("Customer name", value=current.get("customer_name") or "")
            customer_email = st.text_input("Customer email", value=current.get("customer_email") or "")
            phone = st.text_input("Phone", value=current.get("phone") or "")
            sales_order = st.text_input("Sales order", value=current.get("sales_order") or "")
            order_date = st.text_input("Order date", value=current.get("order_date") or "")

            total_amount = st.number_input(
                "Total amount",
                min_value=0.0,
                value=float(current.get("total_amount") or 0),
                step=0.01,
            )
            prepayment = st.number_input(
                "Prepayment",
                min_value=0.0,
                value=float(current.get("prepayment") or 0),
                step=0.01,
            )
            balance_due = st.number_input(
                "Balance due",
                min_value=0.0,
                value=float(current.get("balance_due") or 0),
                step=0.01,
            )

            payment_choice = st.radio(
                "Payment link type",
                options=["balance", "deposit"],
                index=payment_index,
                format_func=lambda x: "Balance" if x == "balance" else "Deposit (50% of balance)",
                horizontal=True,
            )

            payment_calc = payment_choice_to_values(payment_choice, balance_due)
            st.info(f"{payment_calc['payment_label']} — Amount: {format_money(payment_calc['payment_amount'])}")

            payment_link = st.text_input("Payment link", value=current.get("payment_link") or "", disabled=True)
            stripe_session_id = st.text_input("Stripe session ID", value=current.get("stripe_session_id") or "", disabled=True)
            stamped_pdf_path = st.text_input("Bundle PDF", value=current.get("stamped_pdf_path") or "", disabled=True)
            status = st.text_input("Status", value=current.get("status") or "")

            col_save, col_link, col_submit = st.columns(3)
            save_clicked = col_save.form_submit_button("Save Changes")
            create_link_clicked = col_link.form_submit_button("Create Stripe Link")
            submit_clicked = col_submit.form_submit_button("Mark Submitted")

        if save_clicked:
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
                payment_mode=payment_calc["payment_mode"],
                payment_amount=payment_calc["payment_amount"],
                payment_label=payment_calc["payment_label"],
                status=status or current.get("status") or "Ready",
            )
            st.success("Changes saved")
            st.rerun()

        if create_link_clicked:
            try:
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
                    payment_mode=payment_calc["payment_mode"],
                    payment_amount=payment_calc["payment_amount"],
                    payment_label=payment_calc["payment_label"],
                    status=status or current.get("status") or "Ready",
                )

                link_result = create_stripe_checkout_link(
                    customer_name=customer_name,
                    customer_email=customer_email,
                    sales_order=sales_order,
                    amount=payment_calc["payment_amount"],
                    payment_label=payment_calc["payment_label"],
                )

                update_order(
                    selected_file,
                    payment_mode=payment_calc["payment_mode"],
                    payment_amount=payment_calc["payment_amount"],
                    payment_label=payment_calc["payment_label"],
                    payment_link=link_result["url"],
                    stripe_session_id=link_result["session_id"],
                    status="Payment Link Created",
                )
                st.success("Stripe payment link created")
                st.code(link_result["url"])
                st.rerun()
            except Exception as e:
                st.error(str(e))

        if submit_clicked:
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
                payment_mode=payment_calc["payment_mode"],
                payment_amount=payment_calc["payment_amount"],
                payment_label=payment_calc["payment_label"],
                status="Submitted",
                envelope_id=envelope_id,
            )
            st.success(f"Marked submitted. Envelope ID: {envelope_id}")
            st.rerun()

        st.markdown("### Additional Files to Stitch Into Final PDF")
        extra_files = st.file_uploader(
            "Upload extra PDF or image files",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key=f"attachments_{selected_file}",
        )

        col_att_a, col_att_b = st.columns([2, 1])

        if col_att_a.button("Add Files to Bundle", key=f"add_files_btn_{selected_file}"):
            if extra_files:
                order_attach_dir = ATTACHMENTS_DIR / Path(selected_file).stem
                order_attach_dir.mkdir(parents=True, exist_ok=True)

                saved_count = 0
                for up in extra_files:
                    dest = order_attach_dir / up.name
                    suffix = 1
                    while dest.exists():
                        dest = order_attach_dir / f"{Path(up.name).stem}_{suffix}{Path(up.name).suffix}"
                        suffix += 1

                    with open(dest, "wb") as f:
                        f.write(up.getbuffer())

                    add_attachment(selected_file, str(dest), dest.name)
                    saved_count += 1

                st.success(f"Added {saved_count} attachment file(s) to bundle order")
                st.rerun()
            else:
                st.warning("Choose files first")

        attachments = get_attachments(selected_file)

        if col_att_b.button("Build Bundle PDF", key=f"build_bundle_btn_{selected_file}"):
            try:
                refreshed = get_order_by_source_file(selected_file)
                safe_order = re.sub(r"[^A-Za-z0-9_-]+", "_", refreshed.get("sales_order") or Path(selected_file).stem)
                bundle_name = f"{safe_order}_bundle.pdf"
                bundle_path = STAMPED_DIR / bundle_name

                button_label = None
                button_url = None
                if refreshed.get("payment_link"):
                    button_label = refreshed.get("payment_label") or "Pay Now"
                    button_url = refreshed.get("payment_link")

                built_path = build_bundle_pdf(
                    source_pdf_path=selected_file,
                    output_pdf_path=bundle_path,
                    logo_path=LOGO_PATH,
                    attachments=attachments,
                    button_label=button_label,
                    button_url=button_url,
                )

                update_order(
                    selected_file,
                    stamped_pdf_path=str(built_path),
                    status="Bundle PDF Created",
                )

                st.success(f"Bundle PDF created: {built_path.name}")
                st.rerun()
            except Exception as e:
                st.error(f"Bundle build failed: {e}")

        if attachments:
            st.caption("Bundle order:")
            for att in attachments:
                c1, c2, c3 = st.columns([1, 6, 1])
                c1.write(att["sort_order"])
                c2.write(att["original_name"])
                if c3.button("Remove", key=f"remove_att_{att['id']}"):
                    delete_attachment(att["id"])
                    st.rerun()
        else:
            st.caption("No additional files attached.")

        refreshed = get_order_by_source_file(selected_file)
        if refreshed.get("payment_link"):
            st.markdown("### Stored Payment Link")
            st.code(refreshed["payment_link"])

        if refreshed.get("stamped_pdf_path"):
            bundle_file = Path(refreshed["stamped_pdf_path"])
            if bundle_file.exists():
                st.caption(f"Bundle file: {bundle_file}")
                with open(bundle_file, "rb") as f:
                    st.download_button(
                        "Download bundled PDF",
                        data=f.read(),
                        file_name=bundle_file.name,
                        mime="application/pdf",
                        key=f"download_bundle_{bundle_file.name}",
                    )
    else:
        st.info("No orders loaded.")

with tab2:
    st.subheader("Pending Quotes")

    uploaded_quote = st.file_uploader("Upload quote PDF", type=["pdf"], key="quotes_pdf")
    if uploaded_quote is not None:
        save_path = INCOMING_DIR / uploaded_quote.name
        with open(save_path, "wb") as f:
            f.write(uploaded_quote.getbuffer())

        quote = parse_sales_order_pdf(save_path)
        quote["status"] = "Quote"
        upsert_order(quote)
        st.success(f"Loaded {uploaded_quote.name}")

    quote_rows = [x for x in get_orders() if x.get("status") == "Quote"]
    if quote_rows:
        quote_df = pd.DataFrame(quote_rows)
        st.dataframe(
            quote_df[["source_file", "customer_name", "sales_order", "total_amount", "balance_due", "status"]],
            use_container_width=True,
        )
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
            st.rerun()
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