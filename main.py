from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from config.settings import APP_TITLE
from services.db import init_db

st.set_page_config(page_title=APP_TITLE, layout='wide')
init_db()

st.title(APP_TITLE)
st.write('Operations dashboard for DocuSign sales orders and final-payment SMS workflows.')

st.markdown(
    '''
    ### Sections
    - **Pending Orders**: upload PDFs, review extracted data, optionally add Stripe payment link, then submit to DocuSign.
    - **Pending Quotes**: use the same flow for quote PDFs if needed.
    - **Ready SMS**: upload Excel, review message queue, then send via directSMS.
    - **History**: review sent orders and SMS jobs.
    '''
)
