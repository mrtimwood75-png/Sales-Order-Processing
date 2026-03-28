from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config.settings import DATA_DIR
from services.db import init_db, insert_sms_job, list_sms_jobs, update_sms_job
from services.directsms_service import DirectSMSService
from services.excel_parser import parse_ready_delivery_report

init_db()

st.title("Ready for Delivery SMS")

upload = st.file_uploader("Upload ready-for-delivery Excel report", type=["xlsx", "xls"])
if upload:
    temp_path = DATA_DIR / upload.name
    with open(temp_path, "wb") as f:
        f.write(upload.getbuffer())

    df = parse_ready_delivery_report(temp_path)

    for _, row in df.iterrows():
        balance = float(row.get("balance_due") or 0.0)
        order_no = str(row.get("sales_order") or "").strip()
        customer_name = str(row.get("customer_name") or "").strip()
        phone = str(row.get("phone") or "").strip()

        msg = (
            f"Hi {customer_name}, your BoConcept order {order_no} is ready. "
            f"Balance payable is ${balance:,.2f}. Please contact the store to arrange delivery."
        )

        insert_sms_job(
            {
                "source_file": str(temp_path),
                "customer_name": customer_name,
                "phone": phone,
                "sales_order": order_no,
                "total_amount": row.get("total_amount"),
                "balance_due": balance,
                "message": msg,
                "status": "Ready",
                "sms_message_id": "",
            }
        )

    st.success("SMS queue created.")

jobs = list_sms_jobs(["Ready", "Pending", "Failed", "Sent"])
if jobs:
    df = pd.DataFrame(jobs)
    st.dataframe(
        df[["id", "customer_name", "phone", "sales_order", "balance_due", "status"]],
        use_container_width=True,
    )

    selected_id = st.selectbox("Select SMS job", options=[j["id"] for j in jobs])
    current = next(j for j in jobs if j["id"] == selected_id)

    with st.form("send_sms_form"):
        message = st.text_area("SMS text", value=current["message"], height=120)
        send = st.form_submit_button("Send SMS")

    if send:
        sms_id = DirectSMSService().send(current["phone"], message)
        update_sms_job(selected_id, message=message, sms_message_id=sms_id, status="Sent")
        st.success(f"SMS sent. ID: {sms_id}")
else:
    st.info("No SMS jobs loaded yet.")