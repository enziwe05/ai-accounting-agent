"""
Save a read Receipt into the database.

A single receipt becomes one `documents` row (the source file + extracted fields,
plus the full raw JSON for the audit trail, #4) and one `transactions` row (the
money movement). The transaction starts as 'needs_review' until categorization
sorts it.
"""

from db import get_connection
from read_receipt import Receipt


def save_receipt(receipt: Receipt) -> tuple[int, int]:
    """Insert a document + transaction for this receipt.

    Returns (document_id, transaction_id).
    """
    # Empty date string -> NULL (we never store a guessed date).
    doc_date = receipt.date or None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents
                     (source_key, doc_type, vendor, doc_date, total_amount,
                      vat_amount, currency, raw_extraction, status)
                   VALUES (%s, 'receipt', %s, %s, %s, %s, %s, %s, 'read')""",
                (
                    receipt.source_file,
                    receipt.vendor,
                    doc_date,
                    receipt.total_amount,
                    receipt.vat_amount,
                    receipt.currency,
                    receipt.model_dump_json(),
                ),
            )
            document_id = cur.lastrowid

            cur.execute(
                """INSERT INTO transactions
                     (document_id, txn_date, description, amount, vat_amount,
                      currency, source, status)
                   VALUES (%s, %s, %s, %s, %s, %s, 'photo', 'needs_review')""",
                (
                    document_id,
                    doc_date,
                    receipt.vendor,  # description defaults to the vendor name
                    receipt.total_amount,
                    receipt.vat_amount,
                    receipt.currency,
                ),
            )
            transaction_id = cur.lastrowid

        return document_id, transaction_id
    finally:
        conn.close()
