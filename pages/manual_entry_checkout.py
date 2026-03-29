def directsms_send_message(to_mobile, message, debug=False):
    connectionid = directsms_connect(debug=debug)
    senderid = get_secret("DIRECTSMS_SENDERID", "").strip()

    if not senderid:
        raise ValueError("Missing DIRECTSMS_SENDERID in Streamlit secrets.")

    data = {
        "connectionid": connectionid,
        "message": message,
        "to": normalize_mobile_au(to_mobile),
        "senderid": senderid,
        "type": "1-way",
    }

    url = "https://api.directsms.com.au/s3/http/send_message"
    resp = requests.post(url, data=data, timeout=30)

    if debug:
        add_diag("directSMS URL", url)
        add_diag("HTTP status", resp.status_code)
        add_diag("Response body", resp.text)
        add_diag("Payload", data)

    resp.raise_for_status()
    text = resp.text.strip()

    if text.lower().startswith("err:"):
        raise ValueError(text)

    if not text.lower().startswith("id:"):
        raise ValueError(f"Unexpected directSMS response: {text}")

    return text.split(":", 1)[1].strip()