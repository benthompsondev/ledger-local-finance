# Accounts Model — Implementation Plan

**Date:** 2026-07-18 · Branch `feature/home-weekly-checkin` from `1ade49f`.

## Existing account-like structures (build on, don't duplicate)

- `transactions.account_type` — free strings from parsers/manual entry
  (`chequing|savings|mastercard|cash|other|csv`). Used by Dashboard/analytics filters
  and statement-summary lookups. **Keep writing it** for compatibility.
- `transactions.account_id` — free-text user label ("CHQ-1272"). Cosmetic; keep.
- `account_balances` — manual Net Worth balance entries with an `account_kind`
  vocabulary (`cash|chequing|savings|credit_card|loan|mortgage|other_asset|
  other_liability`). **Reuse this vocabulary** as the accounts `type` enum.
- `import_log.account_type` — batch label; unchanged.
- Dedup fingerprint = `sha256(account_type|date|raw_desc|amount)` — cannot
  distinguish two credit cards. This is the concrete bug accounts fix.

## Schema (additive, migration follows Codex's savepoint pattern)

```sql
CREATE TABLE accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,                 -- user-visible, unique among active
  type TEXT NOT NULL,                 -- account_kind vocabulary (above)
  institution TEXT,                   -- optional, user-entered only
  currency TEXT DEFAULT 'CAD',
  is_archived INTEGER DEFAULT 0,
  opening_balance REAL DEFAULT 0,
  opening_balance_date TEXT,
  is_migrated INTEGER DEFAULT 0,      -- created by backfill, enables legacy dedup
  created_at/updated_at TEXT
);
ALTER TABLE transactions ADD COLUMN account_ref INTEGER;   -- nullable
ALTER TABLE import_profiles ADD COLUMN suggested_account_ref INTEGER;
```

## Backfill (no institution guessing)

One migrated account per distinct legacy `account_type` present in transactions:
`chequing→"Chequing (migrated)"`, `savings→"Savings (migrated)"`,
`mastercard→"Credit card (migrated)" (type credit_card)`, `cash→"Cash (migrated)"`,
anything else→`"Imported (migrated)" (type other_asset)`. Rows get `account_ref`
by their `account_type`. Idempotent (skips when `account_ref` already populated;
matches existing migrated accounts by name). Wrapped in a savepoint; injected
failure rolls back completely. No amounts/categories/provenance touched.

## Account-aware duplicate detection

New inserts compute `dedup_hash = sha256("acct{account_ref}|date|desc|amount")`
when `account_ref` is present (legacy formula otherwise). Duplicate iff an
existing row matches the **new** hash, OR matches the **legacy** hash AND that
row's `account_ref` is NULL or equals the target account (protects re-imports of
pre-accounts data into its migrated account, while two different credit cards
with identical charges never collide). Documented compatibility: legacy-hash
checking only guards migrated/NULL rows — a deliberate, narrow bridge.

## Balances (one shared service: `utils/balances.py`)

Amounts are stored as positive magnitudes with `direction` (MC refunds are
negative credits → use `ABS()`), `direction='cancelled'` excluded.

- Asset types (`cash|chequing|savings|other_asset`, `investment` manual):
  `balance = opening + Σ|credits| − Σ|debits|`; net-worth contribution `+balance`.
- Liability types (`credit_card|loan|mortgage|other_liability`):
  `balance owed = opening + Σ|debits| − Σ|credits|`; contribution `−balance`.
- Per-currency computation; only the account's own currency is summed. Non-CAD
  accounts are listed separately, never merged (no invented FX).
- Net Worth page gains a computed-balances panel per account (informational);
  the manual `account_balances` snapshot flow stays authoritative for snapshot
  history — no silent double-counting.

## Workflow changes

- **Add Data / uploads:** per-file destination-account selector in the staged
  list, pre-selected by detected type (first active account of matching type);
  quick "create account" expander; conflict warning when a PDF's detected type
  ≠ selected account's type (warn, never silently switch).
- **Manual entry:** account selector (real accounts) + quick-create replaces the
  temporary label list; first-run (no accounts) shows a small create-first-
  account form instead of an empty selector.
- **Import profiles:** successful import stores `suggested_account_ref`; on
  match it's a visible pre-selection only — always changeable; archived/missing
  suggestions are ignored gracefully.
- **Transactions:** account filter (by real accounts) + account column.
- **Review:** bulk groups show the accounts involved when more than one.
- **Settings:** "Accounts" management section — list with balance/tx count/last
  activity, edit, archive/restore. No deletion of accounts holding transactions.
- **Demo data:** creates demo accounts and assigns `account_ref`.

## Risks

- Dedup bridge subtlety (covered by a test matrix).
- Legacy string filters (Dashboard account dropdown) stay string-based this
  slice — documented, not a regression.
- Streamlit state for per-file selectors keyed by file hash (same pattern as
  CSV mapping — proven).

## Acceptance

Migration idempotent + rollback-on-failure on a populated legacy DB; no row or
amount lost; same-looking charges on two cards both insert; re-import of a
legacy file into its migrated account still skips; balances match hand-computed
values for every account type incl. opening balances, transfers, and a non-CAD
account; app opens and all flows work with zero accounts, one, and many.
