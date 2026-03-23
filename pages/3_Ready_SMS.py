from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from services.db import insert_sms_job, list_sms_jobs, update_sms_job
from services.directsms_service import DirectSMSService
from services.excel_parser import parse_ready_delivery_report

st.title('Ready for Delivery SMS')

upload = st.file_uploader('Upload ready-for-delivery Excel report', type=['xlsx', 'xls'])
if upload:
    temp_path = ROOT / 'data' / upload.name
    with open(temp_path, 'wb') as f:
        f.write(upload.getbuffer())
    df = parse_ready_delivery_report(temp_path)
    for _, row in df.iterrows():
        balance = row.get('balance_due')
        order_no = row.get('sales_order')
        msg = f"Hi {row.get('customer_name')}, your BoConcept order {order_no} is ready. Balance payable is ${balance:,.2f}. Please contact the store to arrange delivery."
        insert_sms_job({
            'source_file': str(temp_path),
            'customer_name': row.get('customer_name'),
            'phone': str(row.get('phone')),
            'sales_order': str(order_no),
            'total_amount': row.get('total_amount'),
            'balance_due': balance,
            'message': msg,
            'status': 'Ready',
            'sms_message_id': '',
        })
    st.success('SMS queue created.')

jobs = list_sms_jobs(['Ready', 'Pending', 'Failed', 'Sent'])
if jobs:
    df = pd.DataFrame(jobs)
    st.dataframe(df[['id', 'customer_name', 'phone', 'sales_order', 'balance_due', 'status']], use_container_width=True)

    selected_id = st.selectbox('Select SMS job', options=[j['id'] for j in jobs])
    current = next(j for j in jobs if j['id'] == selected_id)
    with st.form('send_sms_form'):
        message = st.text_area('SMS text', value=current['message'], height=120)
        send = st.form_submit_button('Send SMS')
    if send:
        sms_id = DirectSMSService().send(current['phone'], message)
        update_sms_job(selected_id, message=message, sms_message_id=sms_id, status='Sent')
        st.success(f'SMS sent. ID: {sms_id}')
else:
    st.info('No SMS jobs loaded yet.')
