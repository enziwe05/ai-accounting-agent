"""
Accounts Receivable — invoices and the money customers owe.

The owner raises an invoice; that invoice is a *receivable* (a promise of money),
not income yet. Income is recognised the day the customer actually pays (cash
basis — the simplest, most honest view for a small business). So:

  - create_invoice   -> a row in `invoices` only. Nothing hits the P&L.
  - mark_invoice_paid -> writes a Sales income transaction into the books, so the
                         money now shows up in reports, traceable back to the
                         invoice (non-negotiable #4).

The pure helpers (invoice_view_status, split_vat) are plain functions with no
database, so they are easy to unit-test.
"""

import json
from datetime import date, timedelta

from currency import DEFAULT_CURRENCY
from db import get_connection

# South Africa: VAT is 15%. Only applied when the owner says the price includes
# it — we never invent VAT on an amount that didn't mention it.
VAT_RATE = 0.15

# Paid invoices are booked as income against this category (kind 'income'). It is
# created on first use if a business's category list doesn't already have it.
SALES_CATEGORY = "Sales"


# --- Pure helpers (no database — unit-tested) --------------------------------

def split_vat(total: float, vat_inclusive: bool) -> tuple[float, float]:
    """Split a total into (subtotal, vat).

    vat_inclusive=True  -> the total already contains 15% VAT; back it out.
    vat_inclusive=False -> no VAT on this invoice; subtotal == total, vat == 0.
    """
    total = round(float(total), 2)
    if not vat_inclusive:
        return total, 0.0
    subtotal = round(total / (1 + VAT_RATE), 2)
    vat = round(total - subtotal, 2)
    return subtotal, vat


def invoice_view_status(status: str, due_date, today: date) -> str:
    """The status to *show* the owner, working out overdue from the due date.

    'paid', 'cancelled' and 'draft' are returned unchanged. A 'sent' invoice is
    'overdue' once its due date has passed, otherwise 'outstanding' (owed, not
    yet due). due_date may be a date or None.
    """
    if status in ("paid", "cancelled", "draft"):
        return status
    if due_date and due_date < today:
        return "overdue"
    return "outstanding"


def _clean_line_items(line_items) -> list[dict] | None:
    """Normalise line items to a plain list of dicts, or None if there are none."""
    if not line_items:
        return None
    cleaned = []
    for item in line_items:
        if not isinstance(item, dict):
            continue
        cleaned.append({
            "description": str(item.get("description", "")).strip(),
            "quantity": item.get("quantity"),
            "unit_price": item.get("unit_price"),
            "amount": item.get("amount"),
        })
    return cleaned or None


def _line_items_total(line_items: list[dict]) -> float:
    """Best-effort total of a set of line items (amount, else qty*unit_price)."""
    total = 0.0
    for item in line_items:
        amount = item.get("amount")
        if amount is None and item.get("quantity") and item.get("unit_price"):
            amount = float(item["quantity"]) * float(item["unit_price"])
        total += float(amount or 0)
    return round(total, 2)


# --- Database layer ----------------------------------------------------------

def _get_or_create_customer(cur, name: str, email=None, phone=None) -> int:
    """Find a customer by name (case-insensitive), or create one. Returns its id."""
    name = (name or "").strip()
    cur.execute("SELECT id FROM customers WHERE LOWER(name) = LOWER(%s)", (name,))
    row = cur.fetchone()
    if row:
        # Fill in contact details if we now have them and didn't before.
        if email or phone:
            cur.execute(
                "UPDATE customers SET email = COALESCE(email, %s), "
                "phone = COALESCE(phone, %s) WHERE id = %s",
                (email, phone, row["id"]),
            )
        return row["id"]
    cur.execute(
        "INSERT INTO customers (name, email, phone) VALUES (%s, %s, %s)",
        (name, email, phone),
    )
    return cur.lastrowid


def _sales_category_id(cur) -> int:
    """The 'Sales' income category id, created once if the list doesn't have it."""
    cur.execute(
        "SELECT id FROM categories WHERE name = %s AND kind = 'income'",
        (SALES_CATEGORY,),
    )
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(
        "INSERT INTO categories (name, kind) VALUES (%s, 'income')",
        (SALES_CATEGORY,),
    )
    return cur.lastrowid


def create_invoice(customer_name: str, amount: float | None = None,
                   description: str | None = None, line_items=None,
                   vat_inclusive: bool = False, due_in_days: int = 14,
                   issue_date: str | None = None, due_date: str | None = None,
                   email: str | None = None, phone: str | None = None,
                   notes: str | None = None, currency: str | None = None) -> dict:
    """Raise a new invoice for a customer. Returns the saved invoice as a dict.

    Give either an `amount` (the total to charge) or `line_items` — if only line
    items are given, the amount is summed from them. `vat_inclusive` decides
    whether 15% VAT is backed out of the total. Nothing touches the P&L here; an
    unpaid invoice is only a receivable until mark_invoice_paid is called.
    """
    currency = (currency or DEFAULT_CURRENCY).upper()
    items = _clean_line_items(line_items)
    if amount is None:
        if not items:
            raise ValueError("Give an amount to invoice, or some line items.")
        amount = _line_items_total(items)
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("Invoice amount must be greater than zero.")

    subtotal, vat = split_vat(amount, vat_inclusive)

    issue = date.fromisoformat(issue_date) if issue_date else date.today()
    if due_date:
        due = date.fromisoformat(due_date)
    else:
        due = issue + timedelta(days=int(due_in_days))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            customer_id = _get_or_create_customer(cur, customer_name, email, phone)

            # Insert first with a temporary unique number, then set the real
            # human-facing number from the row's own id (guaranteed unique).
            cur.execute(
                """INSERT INTO invoices
                     (invoice_number, customer_id, issue_date, due_date,
                      subtotal_amount, vat_amount, total_amount, currency,
                      line_items, notes, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'sent')""",
                (
                    f"PENDING-{issue.isoformat()}-{customer_id}",
                    customer_id, issue, due, subtotal, vat, amount, currency,
                    json.dumps(items, ensure_ascii=False) if items else None,
                    (notes or description),
                ),
            )
            invoice_id = cur.lastrowid
            number = f"INV-{invoice_id:04d}"
            cur.execute("UPDATE invoices SET invoice_number = %s WHERE id = %s",
                        (number, invoice_id))
    finally:
        conn.close()

    return get_invoice(invoice_id=invoice_id)


def get_invoice(invoice_id: int | None = None,
                invoice_number: str | None = None) -> dict | None:
    """Fetch one invoice (with customer + parsed line items), or None."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if invoice_id:
                cur.execute(_INVOICE_SELECT + " WHERE i.id = %s", (int(invoice_id),))
            elif invoice_number:
                cur.execute(_INVOICE_SELECT + " WHERE i.invoice_number = %s",
                            (invoice_number,))
            else:
                return None
            row = cur.fetchone()
    finally:
        conn.close()
    return _shape_invoice(row) if row else None


def list_invoices(status: str | None = None, only_unpaid: bool = False,
                  only_overdue: bool = False, customer: str | None = None,
                  limit: int = 50) -> list[dict]:
    """List invoices (newest first), each with a computed view status.

    only_unpaid keeps everything still owed (sent/overdue); only_overdue keeps
    just the ones past their due date. `status` filters on the raw stored status.
    """
    sql = _INVOICE_SELECT + " WHERE 1=1"
    params: list = []
    if status:
        sql += " AND i.status = %s"; params.append(status)
    if only_unpaid or only_overdue:
        sql += " AND i.status = 'sent'"
    if only_overdue:
        sql += " AND i.due_date IS NOT NULL AND i.due_date < %s"
        params.append(date.today())
    if customer:
        sql += " AND cu.name LIKE %s"; params.append(f"%{customer}%")
    sql += " ORDER BY i.id DESC LIMIT %s"; params.append(int(limit))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [_shape_invoice(r) for r in rows]


def outstanding_summary() -> dict:
    """Totals for what the business is owed: outstanding vs overdue."""
    today = date.today()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                     COUNT(*)                                        AS owed_count,
                     COALESCE(SUM(total_amount), 0)                  AS owed_total,
                     SUM(due_date IS NOT NULL AND due_date < %s)     AS overdue_count,
                     COALESCE(SUM(CASE WHEN due_date IS NOT NULL AND due_date < %s
                                       THEN total_amount ELSE 0 END), 0) AS overdue_total
                   FROM invoices
                   WHERE status = 'sent'""",
                (today, today),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return {
        "currency": "ZAR",
        "owed_count": int(row["owed_count"] or 0),
        "owed_total": float(row["owed_total"] or 0),
        "overdue_count": int(row["overdue_count"] or 0),
        "overdue_total": float(row["overdue_total"] or 0),
    }


def mark_invoice_paid(invoice_id: int | None = None,
                      invoice_number: str | None = None,
                      paid_date: str | None = None) -> dict:
    """Mark an invoice paid and book the money as Sales income.

    This is the moment income is recognised: a documents row (the invoice as the
    source, #4) plus a 'sorted' Sales transaction, so the money shows up in the
    P&L. Identify the invoice by id or by number (e.g. 'INV-0007').
    """
    invoice = get_invoice(invoice_id=invoice_id, invoice_number=invoice_number)
    if not invoice:
        return {"done": False, "reason": "invoice_not_found"}
    if invoice["status"] == "paid":
        return {"done": False, "reason": "already_paid",
                "invoice_number": invoice["invoice_number"],
                "paid_date": invoice["paid_date"]}
    if invoice["status"] == "cancelled":
        return {"done": False, "reason": "invoice_cancelled",
                "invoice_number": invoice["invoice_number"]}

    when = paid_date or date.today().isoformat()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            category_id = _sales_category_id(cur)

            # The invoice itself is the source document for this income (#4).
            cur.execute(
                """INSERT INTO documents
                     (source_key, doc_type, vendor, doc_date, total_amount,
                      vat_amount, currency, raw_extraction, status)
                   VALUES (%s, 'invoice', %s, %s, %s, %s, %s, %s, 'confirmed')""",
                (
                    f"invoice:{invoice['invoice_number']}",
                    invoice["customer"], when, invoice["total_amount"],
                    invoice["vat_amount"], invoice["currency"],
                    json.dumps(invoice, ensure_ascii=False, default=str),
                ),
            )
            document_id = cur.lastrowid

            cur.execute(
                """INSERT INTO transactions
                     (document_id, txn_date, description, amount, vat_amount,
                      currency, category_id, source, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'invoice', 'sorted')""",
                (
                    document_id, when,
                    f"Invoice {invoice['invoice_number']} — {invoice['customer']}",
                    invoice["total_amount"], invoice["vat_amount"],
                    invoice["currency"], category_id,
                ),
            )
            txn_id = cur.lastrowid

            cur.execute(
                "UPDATE invoices SET status='paid', paid_date=%s, "
                "income_transaction_id=%s WHERE id=%s",
                (when, txn_id, invoice["id"]),
            )
    finally:
        conn.close()

    return {"done": True, "invoice_number": invoice["invoice_number"],
            "customer": invoice["customer"], "amount": invoice["total_amount"],
            "currency": invoice["currency"], "paid_date": when,
            "transaction_id": txn_id,
            "note": "Marked paid and booked as Sales income."}


# --- Row shaping -------------------------------------------------------------

_INVOICE_SELECT = """
    SELECT i.id, i.invoice_number, i.issue_date, i.due_date,
           i.subtotal_amount, i.vat_amount, i.total_amount, i.currency,
           i.line_items, i.notes, i.status, i.paid_date, i.income_transaction_id,
           cu.name AS customer, cu.email AS customer_email, cu.phone AS customer_phone
    FROM invoices i
    JOIN customers cu ON cu.id = i.customer_id
"""


def _shape_invoice(row: dict) -> dict:
    """Turn a raw DB row into a clean dict, with line items parsed and a
    human-facing 'view_status' (outstanding / overdue / paid / ...)."""
    line_items = []
    raw = row.get("line_items")
    if raw:
        try:
            line_items = json.loads(raw)
        except (ValueError, TypeError):
            line_items = []
    row["line_items"] = line_items
    row["view_status"] = invoice_view_status(row["status"], row.get("due_date"),
                                             date.today())
    # Coerce Decimals to float for clean JSON when the agent serialises this.
    for key in ("subtotal_amount", "vat_amount", "total_amount"):
        if row.get(key) is not None:
            row[key] = float(row[key])
    return row
