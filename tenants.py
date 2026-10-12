"""
Tenant routing — map an inbound WhatsApp sender to the company it belongs to.

With a single shared bot number, the sender's phone number tells us whose books
a message is for. A number that isn't registered to any company is treated as
unauthorized (the bot stays silent), which also preserves the old security
behaviour: strangers who find the number get nothing.
"""

from typing import Optional

from db import db_cursor
from security import normalize_number


def company_for_number(wa_number: str) -> Optional[int]:
    """Return the company_id that owns this WhatsApp sender, or None if the
    number isn't registered to any company (ignore the message)."""
    digits = normalize_number(wa_number)
    if not digits:
        return None
    with db_cursor() as cur:
        cur.execute(
            "SELECT company_id FROM company_numbers WHERE wa_number = %s LIMIT 1",
            [digits],
        )
        row = cur.fetchone()
        return row["company_id"] if row else None


def company_status(company_id: int) -> Optional[str]:
    """Return the subscription status of a company ('active', 'past_due',
    'suspended', 'cancelled'), or None if it doesn't exist."""
    with db_cursor() as cur:
        cur.execute("SELECT status FROM companies WHERE id = %s", [company_id])
        row = cur.fetchone()
        return row["status"] if row else None


def is_company_active(company_id: int) -> bool:
    """Active or in the grace 'past_due' window → the bot still works.
    Suspended/cancelled → paused."""
    return company_status(company_id) in ("active", "past_due")
