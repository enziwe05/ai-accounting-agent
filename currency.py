"""
Per-company currency handling.

Two env vars drive it (set per company in its env file):
  DEFAULT_CURRENCY — the fallback ISO code used when a document/message shows no
                     currency of its own. Also what new invoices/expenses default to.
  CURRENCIES       — every currency the business actually uses, comma-separated
                     (e.g. "SZL,ZAR"). All of these are treated as NORMAL, so the
                     reader never flags one as foreign and the CFO agent formats
                     each correctly.

Example: M26 is an Eswatini business that trades in both Emalangeni (SZL) and Rand
(ZAR) — which are pegged 1:1 — so its env sets DEFAULT_CURRENCY=SZL, CURRENCIES=SZL,ZAR.
A South-Africa-only client just leaves the defaults (ZAR).
"""
import os

DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "ZAR").strip().upper() or "ZAR"

CURRENCIES = [c.strip().upper() for c in os.getenv("CURRENCIES", DEFAULT_CURRENCY).split(",") if c.strip()]
if DEFAULT_CURRENCY not in CURRENCIES:
    CURRENCIES.insert(0, DEFAULT_CURRENCY)

# name, symbol, and what it looks like printed on a slip. Extend as new markets open.
_INFO = {
    "ZAR": ("South African Rand", "R", "'R' or 'ZAR'"),
    "SZL": ("Eswatini Lilangeni/Emalangeni", "E", "'E', 'L', 'SZL', 'Lilangeni' or 'Emalangeni'"),
    "USD": ("US Dollar", "$", "'$' or 'USD'"),
    "GBP": ("British Pound", "£", "'£' or 'GBP'"),
    "EUR": ("Euro", "€", "'€' or 'EUR'"),
    "BWP": ("Botswana Pula", "P", "'P' or 'BWP'"),
    "NAD": ("Namibian Dollar", "N$", "'N$' or 'NAD'"),
}

# Currency groups that trade 1:1 (safe to total together, no conversion needed).
_PEGGED = [{"ZAR", "SZL", "NAD", "LSL"}]  # Common Monetary Area


def name(code: str) -> str:
    return _INFO.get((code or "").upper(), (code, code, code))[0]


def symbol(code: str) -> str:
    return _INFO.get((code or "").upper(), ("", code, ""))[1] or (code or "")


def format_amount(amount, code: str | None = None) -> str:
    """Format like the region does: 'R2 849.00', 'E500.00'. Space thousands separator."""
    code = (code or DEFAULT_CURRENCY).upper()
    body = f"{float(amount):,.2f}".replace(",", " ")
    return f"{symbol(code)}{body}"


def _pegged_note() -> str:
    for group in _PEGGED:
        used = [c for c in CURRENCIES if c in group]
        if len(used) > 1:
            return f" {' and '.join(name(c) for c in used)} trade one-to-one, so amounts in either are equivalent."
    return ""


def reader_guidance() -> str:
    """One sentence for the receipt-reader system prompt."""
    if len(CURRENCIES) == 1:
        c = CURRENCIES[0]
        return (f"Amounts are usually in {name(c)} ({c}, symbol '{symbol(c)}'). "
                f"Read the currency actually printed on the slip and record its ISO code; "
                f"only fall back to {c} if none is shown.")
    listed = "; ".join(f"{name(c)} ({c}, shown as {_INFO.get(c, ('','',c))[2]})" for c in CURRENCIES)
    return (f"This business uses more than one currency: {listed}.{_pegged_note()} "
            f"All of these are NORMAL for this business — never treat one as foreign or odd. "
            f"Read whichever currency is actually printed on each slip and record its ISO code "
            f"(e.g. ZAR or SZL); only fall back to {DEFAULT_CURRENCY} if no currency is shown.")


def agent_guidance() -> str:
    """One sentence for the CFO-agent system prompt."""
    example = format_amount(2849, CURRENCIES[0])
    if len(CURRENCIES) == 1:
        return f"Money is in {name(CURRENCIES[0])}; write it like '{example}'."
    examples = ", ".join(f"'{format_amount(500, c)}' for {c}" for c in CURRENCIES)
    return (f"This owner deals in more than one currency: {', '.join(name(c) for c in CURRENCIES)}."
            f"{_pegged_note()} Show each amount with its own currency's symbol ({examples}), "
            f"matching whatever the transaction is stored in. Never treat "
            f"{', '.join(c for c in CURRENCIES if c != DEFAULT_CURRENCY)} as foreign or unusual — "
            f"it is normal money for this business.")
