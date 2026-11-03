"""
phone_link.py - the "scan to try it on your phone" QR code.

WHY: a QR code is the easiest door for students - no app to install, no link to type.
And on the online (https) copy, the phone's own microphone works for voice.
"""

import io
from urllib.parse import urlsplit

import segno
import streamlit as st

import config


def app_url() -> str | None:
    """The address students should open: APP_URL (from .env or Streamlit Secrets) if set, otherwise the
    address this page is being viewed at. None if that's "localhost" - a phone can't open this computer."""
    url = config._secret("APP_URL")
    if not url:
        try:
            url = st.context.url
        except Exception:
            url = ""
    parts = urlsplit(url or "")
    if not parts.netloc or parts.hostname in ("localhost", "127.0.0.1", "::1"):
        return None
    return f"{parts.scheme}://{parts.netloc}"          # the front page (Student Assistant)


def show_qr(width: int = 200) -> None:
    """QR code + link for phones, or a hint when the app only runs on this computer."""
    url = app_url()
    if not url:
        st.caption("📱 **Phone link:** put the app online first (README: *Deploy it online*). "
                   "A QR code for phones then appears here.")
        return
    png = io.BytesIO()
    segno.make(url, error="m").save(png, kind="png", scale=10, border=2, dark="#4B2A7B")
    st.image(png.getvalue(), width=width)
    st.caption(f"📱 **Scan to try it on your phone**  \n{url}")
