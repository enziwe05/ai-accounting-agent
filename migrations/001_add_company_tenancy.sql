-- =============================================================================
-- Migration 001 — Multi-tenancy: give every row an owning company.
--
-- SAFE / NON-DESTRUCTIVE: this only ADDS a companies table, ADDS a company_id
-- column to each data table (defaulting to 1), and backfills all existing rows
-- to company 1 (M26). No existing data is changed or deleted. M26 keeps seeing
-- exactly its own books; new companies get their own isolated rows.
--
-- Run ONCE against an existing single-tenant (M26) database, AFTER taking a
-- backup (deploy/backup.sh). Idempotent-ish: uses IF NOT EXISTS / guards where
-- MySQL allows. If a statement errors because it was already applied, it is safe
-- to comment it out and re-run the rest.
-- =============================================================================

-- 0) companies — the tenants. M26 is company 1. -------------------------------
CREATE TABLE IF NOT EXISTS companies (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  name             VARCHAR(200) NOT NULL,
  slug             VARCHAR(100) NOT NULL,
  -- Subscription state, kept in sync from the web app's PayFast webhook.
  status           ENUM('active','past_due','suspended','cancelled')
                     NOT NULL DEFAULT 'active',
  -- The WhatsApp sender number(s) that belong to this company are stored in
  -- company_numbers (below), so one shared bot number can route by sender.
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_company_slug (slug)
) ENGINE=InnoDB;

-- Seed M26 as company 1 (only if the table is empty / id 1 absent).
INSERT INTO companies (id, name, slug, status)
SELECT 1, 'M26', 'm26', 'active'
WHERE NOT EXISTS (SELECT 1 FROM companies WHERE id = 1);

-- 0b) company_numbers — maps an inbound WhatsApp sender -> company. -----------
--     On a single shared bot number, the sender's phone tells us whose books
--     a message belongs to. Numbers are stored digits-only.
CREATE TABLE IF NOT EXISTS company_numbers (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  company_id   INT NOT NULL,
  wa_number    VARCHAR(32) NOT NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_cn_company FOREIGN KEY (company_id) REFERENCES companies(id),
  UNIQUE KEY uq_cn_number (wa_number),
  KEY idx_cn_company (company_id)
) ENGINE=InnoDB;

-- 1) Add company_id (default 1 = M26) to every tenant-scoped table. -----------
--    DEFAULT 1 means existing rows are automatically attributed to M26 and any
--    old INSERT that forgets company_id still lands on M26 (fail-safe during
--    the transition; the code is updated to always set it explicitly).
ALTER TABLE categories       ADD COLUMN company_id INT NOT NULL DEFAULT 1;
ALTER TABLE category_rules   ADD COLUMN company_id INT NOT NULL DEFAULT 1;
ALTER TABLE documents        ADD COLUMN company_id INT NOT NULL DEFAULT 1;
ALTER TABLE transactions     ADD COLUMN company_id INT NOT NULL DEFAULT 1;
ALTER TABLE statements       ADD COLUMN company_id INT NOT NULL DEFAULT 1;
ALTER TABLE statement_lines  ADD COLUMN company_id INT NOT NULL DEFAULT 1;
ALTER TABLE review_queue     ADD COLUMN company_id INT NOT NULL DEFAULT 1;
ALTER TABLE message_log      ADD COLUMN company_id INT NOT NULL DEFAULT 1;
ALTER TABLE customers        ADD COLUMN company_id INT NOT NULL DEFAULT 1;
ALTER TABLE invoices         ADD COLUMN company_id INT NOT NULL DEFAULT 1;

-- 2) Indexes for fast per-company lookups. ------------------------------------
ALTER TABLE categories       ADD KEY idx_cat_company (company_id);
ALTER TABLE category_rules   ADD KEY idx_rule_company (company_id);
ALTER TABLE documents        ADD KEY idx_doc_company (company_id);
ALTER TABLE transactions     ADD KEY idx_txn_company (company_id);
ALTER TABLE statements       ADD KEY idx_stmt_company (company_id);
ALTER TABLE statement_lines  ADD KEY idx_line_company (company_id);
ALTER TABLE review_queue     ADD KEY idx_rq_company (company_id);
ALTER TABLE message_log      ADD KEY idx_ml_company (company_id);
ALTER TABLE customers        ADD KEY idx_cust_company (company_id);
ALTER TABLE invoices         ADD KEY idx_inv_company (company_id);

-- 3) Uniqueness is now PER COMPANY, not global. -------------------------------
--    Two different companies must each be able to have a "Fuel" category, an
--    invoice "INV-0001", a customer "Acme", etc. Drop the global unique keys and
--    recreate them scoped to the company.
ALTER TABLE categories
  DROP INDEX uq_category_name,
  ADD UNIQUE KEY uq_category_company_name (company_id, name);

ALTER TABLE customers
  DROP INDEX uq_customer_name,
  ADD UNIQUE KEY uq_customer_company_name (company_id, name);

ALTER TABLE invoices
  DROP INDEX uq_invoice_number,
  ADD UNIQUE KEY uq_invoice_company_number (company_id, invoice_number);

-- Done. Every existing row now belongs to company 1 (M26); new companies get
-- their own isolated rows keyed by company_id.
