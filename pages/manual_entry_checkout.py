import os
from pathlib import Path

import streamlit as st

try:
    import stripe
except Exception:
    stripe = None


APP_TITLE = "Manual Entry Checkout"
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

STRIPE_SECRET_KEY = st.secrets.get("STRIPE_SECRET_KEY", os.getenv("STRIPE_SECRET_KEY", "")).strip()
STRIPE_SUCCESS_URL = st.secrets.get(
    "STRIPE_SUCCESS_URL",
    os.getenv("STRIPE_SUCCESS_URL", "https://example.com/success"),
).strip()
STRIPE_CANCEL_URL = st.secrets.get(
    "STRIPE_CANCEL_URL",
    os.getenv("STRIPE_CANCEL_URL", "https://example.com/cancel"),
).strip()
STRIPE_CURRENCY = st.secrets.get(
    "STRIPE_CURRENCY",
    os.getenv("STRIPE_CURRENCY", "aud"),
).strip().lower()
STRIPE_TEST_FALLBACK_URL = st.secrets.get(
    "STRIPE_TEST_FALLBACK_URL",
    os.getenv("STRIPE_TEST_FALLBACK_URL", "https://buy.stripe.com/test_14A5kC7WQ3KQ4qQeUU"),
).strip()


def resolve_logo_path():
    candidates = [
        PROJECT_ROOT / "assets" / "boconcept_logo.png",
        PROJECT_ROOT / "assets" / "boconcept_logo.PNG",
        PROJECT_ROOT / "assets" / "BoConcept_logo.png",
        PROJECT_ROOT / "assets" / "BoConcept_logo.PNG",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


LOGO_PATH = resolve_logo_path()


def ensure_stripe_ready():
    if stripe is None:
        raise RuntimeError("Stripe package not installed")
    if not STRIPE_SECRET_KEY:
        raise RuntimeError("Missing STRIPE_SECRET_KEY in Streamlit secrets")
    stripe.api_key = STRIPE_SECRET_KEY


def parse_numeric_input(text, fallback=0.0):
    try:
        return float(str(text).replace(",", "").strip() or 0)
    except Exception:
        return float(fallback)


def format_money(value):
    return f"{float(value or 0):,.2f}"


def payment_choice_to_values(choice: str, balance_due: float):
    bal = round(float(balance_due or 0), 2)

    if choice == "deposit":
        deposit_amount = round(bal * 0.50, 2) if bal > 0 else 0.0
        return {
            "payment_mode": "deposit",
            "payment_amount": deposit_amount,
            "payment_label": "Pay 50% Deposit Now",
        }

    return {
        "payment_mode": "balance",
        "payment_amount": bal,
        "payment_label": "Pay Balance Now",
    }


def create_stripe_checkout_link(customer_name, customer_email, sales_order, amount, payment_label):
    amount_value = float(amount or 0)
    if amount_value <= 0:
        raise RuntimeError("Payment amount must be greater than 0")

    if STRIPE_SECRET_KEY and stripe is not None:
        ensure_stripe_ready()

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

    return {"url": STRIPE_TEST_FALLBACK_URL, "session_id": "test_link"}


def init_state():
    defaults = {
        "manual_customer_name": "",
        "manual_customer_email": "",
        "manual_phone": "",
        "manual_sales_order": "",
        "manual_order_date": "",
        "manual_total_amount": 0.0,
        "manual_prepayment": 0.0,
        "manual_balance_due": 0.0,
        "manual_payment_mode": "balance",
        "manual_payment_amount": 0.0,
        "manual_payment_label": "Pay Balance Now",
        "manual_payment_link": "",
        "manual_stripe_session_id": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


st.set_page_config(page_title=APP_TITLE, layout="wide")
init_state()

top_nav_left, top_nav_right = st.columns([1, 5])
with top_nav_left:
    if st.button("Home", use_container_width=True):
        st.switch_page("main.py")
with top_nav_right:
    if LOGO_PATH:
        st.image(str(LOGO_PATH), width=220)

st.title(APP_TITLE)

if STRIPE_SECRET_KEY:
    st.caption("Stripe mode: live/session creation enabled")
else:
    st.warning("Stripe secret missing. Fallback test link will be used.")

with st.form("manual_entry_form"):
    col_a, col_b, col_c = st.columns(3)
    customer_name = col_a.text_input("Customer", value=st.session_state["manual_customer_name"])
    customer_email = col_b.text_input("Email", value=st.session_state["manual_customer_email"])
    phone = col_c.text_input("Phone", value=st.session_state["manual_phone"])

    col_d, col_e, col_f = st.columns(3)
    sales_order = col_d.text_input("Sales order", value=st.session_state["manual_sales_order"])
    order_date = col_e.text_input("Order date", value=st.session_state["manual_order_date"])
    payment_choice = col_f.radio(
        "Payment type",
        options=["balance", "deposit"],
        index=0 if st.session_state["manual_payment_mode"] == "balance" else 1,
        format_func=lambda x: "Balance" if x == "balance" else "Deposit 50%",
        horizontal=True,
    )

    col_g, col_h, col_i, col_j, col_k = st.columns(5)
    total_amount = col_g.text_input("Total", value=f"{float(st.session_state['manual_total_amount']):.2f}")
    prepayment = col_h.text_input("Prepayment", value=f"{float(st.session_state['manual_prepayment']):.2f}")
    balance_due = col_i.text_input("Balance due", value=f"{float(st.session_state['manual_balance_due']):.2f}")

    parsed_total_amount = parse_numeric_input(total_amount, st.session_state["manual_total_amount"])
    parsed_prepayment = parse_numeric_input(prepayment, st.session_state["manual_prepayment"])
    parsed_balance_due = parse_numeric_input(balance_due, st.session_state["manual_balance_due"])

    payment_calc = payment_choice_to_values(payment_choice, parsed_balance_due)

    current_payment_amount = st.session_state["manual_payment_amount"]
    if current_payment_amount <= 0:
        current_payment_amount = payment_calc["payment_amount"]
    if st.session_state["manual_payment_mode"] != payment_choice:
        current_payment_amount = payment_calc["payment_amount"]

    payment_amount_input = col_j.text_input(
        "Payment amount",
        value=f"{float(current_payment_amount):.2f}",
    )
    overridden_payment_amount = parse_numeric_input(payment_amount_input, payment_calc["payment_amount"])
    col_k.metric("Default", format_money(payment_calc["payment_amount"]))

    effective_payment_label = "Pay 50% Deposit Now" if payment_choice == "deposit" else "Pay Balance Now"

    st.caption(
        f"{effective_payment_label}  |  "
        f"Balance due: {format_money(parsed_balance_due)}  |  "
        f"Payment amount: {format_money(overridden_payment_amount)}"
    )

    if st.session_state["manual_payment_link"]:
        st.text_input("Payment link", value=st.session_state["manual_payment_link"], disabled=True)

    b1, b2 = st.columns(2)
    save_clicked = b1.form_submit_button("Apply Changes")
    create_link_clicked = b2.form_submit_button("Create Stripe Link")

if save_clicked:
    st.session_state["manual_customer_name"] = customer_name
    st.session_state["manual_customer_email"] = customer_email
    st.session_state["manual_phone"] = phone
    st.session_state["manual_sales_order"] = sales_order
    st.session_state["manual_order_date"] = order_date
    st.session_state["manual_total_amount"] = parsed_total_amount
    st.session_state["manual_prepayment"] = parsed_prepayment
    st.session_state["manual_balance_due"] = parsed_balance_due
    st.session_state["manual_payment_mode"] = payment_choice
    st.session_state["manual_payment_amount"] = overridden_payment_amount
    st.session_state["manual_payment_label"] = effective_payment_label
    st.success("Changes applied")
    st.rerun()

if create_link_clicked:
    try:
        st.session_state["manual_customer_name"] = customer_name
        st.session_state["manual_customer_email"] = customer_email
        st.session_state["manual_phone"] = phone
        st.session_state["manual_sales_order"] = sales_order
        st.session_state["manual_order_date"] = order_date
        st.session_state["manual_total_amount"] = parsed_total_amount
        st.session_state["manual_prepayment"] = parsed_prepayment
        st.session_state["manual_balance_due"] = parsed_balance_due
        st.session_state["manual_payment_mode"] = payment_choice
        st.session_state["manual_payment_amount"] = overridden_payment_amount
        st.session_state["manual_payment_label"] = effective_payment_label

        link_result = create_stripe_checkout_link(
            customer_name=customer_name,
            customer_email=customer_email,
            sales_order=sales_order,
            amount=overridden_payment_amount,
            payment_label=effective_payment_label,
        )

        st.session_state["manual_payment_link"] = link_result["url"]
        st.session_state["manual_stripe_session_id"] = link_result["session_id"]

        st.success("Stripe payment link created")
        st.code(link_result["url"])
    except Exception as e:
        st.error(str(e))