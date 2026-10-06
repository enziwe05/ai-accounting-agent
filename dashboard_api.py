"""
Dashboard read API — lightweight endpoints for the Incwadzi SaaS web app.

Protected by a shared API key in the X-Api-Key request header. All responses
are JSON. Read-only — no writes live here.
"""

import os
from datetime import date

from fastapi import APIRouter, Header, HTTPException

from db import db_cursor

router = APIRouter(prefix="/api")

DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")


def _check_key(x_api_key: str) -> None:
    if not DASHBOARD_API_KEY:
        raise HTTPException(status_code=503, detail="Dashboard API not configured — set DASHBOARD_API_KEY")
    if x_api_key != DASHBOARD_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.get("/summary")
def summary(x_api_key: str = Header(...)):
    _check_key(x_api_key)

    today = date.today()
    month_start = today.replace(day=1).isoformat()

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN c.kind='income'  THEN t.amount ELSE 0 END), 0) AS income,
                COALESCE(SUM(CASE WHEN c.kind='expense' THEN t.amount ELSE 0 END), 0) AS expenses
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.txn_date >= %s
            """,
            [month_start],
        )
        row = cur.fetchone()
        income = float(row["income"])
        expenses = float(row["expenses"])

        cur.execute(
            "SELECT COUNT(*) AS cnt FROM transactions WHERE txn_date >= %s",
            [month_start],
        )
        txn_count = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM review_queue WHERE status='pending'")
        pending = cur.fetchone()["cnt"]

        cur.execute(
            """
            SELECT COALESCE(SUM(total_amount), 0) AS total, COUNT(*) AS cnt
            FROM invoices WHERE status IN ('sent','draft')
            """
        )
        inv = cur.fetchone()

    return {
        "month": today.strftime("%B %Y"),
        "income": income,
        "expenses": expenses,
        "net_profit": round(income - expenses, 2),
        "transaction_count": txn_count,
        "pending_review": pending,
        "outstanding_invoices_amount": float(inv["total"]),
        "outstanding_invoices_count": inv["cnt"],
        "currency": "ZAR",
    }


@router.get("/transactions")
def recent_transactions(
    limit: int = 10,
    status: str = "all",
    search: str = "",
    x_api_key: str = Header(...),
):
    _check_key(x_api_key)
    safe_limit = min(limit, 100)

    clauses: list[str] = []
    params: list = []

    if status == "pending":
        clauses.append("t.status = 'pending'")
    elif status == "sorted":
        clauses.append("t.status = 'sorted'")

    if search.strip():
        clauses.append("(t.description LIKE %s OR c.name LIKE %s)")
        s = f"%{search.strip()[:100]}%"
        params.extend([s, s])

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(safe_limit)

    with db_cursor() as cur:
        cur.execute(
            f"""
            SELECT t.id, t.txn_date, t.description, t.amount, t.status,
                   c.name AS category, c.kind,
                   d.doc_type AS source
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            LEFT JOIN documents d  ON d.id = t.document_id
            {where}
            ORDER BY t.created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()

    return [
        {
            "id": r["id"],
            "date": r["txn_date"].isoformat() if r["txn_date"] else None,
            "description": r["description"] or "",
            "amount": float(r["amount"]),
            "category": r["category"] or "Uncategorized",
            "kind": r["kind"] or "expense",
            "status": r["status"],
            "source": r["source"] or "receipt",
        }
        for r in rows
    ]


@router.get("/documents")
def documents_list(x_api_key: str = Header(...)):
    _check_key(x_api_key)

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT id, doc_type, vendor, total_amount, doc_date,
                   currency, vat_amount, paid_by_last4, uploaded_by, status, created_at
            FROM documents
            ORDER BY created_at DESC
            LIMIT 100
            """,
        )
        rows = cur.fetchall()

    return [
        {
            "id": r["id"],
            "type": r["doc_type"] or "receipt",
            "vendor": r["vendor"] or "Unknown vendor",
            "amount": float(r["total_amount"]) if r["total_amount"] else None,
            "date": r["doc_date"].isoformat() if r["doc_date"] else None,
            "currency": r["currency"],
            "vat_amount": float(r["vat_amount"]) if r["vat_amount"] else None,
            "paid_by_last4": r["paid_by_last4"],
            "uploaded_by": r["uploaded_by"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


@router.post("/cfo/ask")
async def cfo_ask(body: dict, x_api_key: str = Header(...)):
    _check_key(x_api_key)
    question = str(body.get("question", "")).strip()[:2000]
    history = list(body.get("history", []))
    if not question:
        raise HTTPException(status_code=400, detail="question required")

    try:
        import asyncio
        from cfo_agent import ask_cfo

        def _call() -> str:
            return ask_cfo(
                question,
                max_turns=8,
                verbose=False,
                system_suffix=(
                    "Your reply will be displayed in a web chat interface. "
                    "Use plain text only — no markdown, no ** or ## symbols. "
                    "Be concise and conversational."
                ),
                history=history,
            )

        response = await asyncio.to_thread(_call)
        return {"response": response, "history": history}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/invoices/outstanding")
def outstanding_invoices(x_api_key: str = Header(...)):
    _check_key(x_api_key)

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT i.id, i.invoice_number, i.issue_date, i.due_date,
                   i.total_amount, i.status, cu.name AS customer
            FROM invoices i
            JOIN customers cu ON cu.id = i.customer_id
            WHERE i.status IN ('sent','draft')
            ORDER BY i.due_date ASC
            LIMIT 20
            """,
        )
        rows = cur.fetchall()

    return [
        {
            "id": r["id"],
            "invoice_number": r["invoice_number"],
            "customer": r["customer"],
            "issue_date": r["issue_date"].isoformat() if r["issue_date"] else None,
            "due_date": r["due_date"].isoformat() if r["due_date"] else None,
            "amount": float(r["total_amount"]),
            "status": r["status"],
        }
        for r in rows
    ]


@router.get("/categories/spend")
def category_spend(x_api_key: str = Header(...)):
    _check_key(x_api_key)

    today = date.today()
    month_start = today.replace(day=1).isoformat()

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT c.name AS category, SUM(t.amount) AS total
            FROM transactions t
            JOIN categories c ON c.id = t.category_id
            WHERE c.kind = 'expense' AND t.txn_date >= %s
            GROUP BY c.name
            ORDER BY total DESC
            LIMIT 10
            """,
            [month_start],
        )
        rows = cur.fetchall()

    total = sum(float(r["total"]) for r in rows)
    return [
        {
            "category": r["category"],
            "amount": float(r["total"]),
            "percent": round(float(r["total"]) / total * 100, 1) if total > 0 else 0,
        }
        for r in rows
    ]


@router.get("/summary/monthly")
def monthly_summary(months: int = 6, x_api_key: str = Header(...)):
    """Last N months of income vs expenses for trend charts."""
    _check_key(x_api_key)
    safe_months = min(max(months, 2), 24)

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
                DATE_FORMAT(t.txn_date, '%%Y-%%m') AS month_key,
                DATE_FORMAT(t.txn_date, '%%b %%Y')  AS month_label,
                COALESCE(SUM(CASE WHEN c.kind='income'  THEN t.amount ELSE 0 END), 0) AS income,
                COALESCE(SUM(CASE WHEN c.kind='expense' THEN t.amount ELSE 0 END), 0) AS expenses
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.txn_date >= DATE_SUB(CURDATE(), INTERVAL %s MONTH)
              AND t.txn_date IS NOT NULL
            GROUP BY month_key, month_label
            ORDER BY month_key ASC
            """,
            [safe_months],
        )
        rows = cur.fetchall()

    return [
        {
            "month": r["month_key"],
            "label": r["month_label"],
            "income": float(r["income"]),
            "expenses": float(r["expenses"]),
        }
        for r in rows
    ]


@router.get("/pending")
def pending_items(x_api_key: str = Header(...)):
    _check_key(x_api_key)

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT rq.id, rq.reason, rq.suggestion_note, rq.created_at,
                   t.description AS txn_desc, t.amount AS txn_amount,
                   c.name AS suggested_category
            FROM review_queue rq
            LEFT JOIN transactions t   ON t.id  = rq.transaction_id
            LEFT JOIN categories c     ON c.id  = rq.suggested_category_id
            WHERE rq.status = 'pending'
            ORDER BY rq.created_at DESC
            LIMIT 20
            """,
        )
        rows = cur.fetchall()

    return [
        {
            "id": r["id"],
            "reason": r["reason"],
            "note": r["suggestion_note"] or "",
            "description": r["txn_desc"] or "",
            "amount": float(r["txn_amount"]) if r["txn_amount"] else None,
            "suggested_category": r["suggested_category"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]
