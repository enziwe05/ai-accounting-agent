"""
End-to-end: read a bank-statement PDF, store it, and reconcile it.

    read_statement -> store_statement -> reconcile_statement

Usage:
    python process_statement.py path\\to\\statement.pdf
"""

import sys
from pathlib import Path

from reconcile import reconcile_statement
from statement_reader import read_statement, store_statement


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python process_statement.py <path-to-statement.pdf>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    print(f"Reading {path.name} (this can take a moment for a long statement) ...")
    extraction = read_statement(path)
    print(f"  Bank: {extraction.bank}  Account: ****{extraction.account_last4}")
    print(f"  Period: {extraction.period_start} to {extraction.period_end}")
    print(f"  Opening: {extraction.opening_balance}  Closing: {extraction.closing_balance}")
    print(f"  Lines read: {len(extraction.lines)}")

    statement_id = store_statement(extraction, path)
    print(f"  Stored as statement #{statement_id}")

    print("\nReconciling spending against documents on file ...")
    result = reconcile_statement(statement_id)
    print(f"  Debit (spending) lines: {result['debit_lines']}")
    print(f"  Matched to a document:  {result['matched']}")
    print(f"  NO document on file:    {result['unmatched_count']}")

    if result["unmatched"]:
        print("\nSpending with no matching document (needs attention):")
        for line in result["unmatched"][:15]:
            print(f"  {line['line_date']}  {line['description'][:45]:45}  {line['amount']}")
        if result["unmatched_count"] > 15:
            print(f"  ... and {result['unmatched_count'] - 15} more")


if __name__ == "__main__":
    main()
