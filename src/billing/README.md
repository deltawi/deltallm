# Billing modules

This package owns pricing, budget reservations, charges, spend, and accounting.
Database queries belong in `src/db`. Bootstrap owns clients and worker startup.

```text
src/billing/                # Prices, reservations, charges, and durable accounting.
├── __init__.py             # Keep the public cost calculation exports.
├── money.py                # Convert and validate exact monetary units.
├── pricing/                # Resolve prices and build cost and token quotes.
├── budgets/                # Enforce budgets and send budget notifications.
├── charges/                # Reserve and settle audio, realtime, and selector charges.
├── spend/                  # Accept, store, deliver, and reconcile spend operations.
└── accounting/             # Coordinate native accounting admission and settlement.
    ├── permits/            # Issue, use, renew, and return local spending permits.
    ├── journal/            # Record terminal work and recover incomplete settlement.
    ├── reporting/          # Project accepted events into accounting read models.
    ├── transport/          # Define and serve authenticated accounting RPC calls.
    └── health/             # Report accounting worker, backlog, and admission health.
```

Put new code with the capability that owns its policy. Keep one owner for each
charge and each accounting effect. Do not create another billing implementation.
