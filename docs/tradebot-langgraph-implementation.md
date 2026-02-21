# Tradebot LangGraph Implementation

## Summary

This change replaces rule-based chat intent parsing with an LLM-driven LangGraph workflow.

Tradebot now:

- uses conversation history (not only last message)
- uses tool calls for DB reads and operational actions
- can enqueue jobs and queue orders through explicit tools
- keeps durable execution in existing workers (`worker:jobs`, `worker:orders`)

## What Was Built

### Backend

- Added LangGraph agent service: `src/services/tradebot_agent.py`
- Replaced regex chat router with thin adapter: `src/api/routers/tradebot.py`
- Added string env helper for typed config loading: `src/utils/env_vars.py`
- Added dependency: `langgraph` in `pyproject.toml`

### Frontend

- Updated chat transport to send full message history:
  - `frontend/src/components/TradebotChat.tsx`
- Preserved current endpoint contract (`POST /api/v1/tradebot/chat`, plain text response)

### Configuration

- Added LLM env examples in `.env.example`:
  - `TRADEBOT_LLM_API_KEY` (or `OPENAI_API_KEY`)
  - `TRADEBOT_LLM_MODEL`
  - `TRADEBOT_LLM_BASE_URL`
  - `TRADEBOT_LLM_TIMEOUT_SECONDS`

## LangGraph Workflow

State graph:

- `model` node: calls OpenAI-compatible `chat/completions`
- `tools` node: executes requested function tools
- conditional edge:
  - if tool calls exist -> loop to `tools`
  - if final assistant response -> end
  - if max tool steps reached -> return tool-step-limit response

Runtime constraints:

- max chat history sent to model: 16 messages
- max tool loop iterations: 8

## Tool Surface

| Function                     | Description                                                 | Any Guardrails                                                                                                                                    |
| ---------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_accounts`              | Lists available brokerage accounts for routing.             | Read-only DB query.                                                                                                                               |
| `list_positions`             | Returns current positions from the database.                | Read-only DB query. Optional `limit` constrained to 1-200.                                                                                        |
| `list_jobs`                  | Returns recent job queue records.                           | Read-only DB query. `limit` constrained to 1-200.                                                                                                 |
| `list_orders`                | Returns recent orders and optional recent events per order. | Read-only DB query. `limit` constrained to 1-200; `events_per_order` constrained to 1-20.                                                         |
| `enqueue_positions_sync_job` | Enqueues a `positions.sync` job for `worker:jobs`.          | Writes to `jobs` queue only. `max_attempts` constrained to 1-10.                                                                                  |
| `submit_cl_order`            | Queues a CL futures order for `worker:orders`.              | Requires `operator_confirmed=true`; validates side/quantity; resolves account; requires `BROKER_TWS_PORT`; qualifies CL contract before queueing. |

## Safety and Side Effects

- `submit_cl_order` requires `operator_confirmed=true`.
- Order tool queues an `orders` row; it does not bypass workers.
- Position sync tool enqueues `positions.sync` jobs for `worker:jobs`.
- Tool failures are returned to the LLM as structured tool error payloads.
- DB session rolls back on tool exceptions.

## Operational Notes

- Contract qualification for CL still requires TWS/Gateway connectivity.
- Tool-driven order queueing still uses existing `Order` + `OrderEvent` lifecycle.
- Existing jobs/orders side panels remain valid because data surfaces did not change.

## Validation

Static checks run during implementation:

- Ruff: passed
- Pyright: passed
