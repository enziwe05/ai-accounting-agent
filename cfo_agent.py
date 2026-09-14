"""
Phase 6 — The AI CFO agent.

A tool-calling agent that answers plain-English questions about the books by
calling real functions against the database, then can produce a report file on
request. It READS and REPORTS — it never changes the books. Anything that would
change them still goes through the review gates in the other modules.

The loop is the standard agentic pattern: ask Claude -> if it wants a tool, run
the tool and feed the result back -> repeat until it stops asking (end_turn).

Usage:
    python cfo_agent.py "what did I spend on stock in February?"
    python cfo_agent.py            # interactive; type questions, 'quit' to exit
"""

import json
import sys
from pathlib import Path

from db import get_connection
from llm import MODEL, client
from reports import build_report, generate_report


def _json(obj) -> str:
    """JSON with Decimals/dates coerced to strings so the model can read them."""
    return json.dumps(obj, default=str, ensure_ascii=False)


# --- Tool implementations (all read-only) -----------------------------------

def tool_query_transactions(category=None, vendor=None, period_start=None,
                            period_end=None, status=None, limit=50) -> str:
    sql = """SELECT t.txn_date, d.vendor, t.description, t.amount, t.vat_amount,
                    t.currency, c.name AS category, t.status
             FROM transactions t
             JOIN documents d ON d.id = t.document_id
             LEFT JOIN categories c ON c.id = t.category_id
             WHERE 1=1"""
    params = []
    if category:
        sql += " AND c.name LIKE %s"; params.append(f"%{category}%")
    if vendor:
        sql += " AND d.vendor LIKE %s"; params.append(f"%{vendor}%")
    if period_start:
        sql += " AND t.txn_date >= %s"; params.append(period_start)
    if period_end:
        sql += " AND t.txn_date <= %s"; params.append(period_end)
    if status:
        sql += " AND t.status = %s"; params.append(status)
    sql += " ORDER BY t.txn_date LIMIT %s"; params.append(int(limit))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    return _json({"count": len(rows), "transactions": rows})


def tool_get_spend_summary(period_start=None, period_end=None) -> str:
    report = build_report(period_start, period_end)
    return _json({
        "currency": report.currency,
        "income_total": report.income_total,
        "expense_total": report.expense_total,
        "net_profit": report.net_profit,
        "spend_by_category": [vars(c) for c in report.spend_by_category],
        "income_by_category": [vars(c) for c in report.income_by_category],
        "transaction_count": report.transaction_count,
        "uncategorized_count": report.uncategorized_count,
    })


def tool_get_document_details(vendor=None, document_id=None) -> str:
    """Return a slip's itemised lines (what was bought) plus its review status.

    Look up by document_id, or by vendor name (most recent match wins).
    """
    sql = """SELECT d.id, d.vendor, d.doc_date, d.total_amount, d.vat_amount,
                    d.currency, d.raw_extraction, t.status AS txn_status
             FROM documents d
             LEFT JOIN transactions t ON t.document_id = d.id
             WHERE 1=1"""
    params = []
    if document_id:
        sql += " AND d.id = %s"; params.append(int(document_id))
    if vendor:
        sql += " AND d.vendor LIKE %s"; params.append(f"%{vendor}%")
    sql += " ORDER BY d.id DESC LIMIT 1"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return _json({"found": False, "note": "No matching document found."})

    raw = row.pop("raw_extraction", None)
    line_items = []
    if raw:
        try:
            line_items = json.loads(raw).get("line_items", [])
        except (ValueError, TypeError):
            line_items = []
    row["line_items"] = line_items
    row["found"] = True
    return _json(row)


def tool_set_category(category, vendor=None, document_id=None, transaction_id=None) -> str:
    """Apply a category the OWNER has chosen to a transaction, and approve its review.

    This is the one action that changes the books. It only ever runs because the
    owner explicitly told us the category (that is their human approval, non-neg #2),
    never on the agent's own initiative. Identify the transaction by transaction_id,
    document_id, or vendor (a pending 'needs_review' one is preferred).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # 1) Resolve the category name -> id (exact, else unambiguous partial).
            cur.execute("SELECT id, name FROM categories")
            cats = cur.fetchall()
            want = (category or "").strip().lower()
            exact = [c for c in cats if c["name"].lower() == want]
            partial = [c for c in cats if want and want in c["name"].lower()]
            chosen = exact[0] if exact else (partial[0] if len(partial) == 1 else None)
            if chosen is None:
                return _json({
                    "done": False,
                    "reason": "ambiguous_or_unknown_category",
                    "you_said": category,
                    "valid_categories": [c["name"] for c in cats],
                    "note": "Ask the owner to pick one of the valid categories.",
                })

            # 2) Find the transaction to update.
            if transaction_id:
                cur.execute("SELECT id FROM transactions WHERE id=%s", (int(transaction_id),))
            elif document_id:
                cur.execute("SELECT id FROM transactions WHERE document_id=%s ORDER BY id DESC LIMIT 1",
                            (int(document_id),))
            elif vendor:
                # Prefer one still awaiting review; fall back to the most recent.
                cur.execute(
                    """SELECT t.id FROM transactions t JOIN documents d ON d.id=t.document_id
                       WHERE d.vendor LIKE %s
                       ORDER BY (t.status='needs_review') DESC, t.id DESC LIMIT 1""",
                    (f"%{vendor}%",),
                )
            else:
                return _json({"done": False, "reason": "no_transaction_identified",
                              "note": "Need a vendor, document_id, or transaction_id."})
            trow = cur.fetchone()
            if not trow:
                return _json({"done": False, "reason": "transaction_not_found"})
            txn_id = trow["id"]

            # 3) Apply it and approve any pending review for this transaction.
            cur.execute("UPDATE transactions SET category_id=%s, status='sorted' WHERE id=%s",
                        (chosen["id"], txn_id))
            cur.execute("UPDATE review_queue SET status='approved' "
                        "WHERE transaction_id=%s AND status='pending'", (txn_id,))
    finally:
        conn.close()

    return _json({"done": True, "transaction_id": txn_id, "category": chosen["name"],
                  "status": "sorted", "note": "Category applied and review approved."})


def tool_get_review_queue() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT rq.id, rq.reason, rq.suggestion_note, rq.status,
                          t.amount, t.currency, d.vendor
                   FROM review_queue rq
                   LEFT JOIN transactions t ON t.id = rq.transaction_id
                   LEFT JOIN documents d ON d.id = t.document_id
                   WHERE rq.status = 'pending'
                   ORDER BY rq.id"""
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return _json({"pending_count": len(rows), "items": rows})


def tool_generate_report(period_start=None, period_end=None) -> str:
    label = period_start[:7] if period_start else "all"
    out = Path("reports") / f"report_{label}.xlsx"
    report, text = generate_report(period_start, period_end, out_path=out)
    return _json({"saved_file": str(out), "summary": text})


DISPATCH = {
    "query_transactions": tool_query_transactions,
    "get_spend_summary": tool_get_spend_summary,
    "get_document_details": tool_get_document_details,
    "set_category": tool_set_category,
    "get_review_queue": tool_get_review_queue,
    "generate_report": tool_generate_report,
}


# --- Tool schemas the model sees --------------------------------------------

_PERIOD = {
    "period_start": {"type": "string", "description": "Start date, ISO YYYY-MM-DD (optional)."},
    "period_end": {"type": "string", "description": "End date, ISO YYYY-MM-DD (optional)."},
}

TOOLS = [
    {
        "name": "query_transactions",
        "description": "List individual transactions, optionally filtered by category, "
                       "vendor, date range, or status (sorted/needs_review/no_document).",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Category name contains (optional)."},
                "vendor": {"type": "string", "description": "Vendor name contains (optional)."},
                **_PERIOD,
                "status": {"type": "string", "description": "sorted | needs_review | no_document (optional)."},
                "limit": {"type": "integer", "description": "Max rows (default 50)."},
            },
        },
    },
    {
        "name": "get_spend_summary",
        "description": "Get the profit & loss and spend-by-category totals for a period "
                       "(or all time if no dates given).",
        "input_schema": {"type": "object", "properties": {**_PERIOD}},
    },
    {
        "name": "get_document_details",
        "description": "Get the itemised lines of a single slip/receipt — i.e. WHAT WAS "
                       "BOUGHT — plus its review status. Use this whenever the owner asks "
                       "what was on a receipt or what they bought somewhere. Look up by "
                       "vendor name or document id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vendor": {"type": "string", "description": "Vendor/shop name contains (optional)."},
                "document_id": {"type": "integer", "description": "The document id (optional)."},
            },
        },
    },
    {
        "name": "set_category",
        "description": "Apply the category the OWNER has chosen to a transaction and approve "
                       "its review. Use this ONLY when the owner has clearly told you which "
                       "category to use (e.g. 'file Drama under entertainment', 'yes, that's "
                       "travel'). Never call it on your own guess. Identify the transaction by "
                       "vendor, document_id, or transaction_id. If the category name doesn't "
                       "match, the tool returns the valid list — show it and ask the owner.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "The category name the owner chose."},
                "vendor": {"type": "string", "description": "Vendor/shop name to find the transaction (optional)."},
                "document_id": {"type": "integer", "description": "Document id (optional)."},
                "transaction_id": {"type": "integer", "description": "Transaction id (optional)."},
            },
            "required": ["category"],
        },
    },
    {
        "name": "get_review_queue",
        "description": "List items awaiting the owner's approval (LLM category suggestions, "
                       "missing documents, possible duplicates).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "generate_report",
        "description": "Produce a downloadable Excel report (P&L + spend breakdown) for a "
                       "period and return the file path.",
        "input_schema": {"type": "object", "properties": {**_PERIOD}},
    },
]

SYSTEM_PROMPT = (
    "You are a friendly bookkeeping helper for a South African small business owner. "
    "Talk like a helpful person, not an accountant: warm, encouraging, and in simple "
    "everyday English that anyone can understand. Keep answers short. Avoid jargon — if "
    "you must use a bookkeeping word, explain it in a few plain words. Money is in South "
    "African Rand; write it like 'R2 849.00'. Sound like a real, helpful person — warm "
    "but not over-the-top. Go very easy on emoji: at most one, only when it genuinely "
    "fits, and usually none.\n\n"
    "Use the tools to look up real numbers — never guess. If someone asks what they "
    "bought on a slip, use get_document_details to read the actual items. If you can't "
    "find something, say so plainly and suggest what to check.\n\n"
    "ALWAYS speak up about reviews: whenever a transaction's status is 'needs_review' (or "
    "there are pending items in the review queue that relate to the question), tell the "
    "owner clearly that it's waiting for them to confirm, say what needs deciding (usually "
    "the category), and mention they can approve it. Don't hide it or treat it as final.\n\n"
    "You mostly READ and REPORT — you do not move money or invent entries. The ONE change "
    "you may make is applying a category when the OWNER clearly tells you which one to use "
    "(e.g. 'file Drama under entertainment', 'yes, that's travel'): call set_category, then "
    "confirm it's done in one friendly line. That is the owner approving it themselves. "
    "Never categorise on your own guess, and for anything else that would change the books, "
    "gently explain a human has to approve it first. When the owner wants a report file, use "
    "generate_report and tell them where it saved."
)


def ask_cfo(question: str, max_turns: int = 8, verbose: bool = True,
            system_suffix: str = "", history: list | None = None) -> str:
    """Run the agentic loop for one question and return the final answer.

    system_suffix lets a caller (e.g. WhatsApp) tweak the reply style without
    changing the base prompt.

    history, if given, is the running message list from earlier turns. It is
    read AND updated in place (this message and the agent's reply are appended),
    so a caller can keep it and get real back-and-forth memory. Pass nothing for
    a one-off question with no memory (the CLI does this).
    """
    system = SYSTEM_PROMPT + (("\n\n" + system_suffix) if system_suffix else "")
    messages = history if history is not None else []
    messages.append({"role": "user", "content": question})

    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    if verbose:
                        print(f"   [calling {block.name}({_json(block.input)})]")
                    func = DISPATCH.get(block.name)
                    try:
                        output = func(**block.input) if func else f"Unknown tool {block.name}"
                    except Exception as e:  # tool failure -> tell the model, don't crash
                        output = _json({"error": str(e)})
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    })
            messages.append({"role": "user", "content": results})
            continue

        # end_turn (or anything else): keep the reply in history, return the text
        messages.append({"role": "assistant", "content": response.content})
        return "".join(b.text for b in response.content if b.type == "text").strip()

    return "(stopped after the maximum number of steps)"


def main() -> None:
    if len(sys.argv) >= 2:
        question = " ".join(sys.argv[1:])
        print(ask_cfo(question))
        return

    print("AI CFO — ask a question about your books (type 'quit' to exit).")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in {"quit", "exit", ""}:
            break
        print(ask_cfo(question))


if __name__ == "__main__":
    main()
