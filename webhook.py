"""
Phase 8 — WhatsApp webhook.

Meta calls this app when someone messages the business number:
  - GET  /webhook  → one-time verification handshake (uses WHATSAPP_VERIFY_TOKEN)
  - POST /webhook  → incoming messages

A photo is routed to the receipt reader (read → store → categorize); any text is
routed to the CFO agent. Every reply is a DIRECT response to something the user
just sent — within WhatsApp's free 24-hour window — so we never initiate an
unsolicited message (non-negotiable #2).

Run it:
    uvicorn webhook:app --port 8000
Then expose it publicly for Meta with:  ngrok http 8000
"""

import hashlib
import hmac
import json
import os
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request, Response

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from categorize import categorize_transaction
from cfo_agent import ask_cfo
from db import get_connection
from read_receipt import read_receipt
from store import save_receipt
from whatsapp import download_media, send_text

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")  # optional; enables signature check

UPLOADS = Path("uploads")
UPLOADS.mkdir(exist_ok=True)

# WhatsApp doesn't render markdown tables — ask the agent for plain text.
WHATSAPP_STYLE = (
    "You are replying over WhatsApp. Keep it short and plain-text: no markdown "
    "tables, no headings. Use simple dashes for lists and *single asterisks* for "
    "the odd bold word. A few short lines is ideal."
)

# Per-sender conversation memory so follow-up replies keep context (e.g. the
# agent asks a question and the owner's next message is understood as the answer).
# In-memory only: it resets when the server restarts, which is fine for now.
CONVERSATIONS: dict[str, list] = {}
MAX_HISTORY = 30  # cap messages kept per sender to bound tokens/memory


def _trim(history: list) -> None:
    """Keep history from growing forever, without breaking the message sequence.

    Trims oldest messages but always leaves the list starting on a real user
    turn (a plain string), so we never orphan a tool_result from its tool_use.
    """
    while len(history) > MAX_HISTORY:
        del history[0]
    while history and not (
        history[0].get("role") == "user" and isinstance(history[0].get("content"), str)
    ):
        del history[0]


app = FastAPI()


@app.get("/webhook")
def verify(request: Request):
    """Meta's verification handshake when you register the webhook URL."""
    params = request.query_params
    if (params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == VERIFY_TOKEN):
        # Must echo the challenge back as plain text.
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    return Response(content="verification failed", status_code=403)


@app.post("/webhook")
async def receive(request: Request, background: BackgroundTasks):
    raw = await request.body()

    # If an app secret is configured, verify Meta's signature before trusting it.
    if APP_SECRET:
        signature = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return Response(content="bad signature", status_code=403)

    body = json.loads(raw or b"{}")
    # Acknowledge immediately; do the slow work (LLM calls) in the background so
    # WhatsApp doesn't time out and retry.
    background.add_task(handle_payload, body)
    return {"status": "received"}


# --- Message handling --------------------------------------------------------

def handle_payload(body: dict) -> None:
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                try:
                    _handle_message(msg)
                except Exception as e:  # never let one bad message kill the worker
                    sender = msg.get("from")
                    if sender:
                        send_text(sender, f"Sorry, something went wrong: {e}")


def _handle_message(msg: dict) -> None:
    sender = msg["from"]
    mtype = msg.get("type")

    if mtype == "text":
        _handle_text(sender, msg["text"]["body"])
    elif mtype == "image":
        _handle_image(sender, msg["image"]["id"])
    elif mtype == "document" and "document" in msg:
        _handle_image(sender, msg["document"]["id"])  # e.g. a PDF invoice
    else:
        send_text(sender, "Send me a photo of a receipt to file it, or ask a "
                          "question about your books.")


def _handle_text(sender: str, text: str) -> None:
    history = CONVERSATIONS.setdefault(sender, [])
    answer = ask_cfo(text, verbose=False, system_suffix=WHATSAPP_STYLE, history=history)
    _trim(history)
    send_text(sender, answer or "I didn't catch that — try asking again.")


def _handle_image(sender: str, media_id: str) -> None:
    content, mime = download_media(media_id)
    ext = ".png" if "png" in mime else (".pdf" if "pdf" in mime else ".jpg")
    path = UPLOADS / f"{media_id}{ext}"
    path.write_bytes(content)

    receipt = read_receipt(path)
    _, transaction_id = save_receipt(receipt)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            result = categorize_transaction(cur, transaction_id)
    finally:
        conn.close()

    reply = [f"Filed: *{receipt.vendor}* — {receipt.currency} {receipt.total_amount:.2f}"]
    if receipt.date:
        reply.append(f"Date: {receipt.date}")
    if result["outcome"] == "sorted":
        reply.append(f"Category: {result['category']} (auto-sorted)")
    else:
        reply.append(f"Needs your review — suggested: {result.get('suggested_category')}")
    message = "\n".join(reply)

    # Remember what we just filed so the owner's follow-up ("what was that?",
    # "what category should it be?") has context.
    history = CONVERSATIONS.setdefault(sender, [])
    history.append({"role": "user", "content": f"[I just sent a photo of a receipt from {receipt.vendor}.]"})
    history.append({"role": "assistant", "content": message})
    _trim(history)

    send_text(sender, message)
