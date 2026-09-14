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
    "You are the AI CFO for a South African small business. You answer the owner's "
    "questions about their books using the tools provided. Money is in South African "
    "Rand (ZAR). Be clear, concise and practical; show the reasoning across categories "
    "when it helps, not just a bare number. You READ and REPORT ONLY — you never change "
    "the books, file anything, or move money. If asked to do something that changes the "
    "books, explain that a human must approve it via the review queue. When the owner "
    "wants a report file, use generate_report and tell them where it was saved."
)


def ask_cfo(question: str, max_turns: int = 8, verbose: bool = True) -> str:
    """Run the agentic loop for one question and return the final answer."""
    messages = [{"role": "user", "content": question}]

    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
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

        # end_turn (or anything else): return the text
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
