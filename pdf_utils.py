"""
PDF helpers — open a (possibly encrypted) bank-statement PDF and hand out
selected pages as base64, so a long statement can be read in page-chunks.

Most South African bank statements are encrypted but open with an EMPTY password
(permissions encryption, not a real passphrase). We handle that automatically and
raise a clear error if a real password is actually required.
"""

import base64
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def open_decrypted(path: Path, password: str = "") -> PdfReader:
    """Return a PdfReader that's ready to read (decrypted if needed)."""
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        # decrypt() returns 0 on failure, 1/2 on success (user/owner password).
        if reader.decrypt(password) == 0:
            raise ValueError(
                f"'{path.name}' is password-protected. Re-run with the PDF password."
            )
    return reader


def pages_to_b64(reader: PdfReader, indices: list[int]) -> str:
    """Write the given page indices to a fresh PDF and return it base64-encoded."""
    writer = PdfWriter()
    for i in indices:
        writer.add_page(reader.pages[i])
    buffer = BytesIO()
    writer.write(buffer)
    return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")


def chunk_indices(total_pages: int, chunk_size: int) -> list[list[int]]:
    """Split page indices into groups, e.g. 9 pages / 3 -> [[0,1,2],[3,4,5],[6,7,8]]."""
    return [list(range(i, min(i + chunk_size, total_pages)))
            for i in range(0, total_pages, chunk_size)]
