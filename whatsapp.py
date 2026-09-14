"""
WhatsApp Cloud API client — send a text reply, and download a photo someone sent.

Talks directly to Meta's Graph API (no third-party BSP). Credentials come from
the environment (#7): WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID.
"""

import os

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

GRAPH_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"


def _token() -> str:
    return os.getenv("WHATSAPP_TOKEN", "")


def _phone_id() -> str:
    return os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")


def _auth() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


def send_text(to: str, body: str) -> dict:
    """Send a plain-text WhatsApp message. `to` is the sender's number (msg['from'])."""
    resp = requests.post(
        f"{GRAPH}/{_phone_id()}/messages",
        headers=_auth(),
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body[:4096]},  # WhatsApp text cap
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def download_media(media_id: str) -> tuple[bytes, str]:
    """Fetch a media attachment by id. Returns (bytes, mime_type).

    Two steps: ask the Graph API for the media's URL, then download it (both
    calls need the auth header).
    """
    meta = requests.get(f"{GRAPH}/{media_id}", headers=_auth(), timeout=30).json()
    url = meta["url"]
    mime = meta.get("mime_type", "image/jpeg")
    content = requests.get(url, headers=_auth(), timeout=60).content
    return content, mime
