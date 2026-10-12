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
from db import db_cursor, get_connection
from read_receipt import read_receipt
from security import (
    _owner_numbers, allow_rate, cap_message, print_security_audit,
    sanitize_untrusted, signature_ok,
)
from store import save_receipt
from tenants import company_for_number, is_company_active
from whatsapp import download_media, send_document, send_text

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")  # when set, signatures are verified

# Print (to the logs) whether we're running locked-down or wide-open, so the
# operator can see at a glance if OWNER_NUMBERS / WHATSAPP_APP_SECRET are set.
print_security_audit()


def _backfill_default_company_numbers() -> None:
    """Keep the existing M26 client working after multi-tenancy is switched on.

    Before tenancy, access was controlled by OWNER_NUMBERS (an allowlist). Now a
    sender is routed to a company via company_numbers. M26 is company 1, so we
    map each OWNER_NUMBERS entry to company 1 if it isn't already mapped — a
    one-time, idempotent backfill. New companies register their own numbers at
    provisioning time, so this only ever affects M26's legacy numbers.
    """
    nums = _owner_numbers()
    if not nums:
        return
    try:
        with db_cursor() as cur:
            for n in nums:
                cur.execute(
                    "INSERT IGNORE INTO company_numbers (company_id, wa_number) "
                    "VALUES (1, %s)",
                    (n,),
                )
        print(f"[tenancy] ensured {len(nums)} M26 owner number(s) map to company 1")
    except Exception as e:
        # If the tenancy migration hasn't run yet (no company_numbers table),
        # don't crash startup — just log it so the operator runs the migration.
        print(f"[tenancy] could not backfill company_numbers ({e}); "
              "run migrations/001_add_company_tenancy.sql")


_backfill_default_company_numbers()

# Sent (as static text, no AI call) when an approved owner is messaging too fast.
RATE_LIMIT_NOTICE = "You're sending messages very quickly — give me a moment to catch up."

UPLOADS = Path("uploads")
UPLOADS.mkdir(exist_ok=True)

# WhatsApp doesn't render markdown tables — ask the agent for plain text.
WHATSAPP_STYLE = (
    "You are replying over WhatsApp. Keep it short and plain-text: no markdown "
    "tables, no headings. Use simple dashes for lists and *single asterisks* for "
    "the odd bold word. A few short lines is ideal. "
    "When you generate a report, the file is sent to the owner as a WhatsApp "
    "attachment automatically — so DON'T mention any file path or where it was "
    "saved. Just say the report is attached and give a one-line summary."
)

# Per-sender conversation memory so follow-up replies keep context (e.g. the
# agent asks a question and the owner's next message is understood as the answer).
# In-memory only: it resets when the server restarts, which is fine for now.
CONVERSATIONS: dict[str, list] = {}
MAX_HISTORY = 30  # cap messages kept per sender to bound tokens/memory

# A friendly introduction sent when someone greets the bot or messages for the
# first time — instead of a generic "hello world".
WELCOME = (
    "Hi, I'm your AI bookkeeping assistant. I help keep your business records in order.\n\n"
    "Here's what I can do:\n"
    "- Send me a photo of a receipt or slip and I'll read it and file it for you\n"
    "- Ask me things like \"how much did I spend this month?\" or \"what did I buy at Woolworths?\"\n"
    "- I'll flag anything that needs your okay before it's final\n\n"
    "Go ahead — send me a receipt, or ask me a question about your money."
)

# Short greetings that should trigger the introduction rather than the CFO agent.
_GREETINGS = {"hi", "hie", "hey", "hello", "hallo", "yebo", "start", "help",
              "menu", "hi there", "good morning", "good afternoon", "good evening"}


def log_message(wa_from: str, direction: str, body: str, company_id: int,
                kind: str = "text", wa_msg_id: str | None = None) -> None:
    """Save one message (in or out) so the owner can read the conversations later.

    company_id ties the message to the business it belongs to. wa_msg_id is
    WhatsApp's own id for the message, kept so that a message the owner later
    quotes/replies to can be looked up. Logging must never break message
    handling, so any failure here is swallowed.
    """
    try:
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO message_log (company_id, wa_from, direction, kind, body, wa_msg_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (company_id, wa_from, direction, kind, body, wa_msg_id),
            )
    except Exception:
        pass


def reply(sender: str, text: str, company_id: int, kind: str = "text") -> None:
    """Send the reply, then log it (with the id WhatsApp assigns) so the owner
    can later swipe-to-reply to it and we can look it up."""
    resp = send_text(sender, text)
    wamid = None
    try:
        wamid = resp["messages"][0]["id"]
    except (KeyError, IndexError, TypeError):
        pass
    log_message(sender, "out", text, company_id, kind, wa_msg_id=wamid)


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

from dashboard_api import router as dashboard_router
app.include_router(dashboard_router)

from provision_api import router as provision_router
app.include_router(provision_router)


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    """Health check for uptime monitoring (e.g. UptimeRobot).

    Answers both GET and HEAD — many uptime monitors send a lightweight HEAD
    request, which a plain GET-only route would reject with 405.

    Returns 200 with {"status":"ok"} only if the app is running AND the database
    is reachable — so a single check confirms the whole bot is alive. Returns 503
    if the database can't be reached. Reveals nothing sensitive.
    """
    try:
        with db_cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "ok"}
    except Exception:
        return Response(content='{"status":"db_unreachable"}',
                        media_type="application/json", status_code=503)


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

    # If an app secret is configured, verify Meta's signature before trusting the
    # request at all — this rejects anyone POSTing forged messages to the endpoint.
    if APP_SECRET:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not signature_ok(APP_SECRET, signature, raw):
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
                sender = msg.get("from")
                # 1) Tenant routing + access control in one step: the sender's
                #    number tells us whose books this is. A number not registered
                #    to any company is an unknown stranger — ignored silently (no
                #    reply, no AI call, nothing revealed), exactly as before.
                company_id = company_for_number(sender) if sender else None
                if not company_id:
                    print(f"[blocked] message from unregistered number ({msg.get('type')})")
                    continue
                # 2) A suspended/cancelled subscription pauses that company's bot
                #    without touching its data.
                if not is_company_active(company_id):
                    print(f"[paused] company {company_id} is not active — ignoring message")
                    continue
                try:
                    _handle_message(msg, company_id)
                except Exception as e:  # never let one bad message kill the worker
                    if sender:
                        reply(sender, f"Sorry, something went wrong: {e}", company_id)


def _handle_message(msg: dict, company_id: int) -> None:
    sender = msg["from"]
    mtype = msg.get("type")

    # Rate limit: stop one number flooding us with (paid) AI work. The sender is
    # already a known, active company here, so a brief static notice is safe.
    if not allow_rate(sender):
        log_message(sender, "in", "[rate limited]", company_id, "system")
        reply(sender, RATE_LIMIT_NOTICE, company_id)
        return

    wamid = msg.get("id")
    if mtype == "text":
        body = msg["text"]["body"]
        log_message(sender, "in", body, company_id, "text", wa_msg_id=wamid)
        # If the owner swiped-to-reply to an earlier message, fetch that message
        # so the agent knows what they're referring to.
        quoted = _quoted_message(msg, company_id)
        _handle_text(sender, body, company_id, quoted)
    elif mtype == "image":
        log_message(sender, "in", "[receipt photo]", company_id, "image", wa_msg_id=wamid)
        _handle_image(sender, msg["image"]["id"], company_id)
    elif mtype == "document" and "document" in msg:
        log_message(sender, "in", "[document]", company_id, "document", wa_msg_id=wamid)
        _handle_image(sender, msg["document"]["id"], company_id)  # e.g. a PDF invoice
    else:
        log_message(sender, "in", f"[{mtype} message]", company_id, "system", wa_msg_id=wamid)
        reply(sender, "Send me a photo of a receipt to file it, or ask a "
                      "question about your books.", company_id)


def _quoted_message(msg: dict, company_id: int) -> dict | None:
    """If this message is a reply to an earlier one, look that earlier message up.

    WhatsApp puts a `context` with the quoted message's id on a swipe-to-reply.
    We find that id in this company's message_log (both the bot's and the owner's
    past messages are recorded) and return its direction + body, or None if we
    can't find it (e.g. it predates message-id logging).
    """
    ctx = msg.get("context") or {}
    quoted_id = ctx.get("id")
    if not quoted_id:
        return None
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT direction, kind, body FROM message_log "
                "WHERE wa_msg_id = %s AND company_id = %s ORDER BY id DESC LIMIT 1",
                (quoted_id, company_id),
            )
            return cur.fetchone()
    except Exception:
        return None


def _handle_text(sender: str, text: str, company_id: int,
                 quoted: dict | None = None) -> None:
    text = cap_message(text)  # bound one huge message so it can't blow up token cost
    history = CONVERSATIONS.setdefault(sender, [])

    # Greet + introduce when the message is just a greeting (so a real first
    # question still gets a real answer instead of the welcome blurb).
    if text.strip().lower().strip("!.") in _GREETINGS:
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": WELCOME})
        _trim(history)
        reply(sender, WELCOME, company_id)
        return

    # If the owner replied to an earlier message, give the agent that context so
    # it knows what "this" / "it" refers to — even if the message is days old and
    # long gone from the rolling memory. The quoted text is treated as data (it's
    # sanitised and clearly labelled), per the agent's security rules.
    question = text
    if quoted and quoted.get("body"):
        who = "You (the assistant) had said" if quoted["direction"] == "out" \
            else "The owner had earlier said"
        quoted_text = sanitize_untrusted(quoted["body"], max_len=600)
        question = (f"[The owner is replying to an earlier message. {who}: "
                    f"\"{quoted_text}\"]\n\nTheir reply: {text}")

    files: list[str] = []
    answer = ask_cfo(question, verbose=False, system_suffix=WHATSAPP_STYLE,
                     history=history, files_out=files, company_id=company_id)
    _trim(history)
    reply(sender, answer or "I didn't catch that — try asking again.", company_id)

    # If the agent produced a report file, send it as a WhatsApp attachment.
    for path in files:
        try:
            send_document(sender, path)
            log_message(sender, "out", f"[report file: {os.path.basename(path)}]",
                        company_id, "document")
        except Exception as e:
            reply(sender, f"(I made your report but couldn't attach it: {e})", company_id)


def _handle_image(sender: str, media_id: str, company_id: int) -> None:
    content, mime = download_media(media_id)
    ext = ".png" if "png" in mime else (".pdf" if "pdf" in mime else ".jpg")
    path = UPLOADS / f"{media_id}{ext}"
    path.write_bytes(content)

    receipt = read_receipt(path)
    _, transaction_id = save_receipt(receipt, company_id)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            result = categorize_transaction(cur, transaction_id, company_id)
    finally:
        conn.close()

    # The vendor comes off an attacker-controllable photo, so neutralise it
    # (flatten to one short line) before it goes anywhere near the AI or a reply.
    vendor = sanitize_untrusted(receipt.vendor) or "(unnamed)"

    lines = [f"Filed: *{vendor}* — {receipt.currency} {receipt.total_amount:.2f}"]
    if receipt.date:
        lines.append(f"Date: {receipt.date}")
    if result["outcome"] == "sorted":
        lines.append(f"Category: {result['category']} (auto-sorted)")
    else:
        lines.append(f"Needs your review — suggested: {result.get('suggested_category')}")
    message = "\n".join(lines)

    # Remember what we just filed so the owner's follow-up ("what was that?",
    # "what category should it be?") has context. The vendor is sanitised above.
    history = CONVERSATIONS.setdefault(sender, [])
    history.append({"role": "user", "content": f"[The owner sent a photo of a receipt; "
                    f"the shop name read as: {vendor}.]"})
    history.append({"role": "assistant", "content": message})
    _trim(history)

    reply(sender, message, company_id)
