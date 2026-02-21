# Contract Reference (SecRef) Setup

## Purpose

The `contracts` table acts as a local cache of IB contract details (a "SecRef" or security reference). It stores qualified contract metadata so that the tradebot agent and other services can look up contracts from the database without connecting to IBKR directly.

This enforces the **agent-IB boundary**: the LLM agent reads contracts from DB and enqueues jobs; only workers talk to IBKR.

## Table: `contracts`

Model: `ContractRef` in `src/models.py` (named `ContractRef` to avoid collision with `ib_async.Contract`).

### Columns

| Column             | Type        | Nullable   | Description                                             |
| ------------------ | ----------- | ---------- | ------------------------------------------------------- |
| `id`               | int         | PK         | Auto-increment primary key                              |
| `con_id`           | int         | no, unique | IB contract ID (globally unique in IB)                  |
| `symbol`           | str         | no         | e.g. "CL", "AAPL", "SPY"                                |
| `sec_type`         | str         | no         | "FUT", "STK", "OPT", "FOP"                              |
| `exchange`         | str         | no         | "NYMEX", "SMART", "CBOE"                                |
| `currency`         | str         | no         | "USD"                                                   |
| `local_symbol`     | str         | yes        | e.g. "CLK6", "AAPL"                                     |
| `trading_class`    | str         | yes        | e.g. "CL"                                               |
| `contract_month`   | str         | yes        | "2026-04" (derived from expiry, FUT/FOP)                |
| `contract_expiry`  | str         | yes        | Raw IB `lastTradeDateOrContractMonth` (e.g. "20260420") |
| `multiplier`       | str         | yes        | "1000" (FUT), "100" (OPT/FOP), null (STK)               |
| `strike`           | float       | yes        | OPT/FOP only                                            |
| `right`            | str         | yes        | "C" or "P" (OPT/FOP only)                               |
| `primary_exchange` | str         | yes        | For STK routing                                         |
| `is_active`        | bool        | no         | `false` when contract is no longer returned by IB       |
| `fetched_at`       | timestamptz | no         | When the contract was last synced from IB               |
| `created_at`       | timestamptz | no         | Row creation time                                       |
| `updated_at`       | timestamptz | no         | Last update time                                        |

### Days to Expiry

Days to expiry is **not stored** in the database. It is computed at runtime from the `contract_expiry` field using `days_until_contract_expiry()` from `src/services/cl_contracts.py`. This avoids stale values and eliminates the need to refresh a computed column daily.

### Indexes

- `(symbol, sec_type, is_active, contract_expiry)` — front-month lookups for futures
- `(symbol, sec_type, is_active, strike, right, contract_expiry)` — option chain lookups

### Multi-Asset Support

The table is designed to hold Futures, Equities, Options, and Future Options. All asset-class-specific fields (`contract_month`, `contract_expiry`, `multiplier`, `strike`, `right`, `primary_exchange`) are nullable so the same table works across sec_types.

## Contract Sync

### Service: `src/services/contract_sync.py`

`sync_contracts(engine, host, port, client_id, specs)`:

- Connects to IB and calls `reqContractDetails()` for each spec
- Upserts rows into `contracts` by `con_id` (insert on conflict update)
- Marks contracts no longer returned for a spec as `is_active=false`

### Job Type: `contracts.sync`

Handled by `handle_contracts_sync()` in `scripts/work_jobs.py`. Defaults to syncing CL futures from NYMEX. Payload can include custom specs:

```json
{
  "specs": [{ "symbol": "CL", "exchange": "NYMEX", "currency": "USD" }]
}
```

### Triggering a Sync

From the tradebot chat: the agent has an `enqueue_contracts_sync_job` tool.

From the CLI:

```bash
# The worker:jobs process must be running to execute the job
ENV=dev task worker:jobs
```

## How the Agent Uses Contracts

1. User asks to buy/sell CL.
2. Agent calls `preview_cl_order` which reads active CL contracts from the `contracts` table, selects the appropriate month, and enqueues a `pretrade.check` job.
3. Agent calls `check_pretrade_job` to poll margin/pricing results.
4. Agent calls `submit_cl_order` with the `pretrade_job_id` to queue the order.

The agent **never imports `ib_async`**. All IB interaction happens in workers.

## Pre-Trade Checks

### Service: `src/services/pretrade_checks.py`

`run_pretrade_check(engine, host, port, client_id, con_id, side, quantity, account_id)`:

- Looks up the contract from DB by `con_id`
- Connects to IB, qualifies the contract, runs `whatIfOrder` for margin impact
- Fetches reference price and computes notional
- Returns margin before/after, commission, reference price, and notional

### Job Type: `pretrade.check`

Payload:

```json
{
  "con_id": 12345,
  "side": "BUY",
  "quantity": 1,
  "account_id": 1
}
```

## Key Files

| File                                                     | Role                                                      |
| -------------------------------------------------------- | --------------------------------------------------------- |
| `src/models.py`                                          | `ContractRef` model                                       |
| `alembic/versions/20260220000000_add_contracts_table.py` | Migration                                                 |
| `src/services/contract_sync.py`                          | IB -> DB sync logic                                       |
| `src/services/pretrade_checks.py`                        | Margin/pricing checks via IB                              |
| `src/services/cl_contracts.py`                           | Pure utility functions (expiry parsing, month formatting) |
| `src/services/jobs.py`                                   | Job type constants                                        |
| `scripts/work_jobs.py`                                   | Job handlers for `contracts.sync` and `pretrade.check`    |
| `src/services/tradebot_agent.py`                         | Agent tools (reads DB, enqueues jobs, never touches IB)   |
