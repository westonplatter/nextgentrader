# Tradebot Chatbot

## Purpose

`/api/v1/tradebot/chat` is the operator chat control surface.

It now runs as an LLM conversation workflow with LangGraph and explicit function tools.

## Architecture

- Frontend uses Vercel AI SDK `useChat` with `TextStreamChatTransport`.
- Client sends chat history (`messages[]`) to preserve conversation context.
- FastAPI router (`src/api/routers/tradebot.py`) normalizes chat messages and calls the agent service.
- Agent service (`src/services/tradebot_agent.py`) runs a LangGraph state machine:
  - `model` node: calls an OpenAI-compatible `chat/completions` model
  - `tools` node: executes requested tool calls against DB/workflows
  - conditional routing loops until final assistant response or tool-step limit

## Available Tools

Read tools:

- `list_accounts`
- `list_positions`
- `list_jobs`
- `list_orders`

Action tools:

- `enqueue_positions_sync_job`
- `submit_cl_order`

## Safety Constraints

- `submit_cl_order` requires `operator_confirmed=true` in tool args.
- Orders are queued in DB for `worker:orders`; the chat endpoint does not submit directly to broker.
- Positions sync is queued for `worker:jobs` via `positions.sync` jobs.
- If an action tool fails, the tool call returns an explicit error payload back to the model.

## Environment Variables

- `TRADEBOT_LLM_API_KEY` (or fallback `OPENAI_API_KEY`)
- `TRADEBOT_LLM_MODEL` (default `gpt-4.1-mini`)
- `TRADEBOT_LLM_BASE_URL` (default `https://api.openai.com/v1`)
- `TRADEBOT_LLM_TIMEOUT_SECONDS` (default `45`)
- `BROKER_TWS_PORT` (required for CL qualification when queueing orders)
- `BROKER_CL_MIN_DAYS_TO_EXPIRY` (default `7`; skip CL contracts too close to expiry)
- `TRADEBOT_QUALIFY_CLIENT_ID` (default `29`)

## UI Components

- `TradebotChat` main chat panel
- `JobsTable` side panel (job timing + actions)
- `OrdersSideTable` side panel (order timing + status/fill)
- Header worker lights from `/api/v1/workers/status`

## Key Files

- `src/api/routers/tradebot.py`
- `src/services/tradebot_agent.py`
- `frontend/src/components/TradebotChat.tsx`
- `frontend/src/components/JobsTable.tsx`
- `frontend/src/components/OrdersSideTable.tsx`
