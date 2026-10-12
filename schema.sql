-- =============================================================================
-- Phase 2 — Storage schema for the AI bookkeeping assistant.
--
-- Multi-tenant: every data table carries a company_id so one business's books
-- never mix with another's. company_id defaults to 1 (M26, the first company)
-- so any insert that forgets it still lands safely on company 1 during the
-- transition; the code always sets it explicitly. A fresh database created from
-- this file is therefore already isolated — identical in shape to a database
-- that was upgraded by migrations/001_add_company_tenancy.sql.
--
-- Money is DECIMAL(12,2) — exact to the cent. Never FLOAT for money.
-- Full card/account numbers are NEVER stored (non-negotiable #3): last 4 only.
-- Every figure keeps a link back to its source document (non-negotiable #4).
--
-- Safe to run more than once (IF NOT EXISTS everywhere).
-- =============================================================================

-- The database itself is created and selected by init_db.py, using the DB_NAME
-- from the environment (default 'bookkeeper'). Tenancy is by company_id WITHIN
-- one database, so a single shared bot can serve many companies.


-- 0) companies — the tenants. M26 is company 1. -------------------------------
CREATE TABLE IF NOT EXISTS companies (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  name        VARCHAR(200) NOT NULL,
  slug        VARCHAR(100) NOT NULL,
  -- Subscription state, kept in sync from the web app's PayFast webhook.
  status      ENUM('active','past_due','suspended','cancelled') NOT NULL DEFAULT 'active',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_company_slug (slug)
) ENGINE=InnoDB;

-- Seed M26 as company 1 (only if it isn't there yet).
INSERT INTO companies (id, name, slug, status)
SELECT 1, 'M26', 'm26', 'active'
WHERE NOT EXISTS (SELECT 1 FROM companies WHERE id = 1);


-- 0b) company_numbers — maps an inbound WhatsApp sender -> company. ------------
--     On a single shared bot number, the sender's phone tells us whose books a
--     message belongs to. Numbers are stored digits-only.
CREATE TABLE IF NOT EXISTS company_numbers (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  company_id   INT NOT NULL,
  wa_number    VARCHAR(32) NOT NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_cn_company FOREIGN KEY (company_id) REFERENCES companies(id),
  UNIQUE KEY uq_cn_number (wa_number),
  KEY idx_cn_company (company_id)
) ENGINE=InnoDB;


-- 1) categories — the chart of categories (Fuel, Rent, Sales, ...) ------------
--    Unique PER COMPANY: two businesses may each have their own "Fuel".
CREATE TABLE IF NOT EXISTS categories (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  company_id  INT NOT NULL DEFAULT 1,
  name        VARCHAR(100) NOT NULL,
  kind        ENUM('income','expense') NOT NULL DEFAULT 'expense',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_category_company_name (company_id, name),
  KEY idx_cat_company (company_id)
) ENGINE=InnoDB;


-- 2) category_rules — the deterministic rules engine (non-negotiable #1) -------
--    "vendor contains 'Engen' -> category Fuel". Code applies these, not the LLM.
CREATE TABLE IF NOT EXISTS category_rules (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  company_id   INT NOT NULL DEFAULT 1,
  match_field  ENUM('vendor','description') NOT NULL DEFAULT 'vendor',
  match_type   ENUM('contains','equals','regex') NOT NULL DEFAULT 'contains',
  pattern      VARCHAR(255) NOT NULL,
  category_id  INT NOT NULL,
  priority     INT NOT NULL DEFAULT 100,   -- lower number wins when several match
  active       TINYINT(1) NOT NULL DEFAULT 1,
  notes        VARCHAR(255) NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rule_category FOREIGN KEY (category_id) REFERENCES categories(id),
  KEY idx_rule_active (active, priority),
  KEY idx_rule_company (company_id)
) ENGINE=InnoDB;


-- 3) documents — every uploaded receipt / invoice / statement ------------------
--    source_key is where the actual file lives (local path now; R2/B2 key later).
--    raw_extraction keeps the full JSON the reader returned = the audit trail.
CREATE TABLE IF NOT EXISTS documents (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  company_id     INT NOT NULL DEFAULT 1,
  source_key     VARCHAR(500) NOT NULL,          -- file path / storage key (#4)
  doc_type       ENUM('receipt','invoice','statement','cash') NOT NULL DEFAULT 'receipt',
  vendor         VARCHAR(255) NULL,
  doc_date       DATE NULL,                       -- NULL when unreadable (never guessed)
  total_amount   DECIMAL(12,2) NULL,
  vat_amount     DECIMAL(12,2) NULL,
  currency       CHAR(3) NOT NULL DEFAULT 'ZAR',
  paid_by_last4  CHAR(4) NULL,                    -- last 4 digits only, never full (#3)
  uploaded_by    VARCHAR(100) NULL,
  raw_extraction JSON NULL,                       -- full validated reader output
  status         ENUM('read','needs_review','confirmed') NOT NULL DEFAULT 'read',
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_doc_vendor_date (vendor, doc_date),
  KEY idx_doc_type (doc_type),
  KEY idx_doc_company (company_id)
) ENGINE=InnoDB;


-- 4) transactions — one money movement, always tied to a document -------------
--    A receipt = 1 transaction; a statement = many (via statement_lines).
CREATE TABLE IF NOT EXISTS transactions (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  company_id   INT NOT NULL DEFAULT 1,
  document_id  INT NOT NULL,                      -- link back to source (#4)
  txn_date     DATE NULL,
  description  VARCHAR(255) NULL,
  amount       DECIMAL(12,2) NOT NULL,
  vat_amount   DECIMAL(12,2) NULL,
  currency     CHAR(3) NOT NULL DEFAULT 'ZAR',
  category_id  INT NULL,                          -- set by a rule, or after review
  source       ENUM('photo','invoice','statement','schedule','cash') NOT NULL DEFAULT 'photo',
  status       ENUM('sorted','needs_review','no_document') NOT NULL DEFAULT 'needs_review',
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_txn_document FOREIGN KEY (document_id) REFERENCES documents(id),
  CONSTRAINT fk_txn_category FOREIGN KEY (category_id) REFERENCES categories(id),
  KEY idx_txn_date (txn_date),
  KEY idx_txn_status (status),
  KEY idx_txn_company (company_id)
) ENGINE=InnoDB;


-- 5) statements — a bank statement upload (the monthly reconciliation event) ---
CREATE TABLE IF NOT EXISTS statements (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  company_id       INT NOT NULL DEFAULT 1,
  document_id      INT NOT NULL,                  -- the uploaded statement file
  account_ref      VARCHAR(100) NULL,             -- reference/label, never full acct no. (#3)
  period_start     DATE NULL,
  period_end       DATE NULL,
  opening_balance  DECIMAL(12,2) NULL,
  closing_balance  DECIMAL(12,2) NULL,
  currency         CHAR(3) NOT NULL DEFAULT 'ZAR',
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_stmt_document FOREIGN KEY (document_id) REFERENCES documents(id),
  KEY idx_stmt_company (company_id)
) ENGINE=InnoDB;


-- 6) statement_lines — each printed line on a statement -----------------------
--    matched_transaction_id links a line to the document that explains it;
--    match_status = 'unmatched' is exactly the "no document" the owner must see.
CREATE TABLE IF NOT EXISTS statement_lines (
  id                     INT AUTO_INCREMENT PRIMARY KEY,
  company_id             INT NOT NULL DEFAULT 1,
  statement_id           INT NOT NULL,
  line_date              DATE NULL,
  description            VARCHAR(255) NULL,
  amount                 DECIMAL(12,2) NOT NULL,          -- always positive
  direction              ENUM('debit','credit') NULL,     -- debit = money out (spending)
  running_balance        DECIMAL(12,2) NULL,
  matched_transaction_id INT NULL,
  match_status           ENUM('matched','unmatched') NOT NULL DEFAULT 'unmatched',
  created_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_line_statement FOREIGN KEY (statement_id) REFERENCES statements(id),
  CONSTRAINT fk_line_txn FOREIGN KEY (matched_transaction_id) REFERENCES transactions(id),
  KEY idx_line_match (match_status),
  KEY idx_line_company (company_id)
) ENGINE=InnoDB;


-- 7) review_queue — everything a human must approve before it's final ----------
--    LLM category suggestions, missing documents, suspected duplicates. (#1, #2)
CREATE TABLE IF NOT EXISTS review_queue (
  id                    INT AUTO_INCREMENT PRIMARY KEY,
  company_id            INT NOT NULL DEFAULT 1,
  transaction_id        INT NULL,
  document_id           INT NULL,
  reason                ENUM('llm_suggested_category','no_document','possible_duplicate','unreadable_field') NOT NULL,
  suggested_category_id INT NULL,                 -- LLM's guess (NOT applied yet)
  suggestion_note       VARCHAR(500) NULL,
  status                ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
  created_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at           DATETIME NULL,
  resolved_by           VARCHAR(100) NULL,
  CONSTRAINT fk_rq_txn FOREIGN KEY (transaction_id) REFERENCES transactions(id),
  CONSTRAINT fk_rq_document FOREIGN KEY (document_id) REFERENCES documents(id),
  CONSTRAINT fk_rq_category FOREIGN KEY (suggested_category_id) REFERENCES categories(id),
  KEY idx_rq_status (status),
  KEY idx_rq_company (company_id)
) ENGINE=InnoDB;


-- 8) message_log — a plain record of every WhatsApp message in and out ----------
--    So the owner can read the conversations users had with the assistant.
CREATE TABLE IF NOT EXISTS message_log (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  company_id  INT NOT NULL DEFAULT 1,
  wa_from     VARCHAR(32) NOT NULL,                    -- the user's WhatsApp number
  direction   ENUM('in','out') NOT NULL,               -- 'in' = from user, 'out' = from bot
  kind        ENUM('text','image','document','system') NOT NULL DEFAULT 'text',
  body        TEXT NULL,
  wa_msg_id   VARCHAR(64) NULL,                         -- WhatsApp's own message id (wamid),
                                                        --   so we can find a message the owner
                                                        --   later quotes/replies to.
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_ml_from (wa_from),
  KEY idx_ml_time (created_at),
  KEY idx_ml_msgid (wa_msg_id),
  KEY idx_ml_company (company_id)
) ENGINE=InnoDB;


-- =============================================================================
-- Accounts Receivable — money customers owe the business.
--
-- The owner invoices a customer; that invoice is a *receivable* (a promise of
-- money), NOT income yet. Income is recognised the day the customer actually
-- pays (cash basis — the simplest, most honest view for a small business). So
-- an unpaid invoice lives here only; when it is marked paid, a Sales income
-- transaction is written into the books so it shows up in the P&L (like any
-- other money movement, it keeps a link back to its source — the invoice, #4).
-- =============================================================================

-- 9) customers — the people/companies the business invoices --------------------
--    Unique PER COMPANY: two businesses may each have a customer called "Acme".
CREATE TABLE IF NOT EXISTS customers (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  company_id  INT NOT NULL DEFAULT 1,
  name        VARCHAR(200) NOT NULL,
  email       VARCHAR(200) NULL,
  phone       VARCHAR(40) NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_customer_company_name (company_id, name),
  KEY idx_cust_company (company_id)
) ENGINE=InnoDB;


-- 10) invoices — one bill sent to a customer -----------------------------------
--     line_items keeps the itemised work/goods as JSON (same idea as a receipt's
--     raw_extraction). income_transaction_id links to the transaction created
--     when the invoice is paid, so paid income traces back to its invoice (#4).
--     Invoice numbers are unique PER COMPANY (each business has its own INV-0001).
CREATE TABLE IF NOT EXISTS invoices (
  id                     INT AUTO_INCREMENT PRIMARY KEY,
  company_id             INT NOT NULL DEFAULT 1,
  invoice_number         VARCHAR(40) NOT NULL,           -- e.g. INV-0007 (human-facing)
  customer_id            INT NOT NULL,
  issue_date             DATE NOT NULL,
  due_date               DATE NULL,                       -- when payment is expected
  subtotal_amount        DECIMAL(12,2) NOT NULL,          -- before VAT
  vat_amount             DECIMAL(12,2) NOT NULL DEFAULT 0,
  total_amount           DECIMAL(12,2) NOT NULL,          -- subtotal + VAT (what's owed)
  currency               CHAR(3) NOT NULL DEFAULT 'ZAR',
  line_items             JSON NULL,                        -- [{description, quantity, unit_price, amount}]
  notes                  VARCHAR(500) NULL,
  status                 ENUM('draft','sent','paid','cancelled') NOT NULL DEFAULT 'sent',
  paid_date              DATE NULL,
  income_transaction_id  INT NULL,                         -- set when marked paid
  created_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_invoice_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
  CONSTRAINT fk_invoice_income_txn FOREIGN KEY (income_transaction_id) REFERENCES transactions(id),
  UNIQUE KEY uq_invoice_company_number (company_id, invoice_number),
  KEY idx_invoice_status (status),
  KEY idx_invoice_due (due_date),
  KEY idx_inv_company (company_id)
) ENGINE=InnoDB;
