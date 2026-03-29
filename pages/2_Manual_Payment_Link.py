from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config.settings import SHAREPOINT_INBOX
from services.db import init_db, list_orders, upsert_order
from services.pdf_parser import parse_sales_order_pdf

init_db()

st.title("Pending Quotes")
st.caption("Use this page if quotes follow the same PDF structure and need review before sending.")

upload = st.file_uploader("Upload quote PDF", type=["pdf"], key="quote_uploader")
if upload:
    save_path = SHAREPOINT_INBOX / upload.name
    with open(save_path, "wb") as f:
        f.write(upload.getbuffer())

    quote = parse_sales_order_pdf(save_path)
    quote.status = "Ready"
    upsert_order(quote.to_dict())
    st.success(f"Parsed {upload.name}")

orders = list_orders(["Ready", "Pending"])
if orders:
    df = pd.DataFrame(orders)
    st.dataframe(
        df[["source_file", "customer_name", "customer_email", "sales_order", "total_amount", "status"]],
        use_container_width=True,
    )
else:
    st.info("No quotes loaded yet.")
