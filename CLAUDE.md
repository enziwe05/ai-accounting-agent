# AI Bookkeeping Assistant — project state

An SME owner does two things — uploads documents and asks questions — and the
system does the rest: reading, matching, categorizing, reporting. It **prepares**
books; a human reviews and signs off before anything is final. That boundary is
kept in every feature.

## Current phase

**Phase 1 — Document reading, standalone. ✅ DONE & verified (2026-09-14).**
One script (`read_receipt.py`), no database yet: feed it a real photographed
slip, get back a validated Pydantic object (vendor, amount, date, VAT).
Tested on two real (low-res, hand-held) till receipts — vendor/total/VAT/currency
extracted correctly; on an unreadable date it returned "" instead of guessing
(non-negotiable #5 confirmed working). Next: Phase 2, storage schema.

Note: the sample folder also has a multi-line *account statement* (ZAR) — that's
the Phase 4 reconciliation document type, not a single-receipt input.

**Phase 2 — Storage schema. ✅ DONE & verified (2026-09-14).**
`schema.sql` (7 tables: categories, category_rules, documents, transactions,
statements, statement_lines, review_queue), `db.py` (PyMySQL connection from env),
`init_db.py` (creates DB + tables, safe to re-run). Runs against **MAMP MySQL**
(localhost:3306, root/root locally). All 9 foreign keys verified; write/read round-trip
works. Money is DECIMAL(12,2), not float. Full card/acct numbers never stored (#3).
Next: Phase 3, categorization as code (rules engine → LLM fallback → review queue).

## Non-negotiables (do not compromise for convenience)

1. Categorization & reconciliation matching run in **code, not model judgement**
   (deterministic rules engine). LLM category suggestions only when no rule
   matches, and always into a review queue — never auto-applied.
2. Nothing is auto-filed, auto-submitted, or sent externally without explicit
   human approval (LLM category suggestions, unsolicited WhatsApp messages,
   anything bound for SARS/an accountant).
3. Never store full card or account numbers. Last four digits only, if that.
4. Every extracted figure keeps a link back to its source document (the audit
   trail). In Phase 1 this is `Receipt.source_file`.
5. Defensive parsing on every LLM call that returns structured data. We use the
   API's structured-output mode (`messages.parse` + Pydantic), so validity is
   enforced in code — not by trusting the prompt. Do not fall back to parsing
   free text with prompt-only guarantees.
6. One Anthropic client instance, reused — never a new client per call.
7. Real authentication before this runs anywhere public. Secrets (Anthropic key,
   later MySQL creds and WhatsApp tokens) live in environment variables, never
   committed, never hardcoded.

## Decisions made

- **Language/model:** Python 3.11+ (dev machine has 3.14). Model `claude-opus-4-8`
  via the Anthropic vision API.
- **Structured output over hand-parsing:** `client.messages.parse(output_format=Receipt)`
  guarantees a validated `Receipt` (satisfies non-negotiable #5).
- **Secrets:** `ANTHROPIC_API_KEY` from the environment (optionally via a local,
  git-ignored `.env` loaded by python-dotenv).
- **Currency default SZL** (Eswatini lilangeni); ZAR appears on South African slips.

## Build order (do not skip ahead)

1. Document reading, standalone.  ← **we are here**
2. Storage schema (MySQL: documents, transactions, categories, category_rules,
   statements, statement_lines, review_queue). Files to R2/B2; DB stores the key.
3. Categorization as code (rules engine first, LLM fallback → review queue).
4. Statement upload & reconciliation (ask the user for their real bank format).
5. Reports on demand (one function the dashboard button and CFO agent both call).
6. AI CFO agent (tool-calling loop over the DB).
7. Web dashboard (FastAPI + HTML, read-only first; add real login here).
8. WhatsApp (last; ngrok for dev, always-on host before real users).

## Working style

- Keep this file current — phase, decisions, non-negotiables.
- Complete and test one phase against **real** documents before the next.
- Write tests for the rules engine and reconciliation matcher (pure functions).
- Commit after each completed phase, not each file.
- If LLM output breaks parsing, fix it in code, not with another prompt plea.

## Phase 1 — how to run

```
cd C:\Projects\bookkeeper
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then edit .env and paste your real key
python read_receipt.py path\to\a-real-slip.jpg
```
