# BoConcept Ops App

Internal Streamlit app for two workflows:

1. Send BoConcept sales orders to DocuSign.
2. Extract delivery-ready balances from Excel and send SMS via directSMS.

## Features

- PDF parsing for customer name, email, order number, totals, prepayment, and balance due.
- Manual review queue with Submit button before DocuSign send.
- Optional Stripe Checkout Session link generation per order.
- Excel import for pending final-payment SMS workflow.
- directSMS API integration wrapper.
- SQLite job log.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app/main.py
```

## Notes

- DocuSign, Stripe, and directSMS calls are implemented as integration-ready service wrappers. Add your live credentials in `.env` before use.
- If you want logo overlay before DocuSign send, add a PNG logo at `assets/logo.png` and extend `services/logo_overlay.py`.
- The app stores uploaded files and the SQLite log in `data/`.

## Repo upload

Upload the full folder contents to a new GitHub repository.
