# HobeRadius Business OS - Section Map

This map defines the intended information architecture. It is contract only and
does not create routes or UI.

## Dashboard / Command Center

- Subsections:
  - Executive overview
  - Financial pulse
  - Operations pulse
  - Risk and alerts
  - Pending approvals
- Inner tabs:
  - Today
  - Month
  - Year
  - Custom range
  - By manager/distributor
- Actions:
  - drill down to filtered records
  - export snapshot
  - open event/risk detail
  - open pending approval

## Subscribers

- Subsections:
  - Subscriber list
  - Subscriber 360
  - Renewals
  - Debts
  - Loans
  - Devices and MACs
  - Login/accounting history
- Inner tabs:
  - Overview
  - Financial
  - Usage and sessions
  - Services
  - Timeline
  - Messages
  - Notes
  - Login events
- Actions:
  - create subscriber
  - renew
  - accept payment
  - add discount
  - create debt
  - grant/settle loan
  - send message
  - change package
  - archive subscriber

## Card Users

- Subsections:
  - Card user list
  - Card user 360
  - Wallets
  - Owned cards
  - Marketplace purchases
- Inner tabs:
  - Overview
  - Wallet
  - Cards
  - Purchases
  - Usage
  - Timeline
  - Messages
- Actions:
  - create card user
  - recharge wallet
  - sell/assign card
  - send message
  - suspend/restore
  - archive

## Cards

- Subsections:
  - Card dashboard
  - Packages
  - Batches
  - Individual cards
  - Print/export
  - Sales
- Inner tabs:
  - All
  - Unused
  - Sold
  - Active
  - Expired
  - Connected
  - Problem cards
- Actions:
  - create package
  - generate batch
  - assign responsible manager/distributor
  - print/export
  - mark sold where policy allows
  - inspect financial summary
  - archive batch

## Profiles & Packages

- Subsections:
  - Internet packages
  - Card packages
  - Speed policies
  - Loan policies
  - Price history
- Inner tabs:
  - Active
  - Draft
  - Retired
  - Pricing
  - Limits
- Actions:
  - create/edit package
  - retire package
  - capture price snapshot on use
  - configure loan policy
  - configure speed policy

## Finance Center

- Subsections:
  - Finance dashboard
  - Wallets
  - Ledger
  - Payments
  - Revenue
  - Debts
  - Loans
  - Profit shares
  - Reconciliation
- Inner tabs:
  - Today
  - Month
  - Year
  - Pending
  - Reversed/corrected
  - By owner
- Actions:
  - credit/debit wallet
  - transfer wallet balance
  - record payment
  - settle debt/loan
  - create ledger correction
  - reverse allowed entries
  - export report

## Managers & Distributors

- Subsections:
  - Managers
  - Distributors
  - Wallets
  - Permissions
  - Limits
  - Profit
  - Assigned subscribers
  - Assigned cards/batches
- Inner tabs:
  - Overview
  - Wallet
  - Subscribers
  - Card batches
  - Payments
  - Profit shares
  - Limits
  - Events
- Actions:
  - create actor
  - recharge wallet
  - assign package access
  - configure permissions
  - configure limits
  - inspect profit
  - suspend/restore
  - archive

## Communication Center

- Subsections:
  - Notifications
  - Templates
  - Campaigns
  - Audience segments
  - Delivery logs
  - Provider settings
- Inner tabs:
  - Manual send
  - Event-triggered
  - Drafts
  - Scheduled
  - Sent
  - Failed
- Actions:
  - create template
  - build audience
  - send message
  - schedule campaign
  - retry failed delivery
  - inspect recipient timeline

## Events & Audit Center

- Subsections:
  - Event feed
  - Audit timeline
  - Security events
  - Financial events
  - Risk flags
  - Investigations
- Inner tabs:
  - All
  - By category
  - By severity
  - By actor
  - By target
  - By correlation ID
- Actions:
  - filter/search
  - open event detail
  - export evidence
  - create investigation note
  - mark reviewed

## Operations Center

- Subsections:
  - Online sessions
  - NAS health
  - RADIUS health
  - Accounting failures
  - VPN/API readiness
  - Speed control
  - Safe pending actions
- Inner tabs:
  - Live status
  - Diagnostics
  - Dry-run actions
  - Applied history
  - Failed actions
- Actions:
  - run read-only diagnostic
  - preview speed policy
  - schedule speed multiplier
  - inspect NAS/router
  - open support bundle

## Requests & Queue

- Subsections:
  - Approval requests
  - Pending customer requests
  - Pending manager actions
  - Failed/retry queue
  - Completed requests
- Inner tabs:
  - Pending
  - Approved
  - Rejected
  - Expired
  - Needs review
- Actions:
  - approve
  - reject
  - request more info
  - retry safe action
  - cancel

## Reports

- Subsections:
  - Financial reports
  - Subscriber reports
  - Card reports
  - Manager/distributor reports
  - Usage reports
  - Audit reports
  - Archives
- Inner tabs:
  - Daily
  - Monthly
  - Yearly
  - Custom range
  - Saved snapshots
- Actions:
  - generate report
  - save snapshot
  - export
  - drill down
  - compare periods

## Settings

- Subsections:
  - Company settings
  - Finance policies
  - Permissions
  - Limits
  - Notification providers
  - Portal settings
  - Safety flags
- Inner tabs:
  - General
  - Financial
  - Operations
  - Providers
  - Audit
- Actions:
  - update setting
  - rotate provider config
  - enable/disable feature flag
  - review audit event

## Subscriber Portal

- Subsections:
  - Portal home
  - Subscription
  - Usage
  - Wallet
  - Debt and loans
  - Payments/recharge
  - Notifications
  - Support
- Inner tabs:
  - Current status
  - History
  - Requests
  - Messages
- Actions:
  - view own data
  - request renewal
  - request loan
  - submit support request
  - view messages

## Card User Portal

- Subsections:
  - Portal home
  - Wallet
  - Marketplace
  - My cards
  - Usage
  - Notifications
  - Support
- Inner tabs:
  - Available packages
  - Active card
  - Purchase history
  - Messages
- Actions:
  - view own wallet
  - buy card from wallet
  - view card details
  - submit support request
