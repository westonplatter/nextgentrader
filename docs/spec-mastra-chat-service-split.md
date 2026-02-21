# Mastra Chat Service Split Spec

## Goal

Split language chat from execution systems.

- `FastAPI`: order + data management system of record
- `Mastra`: language orchestration and chat UX backend

This keeps trade-critical behavior deterministic and auditable while allowing faster iteration on conversational UX.

## Non-Goals

- No direct broker execution from Mastra
- No direct DB writes from Mastra
- No replacement of existing workers in this phase

## System Boundaries

### FastAPI Owns

- Brokerage account/order/position data access
- Validation and risk controls for actions
- Job and order queue writes
- Idempotency enforcement
- Durable audit records for mutating actions

### Mastra Owns

- Session memory and response generation
- Tool selection and argument shaping
- User-facing explanations and clarifying questions
- Plan generation before action execution

## Trust Model

- Mastra is an untrusted caller from a trade-safety perspective.
- FastAPI validates all action requests regardless of Mastra output.
- Mutations require explicit operator confirmation and an idempotency key.

## Integration Pattern

Mastra uses FastAPI as its only tool backend.

- Read tools call read-only FastAPI endpoints.
- Action tools use a two-step protocol:
  - `plan`: validate inputs and return executable preview
  - `execute`: requires confirmation token + idempotency key

## API Contract (Internal)

Base path suggestion: `/api/internal/v1/mastra`

### Read Endpoints

- `GET /accounts`
- `GET /positions?account_id=&limit=`
- `GET /orders?account_id=&status=&limit=`
- `GET /jobs?kind=&status=&limit=`

Rules:

- Read-only
- Cursor pagination for high-volume endpoints
- Stable response schemas with explicit versioning

### Action Endpoints

#### 1) Create Action Plan

`POST /actions/plan`

Request:

- `action_type`: `positions.sync | order.submit.cl | order.cancel | ...`
- `payload`: typed object by action
- `requested_by`: operator identity
- `chat_context`: optional trace info

Response:

- `plan_id`
- `normalized_payload`
- `risk_checks`: pass/fail with reasons
- `preview`: human-readable summary
- `requires_confirmation`: boolean
- `confirmation_token` (short TTL, signed)
- `expires_at`

#### 2) Execute Planned Action

`POST /actions/execute`

Request:

- `plan_id`
- `confirmation_token`
- `idempotency_key`
- `requested_by`

Response:

- `action_id`
- `status`: `queued | rejected | duplicate`
- `queue_target`: `worker:orders | worker:jobs`
- `resource_refs`: e.g. `order_id`, `job_id`

Execution rules:

- Reject expired/invalid confirmation token
- Reject if idempotency key replays with mismatched payload
- Queue work; do not synchronously call broker

## Example Tool Mapping in Mastra

- `list_accounts` -> `GET /accounts`
- `list_positions` -> `GET /positions`
- `list_orders` -> `GET /orders`
- `enqueue_positions_sync` -> `POST /actions/plan` + `POST /actions/execute`
- `submit_cl_order` -> `POST /actions/plan` + `POST /actions/execute`

## AuthN/AuthZ

### Service-to-Service

- Mastra -> FastAPI uses machine auth (JWT client credentials or mTLS)
- `aud` restricted to internal FastAPI API
- Short token TTL (5-15 min)

### Authorization

- Scopes separated by intent:
  - `trade.read`
  - `trade.plan`
  - `trade.execute`
- FastAPI enforces scope + operator identity on action endpoints

## Safety Controls

- `plan -> confirm -> execute` required for every mutation
- Hard server-side validation for side, quantity, account, symbol, contract expiry
- Policy checks in FastAPI (max size, allowed symbols/accounts, market-hours rules)
- Rate limiting on execute endpoints
- Kill switch: global `trade_execute_enabled=false`

## Audit and Traceability

Persist an action audit record for both plan and execute:

- `action_id`, `plan_id`, `idempotency_key`
- operator id + service principal
- request payload hash
- risk-check results
- execution decision + reason
- timestamps
- linked `order_id` or `job_id`
- LLM trace fields (`chat_session_id`, `tool_call_id`) for correlation

## Failure Semantics

- Mastra timeout: safe to retry with same idempotency key
- FastAPI 5xx during execute: retry with same idempotency key
- Validation/risk rejection: terminal; return structured reason to user
- Worker failure: surfaced asynchronously in order/job state; chat reports status, not inferred success

## Observability

Track per-service metrics:

- plan requests, execute requests, execute acceptance rate
- duplicate idempotency hits
- rejection reasons by type
- p95/p99 latency per endpoint
- tool error rate in Mastra

## Deployment Shape

- Keep FastAPI and workers in current runtime boundary
- Deploy Mastra as separate service
- Private network path Mastra -> FastAPI only
- No inbound broker/network credentials in Mastra

## Rollout Plan

1. Read-only cutover

- Mastra serves chat, reads from FastAPI only

2. Action planning only

- Enable `actions/plan`, return previews, no execute

3. Guarded execution for one action

- Enable `order.submit.cl` execute with strict limits

4. Broaden action surface

- Add cancel/replace/sync actions after audit + metrics review

## Minimal First Implementation

- Add internal FastAPI namespace `/api/internal/v1/mastra`
- Implement:
  - read endpoints (`accounts`, `positions`, `orders`, `jobs`)
  - `actions/plan` for `positions.sync` and `order.submit.cl`
  - `actions/execute` with idempotency + signed confirmation token
- Add audit table for plan/execute lifecycle
- Point Mastra tools to these endpoints only

## Open Questions

- Operator identity source of truth (frontend session vs signed upstream header)
- Confirmation UX location (Mastra UI vs existing frontend)
- Scope of first risk-policy set (symbol allowlist, size limits, schedule windows)
- Whether to expose Server-Sent Events for long-running action status updates
