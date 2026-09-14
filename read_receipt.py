"""
Phase 1 — Document reading, standalone.

Feed this script a real photographed receipt (or a PDF) and it returns a
*validated* Pydantic object with the vendor, amount, date and VAT. No database,
no web server, no WhatsApp — just: image in, structured data out.

Usage:
    python read_receipt.py path\\to\\slip.jpg

Non-negotiables honoured here (see CLAUDE.md):
  #5  We do NOT trust the model to return clean JSON. We use the API's
      structured-output mode (messages.parse + a Pydantic schema), so the
      response is validated in code, not by hoping the prompt was obeyed.
  #6  One Anthropic client, created once and reused.
  #7  The API key is read from an environment variable, never hardcoded.
  #4  Each result keeps a link back to its source file (source_file), which
      becomes the audit trail once the database exists in Phase 2.
"""

import base64
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from llm import MODEL, client  # the one shared Anthropic client (#6)

# South African product: home currency is the Rand. Detected currency on the
# document always wins; this is only the fallback when a slip doesn't show one.
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "ZAR")


# --- The shape of the data we want back -------------------------------------
# This class IS the contract. The API is told to fill in exactly these fields,
# and the SDK refuses to hand us anything that doesn't match. If a field can be
# missing on a real receipt (VAT often is), we make it optional.

class Receipt(BaseModel):
    vendor: str = Field(description="The shop or supplier name, exactly as printed.")
    date: str = Field(
        description="Purchase date in ISO format YYYY-MM-DD. "
        "Empty string if it cannot be read."
    )
    total_amount: float = Field(description="The grand total actually paid.")
    vat_amount: float | None = Field(
        default=None,
        description="The VAT/tax amount if the receipt shows one, otherwise null.",
    )
    currency: str = Field(
        default=DEFAULT_CURRENCY,
        description="ISO currency code. Almost always ZAR (South African Rand, 'R'). "
        "Read the symbol/code printed on the slip; only fall back to ZAR if none "
        "is shown.",
    )
    # Not extracted by the model — we set this ourselves so every figure can be
    # traced back to the file it came from (non-negotiable #4).
    source_file: str = Field(default="", description="Path of the source image/PDF.")


SYSTEM_PROMPT = (
    "You read photographed receipts, invoices and slips for a South African "
    "small-business bookkeeping assistant. Amounts are in South African Rand (ZAR, "
    "symbol 'R') and VAT is 15%. Extract only what is actually printed on the "
    "document. Do not guess or invent figures. If a value is unreadable, leave it "
    "empty (for text) or null (for numbers) rather than making something up. "
    "Amounts are the numeric totals only, without the currency symbol."
)

# Map file extensions to the media type the API expects for image blocks.
IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def build_source_block(path: Path) -> dict:
    """Turn a local file into the content block the API reads.

    Images become an 'image' block; PDFs become a 'document' block. Either way
    we base64-encode the raw bytes and send them inline.
    """
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": data,
            },
        }

    media_type = IMAGE_MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise ValueError(
            f"Unsupported file type '{suffix}'. "
            f"Use one of: {', '.join(IMAGE_MEDIA_TYPES)} or .pdf"
        )

    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def read_receipt(path: Path) -> Receipt:
    """Send one file to Claude and return a validated Receipt object."""
    source_block = build_source_block(path)

    response = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    source_block,
                    {
                        "type": "text",
                        "text": "Extract the receipt details into the required "
                        "structure.",
                    },
                ],
            }
        ],
        output_format=Receipt,
    )

    # The model can decline (safety). Structured output isn't guaranteed then.
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to process this document.")

    receipt = response.parsed_output
    if receipt is None:
        raise RuntimeError(
            "No structured data came back — the document may be unreadable."
        )

    # Stamp the source file onto the result (the model didn't set this).
    receipt.source_file = str(path)
    return receipt


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python read_receipt.py <path-to-receipt-image-or-pdf>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    print(f"Reading {path.name} ...\n")
    receipt = read_receipt(path)

    # Show the validated result in plain English...
    print("Extracted (validated) result:")
    print(f"  Vendor : {receipt.vendor}")
    print(f"  Date   : {receipt.date or '(unreadable)'}")
    print(f"  Total  : {receipt.currency} {receipt.total_amount:.2f}")
    vat = f"{receipt.currency} {receipt.vat_amount:.2f}" if receipt.vat_amount is not None else "(none shown)"
    print(f"  VAT    : {vat}")
    print(f"  Source : {receipt.source_file}")

    # ...and the raw JSON, so you can see exactly what the schema captured.
    print("\nAs JSON:")
    print(receipt.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
