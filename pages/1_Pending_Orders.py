from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from config.settings import BRAND_LOGO_PATH, SHAREPOINT_INBOX
from services.db import list_orders, upsert_order, update_order
from services.docusign_service import DocusignService
from services.logo_overlay import add_logo_to_pdf
from services.pdf_parser import parse_sales_order_pdf
from services.stripe_service import StripeService

st.title('Pending Sales Orders')

upload = st.file_uploader('Upload BoConcept sales order PDF', type=['pdf'])
if upload:
    original_path = SHAREPOINT_INBOX / upload.name
    with open(original_path, 'wb') as f:
        f.write(upload.getbuffer())

    save_path = original_path
    if BRAND_LOGO_PATH.exists():
        branded_name = f'{original_path.stem}_branded{original_path.suffix}'
        branded_path = SHAREPOINT_INBOX / branded_name
        save_path = Path(add_logo_to_pdf(original_path, branded_path, BRAND_LOGO_PATH))
        st.info(f'Applied BoConcept logo to each page: {save_path.name}')
    else:
        st.warning(f'Logo not found at {BRAND_LOGO_PATH}; using uploaded file without branding overlay.')

    order = parse_sales_order_pdf(save_path)
    upsert_order(order.to_dict())
    st.success(f'Parsed {upload.name}')

orders = list_orders(['Ready', 'Pending', 'Failed', 'Submitted'])
if orders:
    df = pd.DataFrame(orders)
    st.dataframe(df[['source_file', 'customer_name', 'customer_email', 'sales_order', 'total_amount', 'prepayment', 'balance_due', 'status']], use_container_width=True)

    selected = st.selectbox('Select order', options=[o['source_file'] for o in orders])
    current = next(o for o in orders if o['source_file'] == selected)

    with st.form('submit_order_form'):
        customer_name = st.text_input('Customer name', value=current.get('customer_name', ''))
        customer_email = st.text_input('Customer email', value=current.get('customer_email', ''))
        sales_order = st.text_input('Sales order', value=current.get('sales_order', ''))
        total_amount = st.number_input('Order total', value=float(current.get('total_amount') or 0.0), min_value=0.0, step=0.01)
        balance_due = st.number_input('Balance due', value=float(current.get('balance_due') or 0.0), min_value=0.0, step=0.01)
        add_payment_link = st.checkbox('Add payment link', value=bool(current.get('payment_link')))
        submitted = st.form_submit_button('Submit to DocuSign')

    if submitted:
        payment_link = current.get('payment_link', '')
        if add_payment_link and not payment_link:
            payment_link = StripeService().create_checkout_session(customer_email, sales_order, balance_due or total_amount)
        envelope_id = DocusignService().send_envelope(
            pdf_path=current['source_file'],
            signer_name=customer_name,
            signer_email=customer_email,
            sales_order=sales_order,
            payment_link=payment_link,
        )
        update_order(
            current['source_file'],
            customer_name=customer_name,
            customer_email=customer_email,
            sales_order=sales_order,
            total_amount=total_amount,
            balance_due=balance_due,
            payment_link=payment_link,
            envelope_id=envelope_id,
            status='Submitted',
        )
        st.success(f'Submitted. Envelope ID: {envelope_id}')
else:
    st.info('No orders loaded yet.')
