-- =============================================================================
-- Phase 2 — Storage schema for the AI bookkeeping assistant.
--
-- Seven tables. The relationships are the point: a transaction belongs to a
-- document, is put in a category by a rule, and (for bank statements) is matched
-- to a statement line. Anything the system is unsure about goes to review_queue
-- for a human — nothing is decided silently.
--
-- Money is DECIMAL(12,2) — exact to the cent. Never FLOAT for money.
-- Full card/account numbers are NEVER stored (non-negotiable #3): last 4 only.
-- Every figure keeps a link back to its source document (non-negotiable #4).
--
-- Safe to run more than once (IF NOT EXISTS everywhere).
-- =============================================================================

CREATE DATABASE IF NOT EXISTS bookkeeper
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE bookkeeper;


-- 1) categories — the chart of categories (Fuel, Rent, Sales, ...) ------------
CREATE TABLE IF NOT EXISTS categories (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  name        VARCHAR(100) NOT NULL,
  kind        ENUM('income','expense') NOT NULL DEFAULT 'expense',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_category_name (name)
) ENGINE=InnoDB;


-- 2) category_rules — the deterministic rules engine (non-negotiable #1) -------
--    "vendor contains 'Engen' -> category Fuel". Code applies these, not the LLM.
CREATE TABLE IF NOT EXISTS category_rules (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  match_field  ENUM('vendor','description') NOT NULL DEFAULT 'vendor',
  match_type   ENUM('contains','equals','regex') NOT NULL DEFAULT 'contains',
  pattern      VARCHAR(255) NOT NULL,
  category_id  INT NOT NULL,
  priority     INT NOT NULL DEFAULT 100,   -- lower number wins when several match
  active       TINYINT(1) NOT NULL DEFAULT 1,
  notes        VARCHAR(255) NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_rule_category FOREIGN KEY (category_id) REFERENCES categories(id),
  KEY idx_rule_active (active, priority)
) ENGINE=InnoDB;


-- 3) documents — every uploaded receipt / invoice / statement ------------------
--    source_key is where the actual file lives (local path now; R2/B2 key later).
--    raw_extraction keeps the full JSON the reader returned = the audit trail.
CREATE TABLE IF NOT EXISTS documents (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  source_key     VARCHAR(500) NOT NULL,          -- file path / storage key (#4)
  doc_type       ENUM('receipt','invoice','statement') NOT NULL DEFAULT 'receipt',
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
  KEY idx_doc_type (doc_type)
) ENGINE=InnoDB;


-- 4) transactions — one money movement, always tied to a document -------------
--    A receipt = 1 transaction; a statement = many (via statement_lines).
CREATE TABLE IF NOT EXISTS transactions (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  document_id  INT NOT NULL,                      -- link back to source (#4)
  txn_date     DATE NULL,
  description  VARCHAR(255) NULL,
  amount       DECIMAL(12,2) NOT NULL,
  vat_amount   DECIMAL(12,2) NULL,
  currency     CHAR(3) NOT NULL DEFAULT 'ZAR',
  category_id  INT NULL,                          -- set by a rule, or after review
  source       ENUM('photo','invoice','statement','schedule') NOT NULL DEFAULT 'photo',
  status       ENUM('sorted','needs_review','no_document') NOT NULL DEFAULT 'needs_review',
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_txn_document FOREIGN KEY (document_id) REFERENCES documents(id),
  CONSTRAINT fk_txn_category FOREIGN KEY (category_id) REFERENCES categories(id),
  KEY idx_txn_date (txn_date),
  KEY idx_txn_status (status)
) ENGINE=InnoDB;


-- 5) statements — a bank statement upload (the monthly reconciliation event) ---
CREATE TABLE IF NOT EXISTS statements (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  document_id      INT NOT NULL,                  -- the uploaded statement file
  account_ref      VARCHAR(100) NULL,             -- reference/label, never full acct no. (#3)
  period_start     DATE NULL,
  period_end       DATE NULL,
  opening_balance  DECIMAL(12,2) NULL,
  closing_balance  DECIMAL(12,2) NULL,
  currency         CHAR(3) NOT NULL DEFAULT 'ZAR',
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_stmt_document FOREIGN KEY (document_id) REFERENCES documents(id)
) ENGINE=InnoDB;


-- 6) statement_lines — each printed line on a statement -----------------------
--    matched_transaction_id links a line to the document that explains it;
--    match_status = 'unmatched' is exactly the "no document" the owner must see.
CREATE TABLE IF NOT EXISTS statement_lines (
  id                     INT AUTO_INCREMENT PRIMARY KEY,
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
  KEY idx_line_match (match_status)
) ENGINE=InnoDB;


-- 7) review_queue — everything a human must approve before it's final ----------
--    LLM category suggestions, missing documents, suspected duplicates. (#1, #2)
CREATE TABLE IF NOT EXISTS review_queue (
  id                    INT AUTO_INCREMENT PRIMARY KEY,
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
  KEY idx_rq_status (status)
) ENGINE=InnoDB;


-- 8) message_log — a plain record of every WhatsApp message in and out ----------
--    So the owner can read the conversations users had with the assistant.
CREATE TABLE IF NOT EXISTS message_log (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  wa_from     VARCHAR(32) NOT NULL,                    -- the user's WhatsApp number
  direction   ENUM('in','out') NOT NULL,               -- 'in' = from user, 'out' = from bot
  kind        ENUM('text','image','document','system') NOT NULL DEFAULT 'text',
  body        TEXT NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_ml_from (wa_from),
  KEY idx_ml_time (created_at)
) ENGINE=InnoDB;
