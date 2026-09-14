"""
End-to-end: read a receipt, store it, and categorize it.

This ties the phases together:
    read_receipt (Phase 1)  ->  save_receipt (Phase 2)  ->  categorize (Phase 3)

Usage:
    python process_receipt.py path\\to\\receipt.jpg
"""

import sys
from pathlib import Path

from categorize import categorize_transaction
from db import get_connection
from read_receipt import read_receipt
from store import save_receipt


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python process_receipt.py <path-to-receipt>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    # 1) Read
    print(f"Reading {path.name} ...")
    receipt = read_receipt(path)
    print(f"  Vendor: {receipt.vendor} | Total: {receipt.currency} {receipt.total_amount:.2f}")

    # 2) Store
    document_id, transaction_id = save_receipt(receipt)
    print(f"  Saved as document #{document_id}, transaction #{transaction_id}")

    # 3) Categorize
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            result = categorize_transaction(cur, transaction_id)
    finally:
        conn.close()

    print("\nCategorization:")
    if result["outcome"] == "sorted":
        print(f"  SORTED as '{result['category']}' — by {result['by']}")
    else:
        print(f"  NEEDS REVIEW — {result['by']}")
        print(f"  Suggested: {result['suggested_category']} ({result['confidence']})")
        print(f"  Reason: {result['reason']}")
        print("  (This suggestion is waiting in the review queue for your approval.)")


if __name__ == "__main__":
    main()
