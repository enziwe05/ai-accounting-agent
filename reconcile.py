"""
Phase 4 (part 2) — Reconciliation.

For each spending line on the bank statement (a debit), we look for a document
already on file that explains it — matched by amount, then date proximity, then
vendor similarity. Lines with no match are the "no document" items the owner
must see: money that left the account with no receipt/invoice recorded.

`match_line` is a pure function (no DB, no network) so it's easy to unit-test.
"""

from dataclasses import dataclass
from datetime import date

from db import get_connection


@dataclass
class Candidate:
    """A document/transaction already on file that a statement line might match."""
    transaction_id: int
    vendor: str | None
    txn_date: date | None
    amount: float


def _amounts_equal(a: float, b: float) -> bool:
    return abs(round(a, 2) - round(b, 2)) < 0.005


def _vendor_in_description(vendor: str | None, description: str) -> bool:
    """True if a meaningful word from the vendor appears in the line description."""
    if not vendor:
        return False
    desc = description.lower()
    for word in vendor.lower().split():
        if len(word) >= 3 and word in desc:
            return True
    return False


def match_line(line_date: date | None, line_amount: float, description: str,
               candidates: list[Candidate], days_tol: int = 4) -> Candidate | None:
    """Return the best matching candidate for a statement line, or None.

    Rules, in order:
      1. Amount must match to the cent.
      2. If a date is available, it must be within `days_tol` days.
      3. Among the survivors, prefer one whose vendor appears in the description;
         otherwise take the one closest in date. Ties / ambiguity with no vendor
         signal and multiple candidates -> no confident match (return None).
    """
    # 1) amount match
    matches = [c for c in candidates if _amounts_equal(c.amount, line_amount)]
    if not matches:
        return None

    # 2) date window: a candidate that HAS a date must be within tolerance;
    #    a candidate with no date can't be excluded on date grounds.
    if line_date is not None:
        matches = [c for c in matches
                   if c.txn_date is None
                   or abs((c.txn_date - line_date).days) <= days_tol]
        if not matches:
            return None

    # 3) prefer a vendor-name match
    vendor_hits = [c for c in matches if _vendor_in_description(c.vendor, description)]
    if len(vendor_hits) == 1:
        return vendor_hits[0]
    if len(vendor_hits) > 1:
        matches = vendor_hits  # narrow to vendor hits, then decide by date below

    if len(matches) == 1:
        return matches[0]

    # Several matches, no single vendor signal: take the STRICTLY closest date.
    # If the closest date is tied between candidates, it's ambiguous -> None.
    if line_date is not None and all(c.txn_date is not None for c in matches):
        dists = [abs((c.txn_date - line_date).days) for c in matches]
        closest = [c for c, d in zip(matches, dists) if d == min(dists)]
        if len(closest) == 1:
            return closest[0]
    return None


# --- Database glue -----------------------------------------------------------

def _load_candidates(cur) -> list[Candidate]:
    """Transactions that came from receipts/invoices (money the owner has a doc for)."""
    cur.execute(
        """SELECT t.id, d.vendor, t.txn_date, t.amount
           FROM transactions t
           JOIN documents d ON d.id = t.document_id
           WHERE d.doc_type IN ('receipt', 'invoice')"""
    )
    return [
        Candidate(transaction_id=r["id"], vendor=r["vendor"],
                  txn_date=r["txn_date"], amount=float(r["amount"]))
        for r in cur.fetchall()
    ]


def reconcile_statement(statement_id: int) -> dict:
    """Match every DEBIT line of a statement against documents on file.

    Marks each line matched/unmatched and returns a summary with the unmatched
    (i.e. 'no document') spending lines.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            candidates = _load_candidates(cur)

            cur.execute(
                """SELECT id, line_date, description, amount
                   FROM statement_lines
                   WHERE statement_id = %s AND direction = 'debit'""",
                (statement_id,),
            )
            debit_lines = cur.fetchall()

            matched, unmatched = 0, []
            for line in debit_lines:
                match = match_line(line["line_date"], float(line["amount"]),
                                   line["description"] or "", candidates)
                if match is not None:
                    cur.execute(
                        """UPDATE statement_lines
                           SET match_status='matched', matched_transaction_id=%s
                           WHERE id=%s""",
                        (match.transaction_id, line["id"]),
                    )
                    matched += 1
                else:
                    cur.execute(
                        "UPDATE statement_lines SET match_status='unmatched' WHERE id=%s",
                        (line["id"],),
                    )
                    unmatched.append(line)

        return {
            "debit_lines": len(debit_lines),
            "matched": matched,
            "unmatched_count": len(unmatched),
            "unmatched": unmatched,
        }
    finally:
        conn.close()

