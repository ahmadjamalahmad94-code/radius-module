# HobeRadius Intelligent ISP Business OS - Master Architecture

## Vision

HobeRadius is evolving from a RADIUS administration module into an Intelligent
ISP Business Operating System: a single backend source of truth for subscriber
operations, card sales, wallets, immutable financial accounting, approvals,
events, notifications, reports, and customer/operator portals.

The goal is not scattered features. The goal is an integrated business platform
where every operational action can be traced to:

- who performed it,
- which scope they were allowed to affect,
- which price snapshot was used,
- which ledger entries were recorded,
- which events and notifications were emitted,
- and which dashboard/report should reflect the result.

The backend owns business truth. Web and Flutter clients are clients only.

## Main Sections

### 1. Dashboard / Command Center

The command center gives operators a drill-down view of the ISP business:

- active subscribers and card users,
- online users and NAS health,
- revenue, debt, loans, wallet exposure, and profit,
- low manager/distributor balances,
- expiring subscriptions,
- failed accounting or RADIUS signals,
- pending approvals,
- unresolved operational events.

Every metric should link to the filtered source records behind it.

### 2. Subscribers

The subscriber section manages fixed customers and their lifecycle:

- profile/package,
- service status,
- renewals,
- debts and loans,
- payments and discounts,
- wallet activity,
- sessions/accounting,
- devices and MAC information,
- timeline and messages.

Subscriber actions must record ledger entries and business events whenever money,
debt, loan days, or service entitlement changes.

### 3. Card Users

Card users are non-fixed users who can own a wallet and buy or hold cards:

- personal profile,
- wallet balance,
- purchased cards,
- card usage history,
- messages,
- timeline,
- marketplace purchase history.

They are distinct from subscribers but share finance, event, notification, and
portal foundations.

### 4. Cards

Cards represent prepaid access inventory:

- generated cards,
- assigned/sold cards,
- active and expired cards,
- card credentials and package binding,
- financial cost and sale traces,
- batch membership,
- print/export lifecycle.

Card financial behavior must be based on immutable pricing snapshots captured at
batch creation or sale time.

### 5. Profiles & Packages

Profiles and packages describe service offers:

- internet packages for subscribers,
- card packages for prepaid access,
- speed policy,
- duration,
- retail and wholesale price,
- allowed discounts and manager/distributor constraints.

Changing a package must not change historical revenue records. Historical
actions use `PriceSnapshot`.

### 6. Finance Center

Finance Center is the accounting heart of the system:

- wallets,
- wallet transactions,
- immutable ledger entries,
- payments,
- revenue records,
- debts,
- loans,
- discounts,
- profit shares,
- reconciliation views.

No financial record should be hard-deleted. Corrections are recorded as reversals
or correction entries.

### 7. Managers & Distributors

Managers and distributors are operational business actors with scoped authority:

- managed subscribers,
- card batches and card sales,
- wallet balances,
- cost exposure,
- debt/credit,
- permissions,
- limits,
- profit shares,
- risk score and audit timeline.

Their actions must be scope-aware and limit-aware.

### 8. Communication Center

Communication Center coordinates event-triggered and manual messages:

- notification templates,
- campaigns,
- audience segments,
- delivery logs,
- SMS/WhatsApp/Telegram/email provider abstraction,
- action-coupled messaging where allowed.

Notification delivery is event-driven and audit-visible.

### 9. Events & Audit Center

Events & Audit Center is the investigation surface:

- business events,
- security events,
- financial events,
- RADIUS/system events,
- entity timelines,
- correlation IDs,
- fraud/risk flags,
- operator activity.

The system should assume that every important action may later require proof.

### 10. Operations Center

Operations Center covers network and service health:

- NAS status,
- online sessions,
- RADIUS health,
- accounting issues,
- VPN/API status where available,
- speed control preview,
- safe operational actions or pending actions.

Live network operations must remain guarded, audited, and reversible.

### 11. Requests & Queue

Requests & Queue handles actions that need approval or delayed execution:

- manager discounts above limit,
- loans above policy,
- batch creation requiring approval,
- customer renewal requests,
- portal support requests,
- safe pending network actions.

Requests should have explicit status, actor, target, reason, and audit trail.

### 12. Reports

Reports turn operational data into business insight:

- daily/monthly/yearly finance,
- subscribers and churn,
- cards and batches,
- manager/distributor performance,
- usage and online sessions,
- debt/loan exposure,
- audit/security exports.

Reports should be reproducible and traceable to source records.

### 13. Settings

Settings holds controlled configuration:

- company defaults,
- financial policies,
- manager/distributor limits,
- loan rules,
- notification providers,
- package defaults,
- portal settings,
- safety flags.

Changing settings must emit events and preserve historical snapshots where
business value is affected.

### 14. Subscriber Portal

Subscriber Portal is the self-service surface for fixed customers:

- subscription status,
- remaining time,
- usage,
- wallet/debt/loan status,
- renewal or loan request,
- messages and notifications,
- support.

It must be strictly self-scoped.

### 15. Card User Portal

Card User Portal is the self-service surface for prepaid card users:

- wallet,
- marketplace,
- owned cards,
- active/expired card state,
- purchase history,
- notifications.

It must be strictly self-scoped and never expose admin or other user data.

## Core Architecture Rules

### Immutable Financial Ledger

Financial truth is append-only. A wrong entry is corrected with reversal or
correction entries. Ledger rows are never updated to rewrite history and never
hard-deleted.

### Snapshot Pricing

Every financial action that depends on price captures a price snapshot at the
time of action. Package price changes must not alter historical revenue,
profit, wallet, or ledger calculations.

### Scope-aware Access

Every actor operates within a scope:

- global/company,
- branch,
- manager,
- distributor,
- subscriber,
- card user.

Services must resolve scope before listing, mutating, approving, or reporting
business records.

### Event-driven Notifications

Business events are the source for automated notifications and campaign
audiences. Renewal, loan, debt, wallet, card, batch, security, and system events
should be publishable to the Communication Center.

### Drill-down Dashboards

Dashboard totals are not decorative counters. Each total must be traceable to
filtered records and reports.

### Audit-first Design

Sensitive actions record actor, target, reason, before/after metadata, and
correlation ID where practical.

### No Hard Delete For Financial Data

Financial records, ledger entries, wallet transactions, revenue records, debt,
loan, and profit-share records are archived, voided, or reversed, not deleted.

### Backend Is Source Of Truth

Business rules live in backend services. Web and Flutter must not invent finance
calculations, permission logic, or operational state.

### Flutter/Web Are Clients

Flutter and web clients consume stable backend contracts. They can optimize
presentation, but they do not own accounting, scope, audit, or lifecycle truth.

## Phase Boundary

This document is contract only. It does not create migrations, runtime behavior,
API routes, or UI code.
