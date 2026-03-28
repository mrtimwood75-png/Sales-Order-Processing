from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services.db import init_db, list_orders, list_sms_jobs

init_db()

st.title("History")

st.subheader("Orders")
order_df = pd.DataFrame(list_orders())
if not order_df.empty:
    st.dataframe(order_df, use_container_width=True)
else:
    st.info("No order history yet.")

st.subheader("SMS Jobs")
sms_df = pd.DataFrame(list_sms_jobs())
if not sms_df.empty:
    st.dataframe(sms_df, use_container_width=True)
else:
    st.info("No SMS history yet.")