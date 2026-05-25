# HobeRadius Business OS - Domain Model

This document defines the first-pass business entities for the HobeRadius ISP
Business Operating System. It is a contract only; it does not create migrations.

## Entity Catalog

### Subscriber

- Purpose: Fixed customer with a service package, status, renewal lifecycle,
  financial history, sessions, and portal access.
- Owner/scope: company, branch, manager, distributor; self-scope in portal.
- Important fields: id, tenant_id, manager_id, distributor_id, package_id,
  status, service_status, username, contact, current_expiry, wallet_id,
  debt_balance, loan_state, created_at.
- Lifecycle: prospect -> active -> expiring -> expired/disabled -> archived.
- Ledger/events: renewals, payments, discounts, loans, debts, service changes,
  and manual adjustments write ledger entries and events.

### CardUser

- Purpose: Non-fixed prepaid user with wallet, purchased cards, usage, messages,
  and portal access.
- Owner/scope: company, manager, distributor; self-scope in portal.
- Important fields: id, tenant_id, display_name, phone, wallet_id, status,
  created_by, created_at.
- Lifecycle: created -> active -> suspended -> archived.
- Ledger/events: wallet recharges, card purchases, refunds, and messages write
  ledger entries and events.

### Manager

- Purpose: Operational actor who can manage subscribers, card users, batches,
  wallet actions, and limited business operations.
- Owner/scope: company or branch.
- Important fields: id, tenant_id, name, contact, wallet_id, status,
  permission_profile_id, limit_policy_id, created_at.
- Lifecycle: invited/created -> active -> limited/suspended -> archived.
- Ledger/events: wallet recharge/debit, batch costs, renewals, discounts,
  approvals, and limit violations write ledger entries and events.

### Distributor

- Purpose: Sales or distribution actor responsible for subscribers, card users,
  cards, and revenue/profit share.
- Owner/scope: company, branch, or manager.
- Important fields: id, tenant_id, manager_id, name, wallet_id, status,
  commission_policy_id, limit_policy_id.
- Lifecycle: created -> active -> suspended -> archived.
- Ledger/events: card sales, subscriber payments, wallet changes, profit share,
  and scope changes write ledger entries and events.

### Wallet

- Purpose: Balance container for company, manager, distributor, subscriber, or
  card user.
- Owner/scope: owner_type plus owner_id; company/global wallets may have
  nullable owner_id.
- Important fields: id, tenant_id, owner_type, owner_id, balance,
  pending_balance, currency, status, created_at.
- Lifecycle: active -> frozen -> closed/archived.
- Ledger/events: balance-changing transactions must write wallet transactions,
  ledger entries, and events.

### WalletTransaction

- Purpose: Immutable balance movement for a wallet.
- Owner/scope: wallet owner scope.
- Important fields: id, wallet_id, type, amount, before_balance, after_balance,
  reference_type, reference_id, actor_type, actor_id, notes, created_at.
- Lifecycle: appended -> optionally reversed by a later transaction.
- Ledger/events: each transaction references or creates ledger entries and emits
  a financial event.

### LedgerEntry

- Purpose: Immutable accounting entry for financial truth.
- Owner/scope: tenant plus actor/target scope.
- Important fields: id, tenant_id, entry_type, debit_account, credit_account,
  amount, currency, actor_type, actor_id, target_type, target_id,
  reference_type, reference_id, metadata_json, created_at, voided_at,
  reversal_of.
- Lifecycle: appended -> reversed/corrected by another entry; never deleted.
- Ledger/events: it is the ledger; creation emits a business event.

### Debt

- Purpose: Amount owed by subscriber, card user, manager, or distributor.
- Owner/scope: debtor owner_type and owner_id.
- Important fields: id, tenant_id, debtor_type, debtor_id, original_amount,
  remaining_amount, reason, status, due_at, created_by, created_at.
- Lifecycle: open -> partially_paid -> paid -> written_off/archived.
- Ledger/events: creation, payments, write-offs, and reversals write ledger
  entries and events.

### Loan

- Purpose: Temporary service or money extension governed by policy.
- Owner/scope: borrower owner_type and owner_id.
- Important fields: id, tenant_id, borrower_type, borrower_id, loan_type,
  value, days_granted, policy_snapshot_json, status, due_at, settled_at.
- Lifecycle: requested -> approved/active -> settled -> defaulted/voided.
- Ledger/events: approval, activation, settlement, and default emit events and
  may write ledger entries.

### Payment

- Purpose: Captures money received through cash, wallet, bank, gateway, manual,
  or other channel.
- Owner/scope: payer target plus collector actor scope.
- Important fields: id, tenant_id, payer_type, payer_id, collector_type,
  collector_id, amount, method, reference, status, received_at.
- Lifecycle: pending -> confirmed -> allocated -> reversed/voided.
- Ledger/events: confirmed payments write ledger entries, wallet transactions
  where applicable, revenue records, and events.

### CardPackage

- Purpose: Defines prepaid card offer such as duration, speed, retail price, and
  wholesale cost.
- Owner/scope: company; optionally scoped to managers/distributors.
- Important fields: id, tenant_id, name, duration, speed_profile, retail_price,
  wholesale_price, min_price, max_discount, status.
- Lifecycle: draft -> active -> retired.
- Ledger/events: changes emit events; sales use price snapshots.

### InternetPackage

- Purpose: Defines fixed subscriber service offer.
- Owner/scope: company; optionally scoped to manager/distributor.
- Important fields: id, tenant_id, name, duration_days, speed_policy_id,
  retail_price, wholesale_price, loan_policy_id, status.
- Lifecycle: draft -> active -> retired.
- Ledger/events: changes emit events; renewals use price snapshots.

### CardBatch

- Purpose: Group of generated cards with shared package and financial context.
- Owner/scope: creator plus responsible manager/distributor.
- Important fields: id, tenant_id, package_id, price_snapshot_id,
  responsible_type, responsible_id, quantity, status, created_by, created_at.
- Lifecycle: planned -> generated -> partially_sold -> sold/expired/archived.
- Ledger/events: creation can debit responsible wallet, create ledger entries,
  revenue/profit records, and events.

### Card

- Purpose: Individual prepaid access credential.
- Owner/scope: batch owner; assigned card user or buyer when sold.
- Important fields: id, tenant_id, batch_id, card_package_id, code_hash,
  status, sold_to_type, sold_to_id, activated_at, expires_at.
- Lifecycle: generated -> printed/exported -> sold/assigned -> active ->
  expired -> archived.
- Ledger/events: sale, activation, assignment, expiry, and refund emit events
  and may write ledger/revenue records.

### PriceSnapshot

- Purpose: Immutable copy of pricing inputs used by a business action.
- Owner/scope: tenant plus package/reference scope.
- Important fields: id, tenant_id, reference_type, reference_id, retail_price,
  wholesale_price, effective_price, discount_amount, captured_at, captured_by,
  metadata_json.
- Lifecycle: captured once; never modified except archival metadata if needed.
- Ledger/events: referenced by ledger, revenue, batch, card, payment, and
  renewal records.

### RevenueRecord

- Purpose: Captures revenue and profit decomposition for a transaction.
- Owner/scope: tenant plus source/beneficiary scopes.
- Important fields: id, tenant_id, source_type, source_id, original_price,
  retail_price, wholesale_cost, collected_amount, debt_amount, discount_amount,
  net_profit, company_share, distributor_share, manager_share, status.
- Lifecycle: pending -> realized -> reversed/corrected.
- Ledger/events: references ledger entries and emits revenue events.

### ProfitShare

- Purpose: Tracks calculated share owed to a beneficiary.
- Owner/scope: beneficiary_type and beneficiary_id.
- Important fields: id, tenant_id, beneficiary_type, beneficiary_id,
  source_type, source_id, gross_amount, share_amount, share_percent, status.
- Lifecycle: pending -> payable -> paid -> reversed.
- Ledger/events: paid/reversed states write ledger entries and events.

### Event

- Purpose: Audit and business event record.
- Owner/scope: tenant plus actor/target scopes.
- Important fields: id, tenant_id, category, severity, actor_type, actor_id,
  target_type, target_id, event_key, message, metadata_json, correlation_id,
  created_at.
- Lifecycle: appended -> archived by retention policy; no hard delete.
- Ledger/events: events reference ledger/financial records when relevant.

### Notification

- Purpose: User-facing or operator-facing message generated from event, campaign,
  or manual action.
- Owner/scope: recipient scope.
- Important fields: id, tenant_id, recipient_type, recipient_id, channel,
  template_id, payload_json, status, created_at.
- Lifecycle: queued -> sent -> delivered/failed/read/archived.
- Ledger/events: notification state changes emit events; financial actions may
  trigger notifications.

### Campaign

- Purpose: Bulk or targeted communication plan.
- Owner/scope: creator actor scope and audience scope.
- Important fields: id, tenant_id, name, audience_filter_json, channel,
  template_id, status, scheduled_at, created_by.
- Lifecycle: draft -> scheduled -> sending -> completed/failed/cancelled.
- Ledger/events: campaign actions emit events and create message deliveries.

### MessageDelivery

- Purpose: Per-recipient delivery tracking for notifications/campaigns.
- Owner/scope: recipient scope.
- Important fields: id, campaign_id, notification_id, recipient_type,
  recipient_id, provider, status, provider_reference, error, sent_at,
  delivered_at.
- Lifecycle: queued -> sent -> delivered/failed/retried/read.
- Ledger/events: delivery failures and state changes emit communication events.

### ApprovalRequest

- Purpose: Approval workflow for risky or limit-exceeding actions.
- Owner/scope: requester scope and approver scope.
- Important fields: id, tenant_id, request_type, requester_type, requester_id,
  target_type, target_id, payload_json, reason, status, approved_by,
  decided_at.
- Lifecycle: pending -> approved/rejected/cancelled/expired.
- Ledger/events: approval decisions emit events; approved financial actions
  create ledger entries when executed.

### SpeedPolicy

- Purpose: Defines base speed behavior and operational speed rules.
- Owner/scope: package/profile/company scope.
- Important fields: id, tenant_id, name, base_download, base_upload,
  policy_type, status, metadata_json.
- Lifecycle: draft -> active -> retired.
- Ledger/events: changes emit operational events; not financial unless tied to a
  package price change.

### SpeedMultiplier

- Purpose: Temporary or scheduled multiplier over speed policies.
- Owner/scope: company/profile/subscriber scope depending on target.
- Important fields: id, tenant_id, target_type, target_id, multiplier,
  reason, starts_at, ends_at, status, applied_to_radius.
- Lifecycle: planned -> active -> expired/cancelled.
- Ledger/events: creation/application emits operational and audit events.

### ReportSnapshot

- Purpose: Immutable report output for daily/monthly/yearly archive.
- Owner/scope: tenant plus report scope.
- Important fields: id, tenant_id, report_type, period_start, period_end,
  filters_json, result_json, generated_by, generated_at.
- Lifecycle: generated -> archived; no hard delete for financial reports.
- Ledger/events: generation emits report event.

### ArchiveRecord

- Purpose: Preserves historical business state and old records without hard
  deletion.
- Owner/scope: tenant plus entity scope.
- Important fields: id, tenant_id, entity_type, entity_id, archive_reason,
  snapshot_json, archived_by, archived_at.
- Lifecycle: archived once; restore requires explicit audited action.
- Ledger/events: archive and restore emit audit events; financial archive never
  removes ledger truth.

## Cross-cutting Relationships

- WalletTransaction references Wallet and often LedgerEntry.
- LedgerEntry references Payment, Debt, Loan, CardBatch, Card, RevenueRecord, or
  ProfitShare through reference fields.
- PriceSnapshot is attached to revenue-producing or cost-producing actions.
- Event can reference any entity through actor/target/reference metadata.
- Notification and Campaign consume events and create MessageDelivery rows.
- ApprovalRequest can block or approve financial, card, loan, discount, and
  operational actions.
- ReportSnapshot and ArchiveRecord preserve historical business evidence.
