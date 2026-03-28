from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config.settings import SHAREPOINT_INBOX
from services.db import init_db, list_orders, upsert_order, update_order
from services.docusign_service import DocusignService
from services.pdf_parser import parse_sales_order_pdf
from services.stripe_service import StripeService

init_db()

st.title("Pending Sales Orders")

upload = st.file_uploader("Upload BoConcept sales order PDF", type=["pdf"])
if upload:
    save_path = SHAREPOINT_INBOX / upload.name
    with open(save_path, "wb") as f:
        f.write(upload.getbuffer())

    order = parse_sales_order_pdf(save_path)
    upsert_order(order.to_dict())
    st.success(f"Parsed {upload.name}")

orders = list_orders(["Ready", "Pending", "Failed", "Submitted"])
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
    st.dataframe(df[display_cols], use_container_width=True)

    selected = st.selectbox("Select order", options=[o["source_file"] for o in orders])
    current = next(o for o in orders if o["source_file"] == selected)

    with st.form("submit_order_form"):
        customer_name = st.text_input("Customer name", value=current.get("customer_name") or "")
        customer_email = st.text_input("Customer email", value=current.get("customer_email") or "")
        sales_order = st.text_input("Sales order", value=current.get("sales_order") or "")
        total_amount = st.number_input(
            "Order total",
            value=float(current.get("total_amount") or 0.0),
            min_value=0.0,
            step=0.01,
        )
        balance_due = st.number_input(
            "Balance due",
            value=float(current.get("balance_due") or 0.0),
            min_value=0.0,
            step=0.01,
        )
        add_payment_link = st.checkbox("Add payment link", value=bool(current.get("payment_link")))
        submitted = st.form_submit_button("Submit to DocuSign")

    if submitted:
        payment_link = current.get("payment_link") or ""
        if add_payment_link and not payment_link:
            payment_link = StripeService().create_checkout_session(
                customer_email=customer_email,
                sales_order=sales_order,
                amount=balance_due or total_amount,
            )

        envelope_id = DocusignService().send_envelope(
            pdf_path=current["source_file"],
            signer_name=customer_name,
            signer_email=customer_email,
            sales_order=sales_order,
            payment_link=payment_link,
        )

        update_order(
            current["source_file"],
            customer_name=customer_name,
            customer_email=customer_email,
            sales_order=sales_order,
            total_amount=total_amount,
            balance_due=balance_due,
            payment_link=payment_link,
            envelope_id=envelope_id,
            status="Submitted",
        )
        st.success(f"Submitted. Envelope ID: {envelope_id}")
else:
    st.info("No orders loaded yet.")