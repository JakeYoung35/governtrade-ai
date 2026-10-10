# Verification

- Python compile and JavaScript syntax checks passed.
- 17 engine tests passed: ledger/account reconciliation, duplicate execution prevention, approvals/denials, hard blocks, execution-time permission changes, shared daily limits, order splitting, mandate caps including fees, scheduled execution/expiry, pause, stale quotes, parser clarification/execution, settlements, disabled agents and JSON persistence.
- Concurrent HTTP integration check: eight parallel $10 requests with a $15 daily cap produced exactly one fill; cash remained $990.
- Browser verified all five agent cards open the correct named chat.
- Browser verified authorized $10 fill, $40 approval, $950 hard block with unchanged $1,000 balance, and five-day mandate creation with governed $5 BTC/ETH fills.
- Main account remained unchanged while demo scenarios were exercised.
- Dashboard screenshot saved as ../governtrade-preview.png.

Simulation limits are documented in README.md. Partial fills, real authentication, provider integrations and unattended scheduling are not implemented.

## GovernPay demo interface update
Reused original GuidedDemo class structure and styling: full-width shell, compact simulated date, scenario intro, customer/governance panels, typing, illustrated cursor callouts and sticky presenter controls. Browser verified preview without execution, separate fill action, Back replay and Restart. Existing 17 engine tests pass.
