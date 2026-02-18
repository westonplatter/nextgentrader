# Execute CL Futures Order Script

Use this to place a market `BUY` or `SELL` for the current CL front month with a confirmation step.

## Prereqs

- TWS or IB Gateway is running
- `BROKER_TWS_PORT` is available from `.env.dev`
- If `.env.dev` uses `op://...`, use `op run`

## Commands

### With 1Password env resolution (recommended)

```bash
op run --env-file=.env.dev -- uv run python scripts/execute_cl_buy_or_sell_continous_market.py --env dev --side buy --qty 1
```

```bash
op run --env-file=.env.dev -- uv run python scripts/execute_cl_buy_or_sell_continous_market.py --env dev --side sell --qty 1
```

### Without 1Password resolution

Use this only if `.env.dev` has a numeric `BROKER_TWS_PORT`, or pass `--port`.

```bash
uv run python scripts/execute_cl_buy_or_sell_continous_market.py --env dev --side buy --qty 1 --port 7497
```

## Behavior

- Script queries IBKR and selects the nearest non-expired CL futures contract
- `Order intent` shows the resolved contract month (example: `Contract: CL 2026-03 (NYMEX)`)
- Script prints current margin, expected post-trade margin (what-if), and estimated notional traded size
- Script prompts for explicit confirmation (`BUY` or `SELL`) before sending the live order
- Use `--yes` to skip the prompt
