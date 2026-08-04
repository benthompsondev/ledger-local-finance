# Categorization Friction Reduction Plan

**Date:** 2026-07-17
**Scope:** merchant normalization, saved category decisions, grouped bulk approval, and safe import-batch undo

## Current architecture

- Importers produce transaction dictionaries and call `utils.categorizer.enrich_transaction()` before `pages/3_Import.py` writes them to SQLite.
- `transactions.raw_description` preserves the statement text. `transactions.merchant` currently stores the output of `normalize_merchant()` and is also used as the learned-rule key.
- Duplicate protection happens before insert through a unique deterministic `dedup_hash` built from account, date, raw description, and amount.
- Deterministic payment, transfer, and cancellation handling runs before ordinary categorization. Static importer rules live in `config/rules.py`.
- `learned_rules` already stores one merchant-to-category decision and is consulted before static rules for ordinary transactions. It does not currently support disabling, examples, update timestamps, or source transaction/batch provenance.
- Review can apply a category to one row, uncertain siblings, or every row with the exact same `merchant` string. It does not group noisy aliases under one normalized identity.
- `import_log` is the batch record and `transactions.import_batch_id` is the transaction provenance link. `statement_summaries` is the only other table owned by a transaction import batch.
- `delete_import_batch()` deletes transactions, the log row, and a statement summary, but the UI only shows filename, period, and row count. It does not report later edits or rules learned from the batch, and the helper does not own an explicit transaction boundary when passed an existing connection.
- Historical category changes have no durable source field. Review flags and AI metadata provide partial clues, but they are not an audit trail.

## User friction being addressed

Noisy descriptions such as store numbers, terminal IDs, processor prefixes, and reference suffixes split one real merchant into several exact strings. The user then repeats the same category choice row by row. Current bulk actions help only when the existing `merchant` text is identical, and a later import cannot explain whether a category came from protected financial plumbing, a user rule, or a static heuristic. Import removal is available but does not provide enough evidence for a trustworthy decision.

## Proposed data-model changes

Additive migrations only:

- `transactions.merchant_normalized`: stable exact matcher derived deterministically from `raw_description`.
- `transactions.category_source`: `protected`, `user_rule`, `import_rule`, `user_edit`, `user_bulk`, `ai`, or `unknown`.
- `transactions.category_rule_id`: saved merchant rule used during import, when applicable.
- `transactions.category_explanation`: short inspectable explanation of the automatic or manual decision.
- `transactions.category_updated_at` and `transactions.manually_edited_at`: timestamps for audit and undo warnings.
- Extend `learned_rules` with `enabled`, `example_description`, `updated_at`, `source_transaction_id`, and `source_import_batch_id`.

Existing databases are migrated in place. Existing transaction rows receive a normalized merchant without changing the original description or historical category. Existing learned rules remain enabled and user-authored.

## Matching precedence

1. Exact duplicate detection prevents a second insert.
2. Deterministic internal-transfer, credit-card-payment, transfer, and cancellation handling.
3. Other protected plumbing: refunds/reimbursements, fees/interest, cash advances, and other importer-detected system categories.
4. Enabled exact user rule for `merchant_normalized`.
5. Existing static importer/category heuristics.
6. Uncategorized/low-confidence review state.

The matcher is exact after normalization. There is no fuzzy or probabilistic classification. If multiple historical rule rows somehow overlap, the unique normalized matcher and lowest stable rule ID make resolution deterministic.

## Normalization strategy

Normalize Unicode and case, collapse whitespace, standardize punctuation, and remove only high-confidence statement noise at the edges: payment-processor prefixes, trailing reference/order tokens, terminal or store-number suffixes, date-like suffixes, and trailing city/province fragments. Keep meaningful words and embedded digits when they are part of merchant identity. Empty or malformed input returns an empty matcher.

The UI always keeps and shows sample raw descriptions so a match is explainable.

## Bulk approval behaviour

The Review page will lead with repeated uncertain merchants grouped by `merchant_normalized`. Each group shows count, absolute total, date range, current categories, and sample raw descriptions. The user selects a category, previews the exact selected row count, chooses whether to save a future rule, and confirms one bulk action. Current-row changes and future-import behavior are separate choices.

Only explicitly selected transaction IDs are updated. Amounts and all non-category financial fields remain unchanged. Protected plumbing rows are excluded from ordinary merchant bulk groups.

## Import-batch undo behaviour

Before confirmation, a deterministic preview shows filenames, import time, transaction count, income, spending, date range, later/manual edit count, and rules sourced from the batch. Duplicate-only batches are valid undo targets even when they own zero transactions.

Undo runs in one SQLite transaction, deletes only transactions and statement summaries tied to the selected `import_log.id`, then deletes that log row. Saved merchant rules are preserved by default and their source transaction link is cleared by SQLite semantics/application code if needed. The helper verifies the batch exists, prevents a second execution from being treated as success, and rolls back the whole operation on any failure.

## Risks and controls

- **Over-normalization:** conservative anchored patterns plus distinct-merchant regression tests.
- **Plumbing corruption:** protected-type precedence and protected-row exclusion from ordinary bulk approval.
- **Historical rewrite:** migrations backfill metadata only; deleting or disabling a rule never recategorizes old rows.
- **Large bulk action:** explicit ID preview and confirmation; no open-ended `UPDATE ... WHERE merchant LIKE ...`.
- **Undo data loss:** explicit summary, manual-edit warning, transactional delete, unrelated-batch tests, and preserved rules.
- **Old database ambiguity:** pre-migration manual edits cannot always be proven. Undo reports known manual provenance plus legacy reviewed/AI signals as a conservative warning.

## Acceptance criteria

- Normalization is deterministic and joins common noisy variants without collapsing clearly different merchants.
- A saved enabled user rule categorizes a later matching import and stores an explanation; disabled rules do not.
- Protected transfer/payment/refund/fee/interest/cash-advance logic wins over a conflicting saved rule.
- Review groups repeated uncertainty by normalized merchant and updates only the selected IDs; preview and applied counts match; totals are unchanged.
- Rules can be listed, edited, enabled/disabled, and deleted without retroactively changing transactions.
- Batch undo previews ownership and edit risk, removes only the chosen batch transactionally, preserves unrelated batches and saved rules, handles duplicate-only logs, and is safe when repeated.
- Focused tests, the full pytest suite, compile check, doctor, smoke suite, and synthetic demo-mode UI walkthrough are reported separately.

## Acceptance hardening completed

- Categorization schema additions, indexes, and historical matcher backfill run inside one SQLite savepoint. An injected backfill failure rolls the whole schema slice back.
- Each file import now persists its log, transactions, learned-rule hit counts, and statement summary atomically. A failed row or summary leaves no partial batch.
- Learned-rule hits increase only for successfully inserted transactions, not previews or duplicates.
- Bulk approval fails closed when selected IDs disappear or become protected between preview and apply. Unresolved groups require a real category before Apply or future-rule creation is enabled.
- Import undo clears rule links by both batch provenance and the actual source transaction IDs being removed. Edited batches require an explicit acknowledgement in the UI.
- Automatic recategorization preserves manual, bulk, AI-reviewed, and older unknown-provenance categories.
- A populated synthetic legacy database verifies idempotent migration, preserved categories and totals, rule creation, and batch-isolated undo without using private financial data.
