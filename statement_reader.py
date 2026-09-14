"""
Phase 4 (part 1) — Read a bank-statement PDF into a validated structure.

Works for ANY South African bank: we send the (decrypted) PDF to Claude and get
back a schema-validated StatementExtraction — bank, account last-4 only, period,
balances, and every line with an explicit debit/credit direction and ISO date.
No bank-specific layout is hardcoded.
"""

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from db import get_connection
from llm import MODEL, client
from pdf_utils import chunk_indices, open_decrypted, pages_to_b64

# How many statement pages to read per API call. Small enough that the JSON for
# one chunk always fits in the output limit; large statements just take more calls.
PAGES_PER_CHUNK = 3


# --- The shape of a parsed statement ----------------------------------------

class StatementLine(BaseModel):
    date: str = Field(description="Transaction date as ISO YYYY-MM-DD. Infer the "
                      "year from the statement period if the line shows only day+month.")
    description: str = Field(description="The transaction description / merchant, as printed.")
    amount: float = Field(description="The amount as a POSITIVE number, no currency symbol.")
    direction: Literal["debit", "credit"] = Field(
        description="'credit' if money came IN (often marked 'Cr'), 'debit' if money went OUT."
    )
    balance: Optional[float] = Field(default=None, description="Running balance as a number, or null.")


class StatementExtraction(BaseModel):
    bank: str = Field(description="The bank name, e.g. FNB, Absa, Standard Bank, Nedbank, Capitec.")
    account_last4: str = Field(description="ONLY the last 4 digits of the account number. "
                               "Never return the full account number.")
    period_start: str = Field(description="Statement period start, ISO YYYY-MM-DD.")
    period_end: str = Field(description="Statement period end, ISO YYYY-MM-DD.")
    opening_balance: Optional[float] = Field(default=None)
    closing_balance: Optional[float] = Field(default=None)
    currency: str = Field(default="ZAR")
    lines: list[StatementLine] = Field(description="Every transaction line on these pages.")


class StatementLines(BaseModel):
    """Used for continuation pages — lines only, no header."""
    lines: list[StatementLine] = Field(description="Every transaction line on these pages.")


SYSTEM_PROMPT = (
    "You read South African bank-statement PDFs for a bookkeeping assistant. "
    "Extract the account details and EVERY transaction line. Rules: amounts are "
    "positive numbers without the 'R' symbol; a line marked 'Cr' is a credit "
    "(money in), otherwise it is a debit (money out); dates that show only day and "
    "month take their year from the statement period; output ISO dates. For the "
    "account number, return ONLY the last four digits — never the full number. Do "
    "not invent lines or figures; transcribe exactly what is printed."
)


def _parse_chunk(b64_pdf: str, output_format, instruction: str):
    """One API call for one page-chunk, returning the validated model."""
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "document",
                     "source": {"type": "base64", "media_type": "application/pdf", "data": b64_pdf}},
                    {"type": "text", "text": instruction},
                ],
            }
        ],
        output_format=output_format,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to process this statement.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("A statement chunk was too long even at "
                           f"{PAGES_PER_CHUNK} pages — lower PAGES_PER_CHUNK.")
    if response.parsed_output is None:
        raise RuntimeError("No structured data came back from a statement chunk.")
    return response.parsed_output


def read_statement(path: Path, password: str = "") -> StatementExtraction:
    """Read a statement PDF in page-chunks and return one combined extraction."""
    reader = open_decrypted(path, password=password)
    chunks = chunk_indices(len(reader.pages), PAGES_PER_CHUNK)

    # First chunk: header + its lines.
    first = _parse_chunk(
        pages_to_b64(reader, chunks[0]),
        StatementExtraction,
        "Extract the account header and every transaction line on these pages "
        "into the required structure.",
    )

    all_lines = list(first.lines)

    # Remaining chunks: lines only, told the period so the year is right.
    for indices in chunks[1:]:
        cont = _parse_chunk(
            pages_to_b64(reader, indices),
            StatementLines,
            f"These are continuation pages of a {first.bank} statement for the "
            f"period {first.period_start} to {first.period_end}. Extract every "
            f"transaction line on these pages (lines only, no header).",
        )
        all_lines.extend(cont.lines)

    first.lines = all_lines
    return first


def store_statement(extraction: StatementExtraction, source_path: Path) -> int:
    """Save the statement + all its lines. Returns the statement id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # The statement file itself is a 'statement' document.
            cur.execute(
                """INSERT INTO documents
                     (source_key, doc_type, vendor, doc_date, currency, raw_extraction, status)
                   VALUES (%s, 'statement', %s, %s, %s, %s, 'read')""",
                (str(source_path), extraction.bank, extraction.period_end,
                 extraction.currency, extraction.model_dump_json()),
            )
            document_id = cur.lastrowid

            cur.execute(
                """INSERT INTO statements
                     (document_id, account_ref, period_start, period_end,
                      opening_balance, closing_balance, currency)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (document_id, f"****{extraction.account_last4}",  # masked (#3)
                 extraction.period_start, extraction.period_end,
                 extraction.opening_balance, extraction.closing_balance, extraction.currency),
            )
            statement_id = cur.lastrowid

            for line in extraction.lines:
                cur.execute(
                    """INSERT INTO statement_lines
                         (statement_id, line_date, description, amount, direction,
                          running_balance, match_status)
                       VALUES (%s, %s, %s, %s, %s, %s, 'unmatched')""",
                    (statement_id, line.date or None, line.description,
                     line.amount, line.direction, line.balance),
                )

        return statement_id
    finally:
        conn.close()
