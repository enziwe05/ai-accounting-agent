"""
Phase 5 — Reports on demand.

One place builds the numbers: a spend breakdown by category and a simple profit
& loss, from categorized transactions. The dashboard button and the AI CFO
agent's `generate_report` tool both call `generate_report()` here — so the books
can never disagree with what the chat says.

`summarize()` is a pure function (rows in, report out) — easy to unit-test. The
database layer only fetches rows; all the arithmetic lives in the pure function.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from db import get_connection


@dataclass
class CategoryTotal:
    category: str
    total: float
    count: int


@dataclass
class Report:
    period_start: Optional[str]
    period_end: Optional[str]
    currency: str
    income_total: float
    expense_total: float
    net_profit: float
    income_by_category: list[CategoryTotal] = field(default_factory=list)
    spend_by_category: list[CategoryTotal] = field(default_factory=list)
    transaction_count: int = 0
    uncategorized_count: int = 0


def summarize(rows: list[dict], currency: str = "ZAR",
              period_start: Optional[str] = None,
              period_end: Optional[str] = None) -> Report:
    """Build a Report from transaction rows.

    Each row: {"amount": float, "kind": "income"|"expense"|None, "category": str|None}.
    Rows with kind None are uncategorized — counted, but kept out of the totals.
    """
    income_by: dict[str, list] = defaultdict(lambda: [0.0, 0])
    spend_by: dict[str, list] = defaultdict(lambda: [0.0, 0])
    income_total = expense_total = 0.0
    uncategorized = 0

    for row in rows:
        amount = float(row["amount"])
        kind = row.get("kind")
        category = row.get("category") or "Uncategorized"

        if kind == "income":
            income_total += amount
            income_by[category][0] += amount
            income_by[category][1] += 1
        elif kind == "expense":
            expense_total += amount
            spend_by[category][0] += amount
            spend_by[category][1] += 1
        else:
            uncategorized += 1

    def to_totals(d: dict) -> list[CategoryTotal]:
        items = [CategoryTotal(cat, round(v[0], 2), v[1]) for cat, v in d.items()]
        return sorted(items, key=lambda c: c.total, reverse=True)

    return Report(
        period_start=period_start,
        period_end=period_end,
        currency=currency,
        income_total=round(income_total, 2),
        expense_total=round(expense_total, 2),
        net_profit=round(income_total - expense_total, 2),
        income_by_category=to_totals(income_by),
        spend_by_category=to_totals(spend_by),
        transaction_count=len(rows),
        uncategorized_count=uncategorized,
    )


# --- Database layer ----------------------------------------------------------

def _fetch_rows(cur, period_start: Optional[str], period_end: Optional[str]) -> list[dict]:
    sql = """SELECT t.amount, c.name AS category, c.kind
             FROM transactions t
             LEFT JOIN categories c ON c.id = t.category_id
             WHERE 1=1"""
    params: list = []
    if period_start:
        sql += " AND t.txn_date >= %s"
        params.append(period_start)
    if period_end:
        sql += " AND t.txn_date <= %s"
        params.append(period_end)
    cur.execute(sql, params)
    return [{"amount": r["amount"], "kind": r["kind"], "category": r["category"]}
            for r in cur.fetchall()]


def build_report(period_start: Optional[str] = None,
                 period_end: Optional[str] = None) -> Report:
    """Fetch rows from the DB and summarize them into a Report."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            rows = _fetch_rows(cur, period_start, period_end)
        return summarize(rows, "ZAR", period_start, period_end)
    finally:
        conn.close()


# --- Rendering ---------------------------------------------------------------

def render_text(report: Report) -> str:
    cur = report.currency
    span = f"{report.period_start or 'start'} to {report.period_end or 'now'}"
    lines = [
        "AI ACCOUNTING AGENT - REPORT",
        f"Period: {span}",
        "",
        f"  Income      {cur} {report.income_total:>12,.2f}",
        f"  Expenses    {cur} {report.expense_total:>12,.2f}",
        f"  -----------------------------------",
        f"  Net profit  {cur} {report.net_profit:>12,.2f}",
        "",
        "Where the money went (spend by category):",
    ]
    if report.spend_by_category:
        for ct in report.spend_by_category:
            lines.append(f"  {ct.category:<26} {cur} {ct.total:>10,.2f}  ({ct.count})")
    else:
        lines.append("  (no categorized expenses yet)")

    if report.income_by_category:
        lines.append("")
        lines.append("Income by category:")
        for ct in report.income_by_category:
            lines.append(f"  {ct.category:<26} {cur} {ct.total:>10,.2f}  ({ct.count})")

    lines.append("")
    lines.append(f"Transactions counted: {report.transaction_count} "
                 f"({report.uncategorized_count} still uncategorized / in review)")
    return "\n".join(lines)


def export_xlsx(report: Report, path: Path) -> Path:
    """Write the report to an .xlsx file the owner can open in Excel."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    ws.append(["AI Accounting Agent - Report"])
    ws.append(["Period", f"{report.period_start or 'start'} to {report.period_end or 'now'}"])
    ws.append(["Currency", report.currency])
    ws.append([])
    ws.append(["Income", report.income_total])
    ws.append(["Expenses", report.expense_total])
    ws.append(["Net profit", report.net_profit])
    ws.append([])
    ws.append(["Spend by category", "Amount", "Count"])
    for ct in report.spend_by_category:
        ws.append([ct.category, ct.total, ct.count])
    if report.income_by_category:
        ws.append([])
        ws.append(["Income by category", "Amount", "Count"])
        for ct in report.income_by_category:
            ws.append([ct.category, ct.total, ct.count])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def generate_report(period_start: Optional[str] = None,
                    period_end: Optional[str] = None,
                    out_path: Optional[Path] = None) -> tuple[Report, str]:
    """The single entry point the dashboard button and the CFO agent both call.

    Returns (report, rendered_text). If out_path is given, also writes an .xlsx.
    """
    report = build_report(period_start, period_end)
    text = render_text(report)
    if out_path is not None:
        export_xlsx(report, Path(out_path))
    return report, text


if __name__ == "__main__":
    import sys

    # Optional YYYY-MM argument to scope to one month, e.g. python reports.py 2026-02
    p_start = p_end = None
    if len(sys.argv) == 2:
        year, month = sys.argv[1].split("-")
        p_start = f"{year}-{month}-01"
        # crude month end; good enough for a report boundary
        p_end = f"{year}-{month}-31"

    out = Path("reports") / f"report_{sys.argv[1] if len(sys.argv) == 2 else 'all'}.xlsx"
    report, text = generate_report(p_start, p_end, out_path=out)
    print(text)
    print(f"\nSaved: {out}")
