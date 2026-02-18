# Download IBKR Positions to Postgres

Pull live positions from TWS/IB Gateway and store them in the `positions` table.

## Prereqs

- Postgres is running on `DB_HOST:DB_PORT` (defaults to `localhost:5432`)
- TWS or IB Gateway is running
- `.env.dev` has `BROKER_TWS_PORT`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`

## First-time setup

Create the database and run migrations:

```bash
op run --env-file=.env.dev -- uv run python scripts/setup_db.py --env dev
```

This connects to the `postgres` maintenance DB, creates `ngtrader_dev` if needed, and runs `alembic upgrade head`.

## Download positions

```bash
op run --env-file=.env.dev -- uv run python scripts/download_positions.py --env dev
```

- Connects to TWS (clientId=2) and calls `ib.positions()`
- Upserts each position into the `positions` table (keyed on `account` + `con_id`)
- Prints a summary of positions saved

## Verify

```bash
psql -d ngtrader_dev -c "SELECT account, symbol, sec_type, position, avg_cost, fetched_at FROM positions;"
```

## Key files

| File | Purpose |
|------|---------|
| `src/db.py` | Builds SQLAlchemy engine from env vars |
| `src/models.py` | `Position` SQLAlchemy model |
| `src/schemas.py` | Pandera schema for positions DataFrame validation |
| `scripts/setup_db.py` | Creates DB + runs migrations |
| `scripts/download_positions.py` | Pulls positions from TWS, saves to DB |
| `alembic/` | Migration files (Rails-style datetime naming) |
