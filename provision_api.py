"""
Provisioning & tenant-admin API — called server-side by the Incwadzi web app.

These are the write/control endpoints the web app uses to manage tenants:
  - create a company when someone signs up,
  - register the WhatsApp number(s) that belong to that company (so the one
    shared bot number can route inbound messages to the right books),
  - keep each company's subscription status in sync with PayFast.

Protected by the same shared DASHBOARD_API_KEY (the web app is a trusted
caller). Kept separate from dashboard_api.py because those are read-only.
"""

import os
import re

from fastapi import APIRouter, Header, HTTPException

from db import db_cursor
from seed_db import seed_company

router = APIRouter(prefix="/api/admin")

DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")

VALID_STATUSES = {"active", "past_due", "suspended", "cancelled"}


def _check_key(x_api_key: str) -> None:
    if not DASHBOARD_API_KEY:
        raise HTTPException(status_code=503, detail="Dashboard API not configured — set DASHBOARD_API_KEY")
    if x_api_key != DASHBOARD_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _digits(number: str) -> str:
    return re.sub(r"\D", "", number or "")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "company"


@router.post("/provision-company")
def provision_company(body: dict, x_api_key: str = Header(...)):
    """Create a new tenant company and (optionally) map its WhatsApp number.

    Returns the new company_id, which the web app stores on the user so every
    later dashboard call can be scoped to it.
    """
    _check_key(x_api_key)

    name = (str(body.get("name", "")).strip()[:200]) or "New company"
    wa = _digits(str(body.get("whatsapp_number", "")))
    base = _slugify(name)

    with db_cursor() as cur:
        # Find a unique slug (base, base-2, base-3, ...).
        slug = base
        suffix = 1
        while True:
            cur.execute("SELECT 1 FROM companies WHERE slug = %s", [slug])
            if not cur.fetchone():
                break
            suffix += 1
            slug = f"{base}-{suffix}"

        cur.execute(
            "INSERT INTO companies (name, slug, status) VALUES (%s, %s, 'active')",
            [name, slug],
        )
        company_id = cur.lastrowid

        if wa:
            # Map this sender number to the new company. INSERT IGNORE so a number
            # that is already mapped elsewhere doesn't error (we don't steal it).
            cur.execute(
                "INSERT IGNORE INTO company_numbers (company_id, wa_number) VALUES (%s, %s)",
                [company_id, wa],
            )

    # Give the new tenant its own starter categories + rules so the rules engine
    # works for them from message one. Optional industry pack via "seed_profile".
    # Non-fatal: a company with no rules still functions (everything just goes to
    # the review queue), so a seeding hiccup must not fail provisioning.
    try:
        seed_company(company_id, str(body.get("seed_profile", "")).strip())
    except Exception as e:
        print(f"[provision] could not seed company {company_id}: {e}")

    return {"company_id": company_id, "slug": slug}


@router.post("/register-number")
def register_number(body: dict, x_api_key: str = Header(...)):
    """Attach (or move) a WhatsApp sender number to a company."""
    _check_key(x_api_key)
    company_id = int(body.get("company_id", 0) or 0)
    wa = _digits(str(body.get("whatsapp_number", "")))
    if not company_id or not wa:
        raise HTTPException(status_code=400, detail="company_id and whatsapp_number required")

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO company_numbers (company_id, wa_number) VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE company_id = VALUES(company_id)
            """,
            [company_id, wa],
        )
    return {"ok": True}


@router.post("/company-status")
def set_company_status(body: dict, x_api_key: str = Header(...)):
    """Sync a company's subscription status (called from the web app's PayFast
    webhook). 'suspended'/'cancelled' pauses the bot for that company; 'active'
    resumes it. Data is never touched — only access."""
    _check_key(x_api_key)
    company_id = int(body.get("company_id", 0) or 0)
    status = str(body.get("status", "")).strip()
    if not company_id:
        raise HTTPException(status_code=400, detail="company_id required")
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(VALID_STATUSES)}")

    with db_cursor() as cur:
        cur.execute("UPDATE companies SET status = %s WHERE id = %s", [status, company_id])
    return {"ok": True}
