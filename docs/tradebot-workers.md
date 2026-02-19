# Tradebot Workers

## Purpose

Workers run as separate processes and consume DB queues.

- `worker:jobs` handles generic background jobs (`jobs` table).
- `worker:orders` handles live TWS execution (`orders` table).

## Construction

### Jobs Worker

- Entrypoint: `scripts/work_jobs.py`
- Queue primitive: `src/services/jobs.py`
- Current handler map:
  - `positions.sync` -> `src/services/position_sync.py`
- Claims queued jobs, runs handler, writes `result`/`status`, retries until `max_attempts`.

### Orders Worker

- Entrypoint: `scripts/work_order_queue.py`
- Reads queued orders, qualifies contracts, submits to TWS, updates fill/status lifecycle.
- Writes audit trail to `order_events`.

## Heartbeats and Health

- Heartbeats stored in `worker_heartbeats`.
- Helper: `src/services/worker_heartbeat.py`
- API status endpoint: `GET /api/v1/workers/status`
- UI header lights map heartbeat freshness to green/yellow/red.

## Data Flow

1. Chat/API enqueues a row in `jobs` or `orders`.
2. Worker claims row and performs external side effect (DB sync or TWS submit).
3. Worker updates lifecycle fields and timestamps.
4. UI polls tables and displays queue/run/total timing.

## Start Commands

```bash
ENV=dev task worker:jobs
ENV=dev task worker:orders
```

Both commands run under `op run --env-file=.env.<env>` to resolve `op://` references.

## Key Files

- `scripts/work_jobs.py`
- `scripts/work_order_queue.py`
- `src/services/jobs.py`
- `src/services/position_sync.py`
- `src/services/worker_heartbeat.py`
- `src/api/routers/workers.py`
