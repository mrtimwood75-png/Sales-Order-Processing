import os
import re
from pathlib import Path

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
BASE_DIR = Path(__file__).resolve().parent

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://example.com/success").strip()
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "https://example.com/cancel").strip()
STRIPE_CURRENCY = os.getenv("STRIPE_CURRENCY", "aud").strip().lower()


def resolve_logo_path():
    candidates = [
        BASE_DIR / "assets" / "boconcept_logo.png",
        BASE_DIR / "assets" / "boconcept_logo.PNG",
        BASE_DIR / "assets" / "BoConcept_logo.png",
        BASE_DIR / "assets" / "BoConcept_logo.PNG",
    ]

    env_path = os.getenv("BOCONCEPT_LOGO_PATH", "").strip()
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    for p in candidates:
        if p.exists():
            return p

    return None


LOGO_PATH = resolve_logo_path()


def reset_session():
    keys = [
        "order_pdf_name",
        "order_pdf_bytes",
        "attachments",
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
        "bundle_pdf_bytes",
        "bundle_pdf_name",
    ]
    for k in keys:
        if k in st.session_state:
            del st.session_state[k]


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


def parse_sales_order_pdf_bytes(pdf_bytes: bytes):
    default_result = {
        "customer_name": "",
        "customer_email": "",
        "phone": "",
        "sales_order": "",
        "order_date": "",
        "total_amount": 0.0,
        "prepayment": 0.0,
        "balance_due": 0.0,
        "payment_mode": "balance",
        "payment_amount": 0.0,
        "payment_label": "Pay Balance Now",
    }

    if fitz is None:
        return default_result

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
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

    total_amount = parse_money(total_raw) or 0.0
    prepayment = parse_money(prepayment_raw) or 0.0
    balance_due = parse_money(balance_raw)

    if balance_due is None:
        balance_due = total_amount - prepayment

    return {
        "customer_name": customer_name,
        "customer_email": find_value(r"(?:E-?mail|Email)\s*:?\s*([^\s]+@[^\s]+)", text),
        "phone": find_value(r"(?:Mobile phone|Mobile|Phone|Telephone)\s*:?\s*([+\d][\d\s]+)", text),
        "sales_order": find_value(r"Sales order\s*:?\s*([A-Za-z0-9\-\/]+)", text),
        "order_date": find_value(r"Date\s*:?\s*([\d]{1,2}[\/\-.][\d]{1,2}[\/\-.][\d]{2,4})", text),
        "total_amount": float(total_amount),
        "prepayment": float(prepayment),
        "balance_due": float(balance_due),
        "payment_mode": "balance",
        "payment_amount": float(balance_due),
        "payment_label": "Pay Balance Now",
    }


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


def ensure_stripe_ready():
    if stripe is None:
        raise RuntimeError("Stripe package not installed")
    if not STRIPE_SECRET_KEY:
        raise RuntimeError("Missing STRIPE_SECRET_KEY environment variable")
    stripe.api_key = STRIPE_SECRET_KEY


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

    return {"url": session.url, "session_id": session.id}


def get_page_text_left_margin(page):
    try:
        blocks = page.get_text("blocks")
    except Exception:
        return 24

    candidates = []
    for block in blocks:
        if len(block) < 5:
            continue
        x0, y0, x1, y1, text = block[:5]
        text_clean = clean_text(text)
        if not text_clean:
            continue
        if len(text_clean) < 2:
            continue
        if y0 < 10:
            continue
        candidates.append(float(x0))

    if not candidates:
        return 24

    left = min(candidates)
    left = max(18, min(left, 80))
    return left


def stamp_main_pdf_bytes(pdf_bytes: bytes, logo_path, button_label=None, button_url=None):
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for page in doc:
        page_width = page.rect.width

        if logo_path and Path(logo_path).exists():
            left_x = get_page_text_left_margin(page)
            logo_rect = fitz.Rect(left_x, 18, left_x + 126, 60)
            page.insert_image(
                logo_rect,
                filename=str(logo_path),
                keep_proportion=True,
                overlay=True,
            )

        if button_label and button_url:
            button_width = 180
            button_height = 30
            x1 = page_width - 24 - button_width
            y1 = 20
            button_rect = fitz.Rect(x1, y1, x1 + button_width, y1 + button_height)

            shape = page.new_shape()
            shape.draw_rect(button_rect)
            shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=1)
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

    output = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return output


def append_image_bytes_as_pdf_pages(bundle_doc, image_bytes: bytes):
    img_doc = fitz.open(stream=image_bytes)
    pdf_bytes = img_doc.convert_to_pdf()
    img_doc.close()

    img_pdf = fitz.open("pdf", pdf_bytes)
    bundle_doc.insert_pdf(img_pdf)
    img_pdf.close()


def append_file_bytes_to_pdf(bundle_doc, file_name: str, file_bytes: bytes):
    ext = Path(file_name).suffix.lower()

    if ext == ".pdf":
        src = fitz.open(stream=file_bytes, filetype="pdf")
        bundle_doc.insert_pdf(src)
        src.close()
        return

    if ext in [".png", ".jpg", ".jpeg", ".webp"]:
        append_image_bytes_as_pdf_pages(bundle_doc, file_bytes)
        return

    raise RuntimeError(f"Unsupported attachment type: {file_name}")


def build_single_bundle_pdf_bytes(main_pdf_bytes: bytes, attachments, logo_path, button_label=None, button_url=None):
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed")

    stamped_main_bytes = stamp_main_pdf_bytes(
        pdf_bytes=main_pdf_bytes,
        logo_path=logo_path,
        button_label=button_label,
        button_url=button_url,
    )

    final_doc = fitz.open()

    main_doc = fitz.open(stream=stamped_main_bytes, filetype="pdf")
    final_doc.insert_pdf(main_doc)
    main_doc.close()

    for att in attachments:
        append_file_bytes_to_pdf(
            bundle_doc=final_doc,
            file_name=att["name"],
            file_bytes=att["bytes"],
        )

    output = final_doc.tobytes(garbage=4, deflate=True)
    final_doc.close()
    return output


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

top_a, top_b = st.columns([3, 1])
if LOGO_PATH:
    top_a.caption(f"Logo loaded: {LOGO_PATH.name}")
else:
    top_a.error("BoConcept logo not found in assets folder")

if top_b.button("Reset Session"):
    reset_session()
    st.rerun()

st.caption("Session-only mode. Nothing is saved after reload.")

uploaded_pdf = st.file_uploader("Upload sales order PDF", type=["pdf"], key="orders_pdf")

if uploaded_pdf is not None:
    pdf_bytes = uploaded_pdf.getvalue()

    if st.session_state.get("order_pdf_name") != uploaded_pdf.name:
        parsed = parse_sales_order_pdf_bytes(pdf_bytes)

        st.session_state["order_pdf_name"] = uploaded_pdf.name
        st.session_state["order_pdf_bytes"] = pdf_bytes
        st.session_state["attachments"] = []
        st.session_state["payment_link"] = ""
        st.session_state["stripe_session_id"] = ""
        st.session_state["bundle_pdf_bytes"] = None
        st.session_state["bundle_pdf_name"] = ""

        st.session_state["customer_name"] = parsed["customer_name"]
        st.session_state["customer_email"] = parsed["customer_email"]
        st.session_state["phone"] = parsed["phone"]
        st.session_state["sales_order"] = parsed["sales_order"]
        st.session_state["order_date"] = parsed["order_date"]
        st.session_state["total_amount"] = parsed["total_amount"]
        st.session_state["prepayment"] = parsed["prepayment"]
        st.session_state["balance_due"] = parsed["balance_due"]
        st.session_state["payment_mode"] = parsed["payment_mode"]
        st.session_state["payment_amount"] = parsed["payment_amount"]
        st.session_state["payment_label"] = parsed["payment_label"]

if st.session_state.get("order_pdf_bytes"):
    st.markdown("### Current Order")
    st.caption(st.session_state.get("order_pdf_name", ""))

    c1, c2, c3 = st.columns(3)
    c1.metric("Total", format_money(st.session_state.get("total_amount")))
    c2.metric("Prepayment", format_money(st.session_state.get("prepayment")))
    c3.metric("Balance due", format_money(st.session_state.get("balance_due")))

    with st.form("order_form"):
        customer_name = st.text_input("Customer name", value=st.session_state.get("customer_name", ""))
        customer_email = st.text_input("Customer email", value=st.session_state.get("customer_email", ""))
        phone = st.text_input("Phone", value=st.session_state.get("phone", ""))
        sales_order = st.text_input("Sales order", value=st.session_state.get("sales_order", ""))
        order_date = st.text_input("Order date", value=st.session_state.get("order_date", ""))

        total_amount = st.number_input("Total amount", min_value=0.0, value=float(st.session_state.get("total_amount", 0.0)), step=0.01)
        prepayment = st.number_input("Prepayment", min_value=0.0, value=float(st.session_state.get("prepayment", 0.0)), step=0.01)
        balance_due = st.number_input("Balance due", min_value=0.0, value=float(st.session_state.get("balance_due", 0.0)), step=0.01)

        current_mode = st.session_state.get("payment_mode", "balance")
        payment_index = 0 if current_mode == "balance" else 1

        payment_choice = st.radio(
            "Payment link type",
            options=["balance", "deposit"],
            index=payment_index,
            format_func=lambda x: "Balance" if x == "balance" else "Deposit (50% of balance)",
            horizontal=True,
        )

        payment_calc = payment_choice_to_values(payment_choice, balance_due)
        st.info(f"{payment_calc['payment_label']} — Amount: {format_money(payment_calc['payment_amount'])}")

        st.text_input("Payment link", value=st.session_state.get("payment_link", ""), disabled=True)
        st.text_input("Stripe session ID", value=st.session_state.get("stripe_session_id", ""), disabled=True)

        b1, b2 = st.columns(2)
        save_clicked = b1.form_submit_button("Apply Changes")
        create_link_clicked = b2.form_submit_button("Create Stripe Link")

    if save_clicked:
        st.session_state["customer_name"] = customer_name
        st.session_state["customer_email"] = customer_email
        st.session_state["phone"] = phone
        st.session_state["sales_order"] = sales_order
        st.session_state["order_date"] = order_date
        st.session_state["total_amount"] = total_amount
        st.session_state["prepayment"] = prepayment
        st.session_state["balance_due"] = balance_due
        st.session_state["payment_mode"] = payment_calc["payment_mode"]
        st.session_state["payment_amount"] = payment_calc["payment_amount"]
        st.session_state["payment_label"] = payment_calc["payment_label"]
        st.success("Changes applied to current session")

    if create_link_clicked:
        try:
            st.session_state["customer_name"] = customer_name
            st.session_state["customer_email"] = customer_email
            st.session_state["phone"] = phone
            st.session_state["sales_order"] = sales_order
            st.session_state["order_date"] = order_date
            st.session_state["total_amount"] = total_amount
            st.session_state["prepayment"] = prepayment
            st.session_state["balance_due"] = balance_due
            st.session_state["payment_mode"] = payment_calc["payment_mode"]
            st.session_state["payment_amount"] = payment_calc["payment_amount"]
            st.session_state["payment_label"] = payment_calc["payment_label"]

            link_result = create_stripe_checkout_link(
                customer_name=customer_name,
                customer_email=customer_email,
                sales_order=sales_order,
                amount=payment_calc["payment_amount"],
                payment_label=payment_calc["payment_label"],
            )

            st.session_state["payment_link"] = link_result["url"]
            st.session_state["stripe_session_id"] = link_result["session_id"]

            st.success("Stripe payment link created")
            st.code(link_result["url"])
        except Exception as e:
            st.error(str(e))

    st.markdown("### Additional Files to Stitch Into Final PDF")
    extra_files = st.file_uploader(
        "Upload extra PDF or image files",
        type=["pdf", "png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="attachments_uploader",
    )

    a1, a2 = st.columns([2, 1])

    if a1.button("Add Files to Bundle"):
        if extra_files:
            if "attachments" not in st.session_state:
                st.session_state["attachments"] = []

            for up in extra_files:
                st.session_state["attachments"].append(
                    {
                        "name": up.name,
                        "bytes": up.getvalue(),
                    }
                )

            st.success(f"Added {len(extra_files)} attachment file(s)")
        else:
            st.warning("Choose files first")

    attachments = st.session_state.get("attachments", [])

    if a2.button("Build Bundle PDF"):
        try:
            safe_order = re.sub(
                r"[^A-Za-z0-9_-]+",
                "_",
                st.session_state.get("sales_order") or Path(st.session_state.get("order_pdf_name", "order")).stem,
            )
            bundle_name = f"{safe_order}_bundle.pdf"

            button_label = None
            button_url = None
            if st.session_state.get("payment_link"):
                button_label = st.session_state.get("payment_label") or "Pay Now"
                button_url = st.session_state.get("payment_link")

            bundle_bytes = build_single_bundle_pdf_bytes(
                main_pdf_bytes=st.session_state["order_pdf_bytes"],
                attachments=attachments,
                logo_path=LOGO_PATH,
                button_label=button_label,
                button_url=button_url,
            )

            st.session_state["bundle_pdf_bytes"] = bundle_bytes
            st.session_state["bundle_pdf_name"] = bundle_name

            st.success("Single bundled PDF created")
        except Exception as e:
            st.error(f"Bundle build failed: {e}")

    if attachments:
        st.caption("Bundle order:")
        for i, att in enumerate(attachments, start=1):
            r1, r2, r3 = st.columns([1, 6, 1])
            r1.write(i)
            r2.write(att["name"])
            if r3.button("Remove", key=f"remove_att_{i}"):
                attachments.pop(i - 1)
                st.session_state["attachments"] = attachments
                st.rerun()
    else:
        st.caption("No additional files attached.")

    if st.session_state.get("payment_link"):
        st.markdown("### Stored Payment Link")
        st.code(st.session_state["payment_link"])

    if st.session_state.get("bundle_pdf_bytes"):
        st.download_button(
            "Download bundled PDF",
            data=st.session_state["bundle_pdf_bytes"],
            file_name=st.session_state.get("bundle_pdf_name", "bundle.pdf"),
            mime="application/pdf",
        )
else:
    st.info("Upload a sales order PDF to begin.")