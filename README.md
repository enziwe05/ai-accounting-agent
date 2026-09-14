# AI Accounting Agent

**An AI bookkeeping assistant for small businesses.** The owner does two things —
uploads documents and asks questions — and the system does the rest: reading,
matching, categorizing, reporting.

It **prepares** books; it does not file taxes or move money. A human (the owner or
their accountant) reviews and signs off before anything is treated as final. That
boundary is kept in every feature.

> **Status:** early build. **Phases 1–2 are done** — document reading (tested on real
> receipts) and the MySQL storage schema. Phases 3–8 are planned — see the roadmap below.

## What it will do

- **Read** — a photographed receipt, invoice, or bank statement → structured fields
  (vendor, amount, date, VAT, line items) via Claude's vision, returned as a
  *validated* object, never loose text.
- **Check** — compare each new document against everything on file: is it a
  duplicate? When a statement arrives, which lines have no matching document?
- **Sort** — categorize each transaction with a **deterministic rules engine**
  (plain code). Only when no rule matches does it ask the model for a *suggestion* —
  and that lands in a review queue for a human, never applied automatically.
- **Report** — a live spend breakdown and P&L, rebuilt as documents arrive.
- **Ask the CFO** — a tool-calling agent that answers questions in plain English
  ("what did I spend on fuel last month?") and can produce a report on request.

Two upload rhythms: **daily** (receipts/invoices, via web or WhatsApp photo) and
**occasional** (the bank statement — the monthly reconciliation event).

## Why you can trust it

These are non-negotiable and enforced in code, not just intention:

1. **Categorization runs in code, not model judgement.** A rules engine decides;
   the model is a fallback suggestion into a review queue, never auto-applied.
2. **Nothing is auto-filed, submitted, or sent externally without human approval** —
   including anything bound for the tax authority or an accountant.
3. **Full card/account numbers are never stored.** Last four digits only, if that.
4. **Every extracted figure links back to its source document** — the photo or PDF
   it came from. That link is the audit trail.
5. **Structured output, validated in code.** Every model response that returns data
   is checked against a schema; we don't trust a prompt to guarantee clean output.
6. Secrets (API keys, database credentials, tokens) live in environment variables —
   never committed, never hardcoded.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI |
| LLM | Anthropic Claude (`claude-opus-4-8`) — vision for reading, tool-calling for the agent |
| Database | MySQL (transactions ⋈ documents ⋈ categories ⋈ rules) |
| File storage | Cloudflare R2 / Backblaze B2 (the DB stores the key, not the bytes) |
| Reports | pandas + WeasyPrint/reportlab (PDF) + openpyxl (XLSX) |
| Messaging | Meta WhatsApp Cloud API (built last) |
| Frontend | FastAPI + server-rendered HTML |

## Roadmap (build order)

1. ✅ **Document reading, standalone** — `read_receipt.py`. Image/PDF in → validated
   `Receipt` out. Tested on real hand-held till receipts.
2. ✅ **Storage schema** — `schema.sql` + `init_db.py`. Seven MySQL tables
   (documents, transactions, categories, category_rules, statements,
   statement_lines, review_queue) with foreign keys; money as exact DECIMAL.
3. ⬜ Categorization as code (rules engine first, model fallback → review queue).
4. ⬜ Statement upload & reconciliation (matches statement lines to documents).
5. ⬜ Reports on demand.
6. ⬜ The AI CFO agent (tool-calling loop over the database).
7. ⬜ Web dashboard (FastAPI + HTML; real login added here).
8. ⬜ WhatsApp (last — develop via ngrok, then an always-on host).

## Try Phase 1

Requires Python 3.11+ and an Anthropic API key.

```bash
git clone https://github.com/enziwe05/ai-accounting-agent.git
cd ai-accounting-agent
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt

# add your key
copy .env.example .env        # then edit .env and paste your ANTHROPIC_API_KEY

python read_receipt.py path/to/a-receipt.jpg
```

Example output:

```
Extracted (validated) result:
  Vendor : Drama CPT
  Date   : 2026-09-19
  Total  : ZAR 2849.00
  VAT    : ZAR 337.83
  Source : receipt.png
```

When a field is unreadable, it is left empty rather than guessed — the human confirms
it against the source photo.

## Not accounting or tax advice

This system prepares a draft set of books for human review. Verify figures, VAT
treatment, and anything you file with a qualified professional.
