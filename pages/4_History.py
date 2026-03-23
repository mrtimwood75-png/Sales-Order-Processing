from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from services.db import list_orders, list_sms_jobs

st.title('History')

st.subheader('Orders')
order_df = pd.DataFrame(list_orders())
if not order_df.empty:
    st.dataframe(order_df, use_container_width=True)
else:
    st.info('No order history yet.')

st.subheader('SMS Jobs')
sms_df = pd.DataFrame(list_sms_jobs())
if not sms_df.empty:
    st.dataframe(sms_df, use_container_width=True)
else:
    st.info('No SMS history yet.')
