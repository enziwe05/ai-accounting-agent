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

**Phase 3 — Categorization as code. ✅ DONE & verified (2026-09-14).**
- `categorize.py` — pure `match_rule(vendor, description, rules)` rules engine
  (contains/equals/regex, priority order, case-insensitive) + `categorize_transaction()`.
  Rule match → apply + status 'sorted'. No match → LLM `suggest_category()` (structured
  output) → row in `review_queue` (pending), status 'needs_review', category NOT applied (#1/#2).
- `data/default_categories.json` (13) + `data/default_rules.json` (~35 South African
  example rules — editable starter template per business);
  `seed_db.py` loads them (idempotent).
- `store.py` — `save_receipt(receipt)` → documents + transactions rows.
- `process_receipt.py` — end-to-end read → store → categorize.
- `llm.py` — the single shared Anthropic client (#6); read_receipt.py now imports it.
- `test_categorize.py` — 9 pytest tests for the matcher, all green.
Verified: Drama receipt → review queue (suggestion not applied); Engen → sorted to Fuel by rule.

**Phase 4 — Statement upload & reconciliation. ✅ DONE & verified (2026-09-14).**
- `pdf_utils.py` — open_decrypted (handles empty-password encryption on bank PDFs),
  pages_to_b64, chunk_indices.
- `statement_reader.py` — reads ANY SA bank PDF in page-chunks (PAGES_PER_CHUNK=3) via
  Claude document blocks → validated StatementExtraction (bank, account_last4 ONLY (#3),
  period, opening/closing, lines with debit/credit direction + ISO dates). Stitches chunks.
  store_statement → documents(type=statement) + statements(account masked ****last4) +
  statement_lines. Long statements need chunking (single call truncates at max_tokens).
- `reconcile.py` — pure `match_line()` (amount to the cent, date window ±4d, vendor-in-
  description tiebreak, ambiguous→None) + `reconcile_statement()` matches debit lines to
  receipt/invoice transactions, marks statement_lines matched/unmatched. Unmatched debit =
  the "no document" gap.
- `process_statement.py` — read → store → reconcile → report unmatched.
- `test_reconcile.py` — 7 pytest tests (16 total across the project), all green.
- Added `direction` col to statement_lines. Deps: pypdf, cryptography.
Verified on a real encrypted 9-page FNB PDF: 443 lines, opening+credits-debits=closing to
the cent (no lines dropped/dup), account masked to ****5379. Reader is bank-agnostic;
tested on FNB, untested on Absa/Standard/Nedbank/Capitec (need samples).

**Phase 5 — Reports on demand. ✅ DONE & verified (2026-09-14).**
- `reports.py` — pure `summarize(rows)` → Report (income/expense/net, spend_by_category
  + income_by_category sorted desc, uncategorized_count). `build_report()` fetches from
  transactions⋈categories (optional period filter); `render_text()`; `export_xlsx()` via
  openpyxl; `generate_report(period_start, period_end, out_path)` = the ONE entry point the
  dashboard button AND the CFO agent's generate_report tool will both call.
- `test_reports.py` — 5 pytest tests (21 total, all green). Dep: openpyxl.
- Reports write to reports/*.xlsx (gitignored).
Verified: seeded 10 sample SA txns (uploaded_by='sample-data') → Feb report income 45,000,
expenses 23,369, net 21,631, correct category breakdown; xlsx exported.
Next: Phase 6, the AI CFO agent (tool-calling loop; generate_report is one of its tools).

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
- **Market: South Africa.** Home currency ZAR (South African Rand, "R"); VAT is 15%.
  `DEFAULT_CURRENCY` env sets the fallback; detected currency on a document still wins.
- **Commercial product** — standalone SaaS to be sold to South African SMEs. NOT tied to
  m26 (only shares the local MAMP MySQL *server*; the `bookkeeper` database is separate).
  Multi-tenant support (per-business data/rules/login) is a known future need, added around
  Phase 7 (auth) — not built yet; the current core is single-tenant.
- **Bank-statement reader must handle ALL South African banks** (FNB, Absa, Standard Bank,
  Nedbank, Capitec, TymeBank, Investec, etc.) generically from a PDF — not one bank hardcoded.

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
