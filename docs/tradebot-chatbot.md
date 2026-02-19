# Tradebot Chatbot

## Purpose

`/api/v1/tradebot/chat` is the chat control surface for operator requests.

It currently supports:

- Position summaries
- Queueing `positions.sync` jobs
- Queueing CL futures orders
- Order/job progress summaries

## Request/Response Model

- Frontend uses Vercel AI SDK `useChat` with `TextStreamChatTransport`.
- Client sends `messages[]` (`role` + `parts[]`).
- Server reads latest user text and returns plain text.

## Intent Routing

Text is routed by simple intent parsing:

- Position sync intent (`refresh/sync/fetch positions`) -> enqueue `jobs` row (`job_type=positions.sync`)
- CL order intent (`buy|sell <qty> CL`) -> qualify front-month CL via TWS, then enqueue `orders` row
- Status/progress intent -> summarize latest jobs/orders
- Fallback -> usage hints

## Current Constraints

- Order intent currently supports only `CL` futures.
- Account resolution uses `account <id|alias|account_number>` when provided; otherwise first account row.
- Contract qualification requires TWS/Gateway connectivity.

## UI Components

- `TradebotChat` main chat panel
- `JobsTable` side panel (job timing + actions)
- `OrdersSideTable` side panel (order timing + status/fill)
- Header worker lights from `/api/v1/workers/status`

## Key Files

- `src/api/routers/tradebot.py`
- `frontend/src/components/TradebotChat.tsx`
- `frontend/src/components/JobsTable.tsx`
- `frontend/src/components/OrdersSideTable.tsx`
