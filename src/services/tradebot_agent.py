"""LLM-backed tradebot agent with LangGraph tool workflows."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Sequence, TypedDict
from urllib import error, request

from ib_async import Contract, Future, IB
from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models import Account, Job, Order, OrderEvent, Position
from src.services.cl_contracts import (
    DEFAULT_CL_MIN_DAYS_TO_EXPIRY,
    contract_days_to_expiry,
    format_contract_month,
    parse_contract_expiry,
    to_qualified_contract,
)
from src.services.jobs import JOB_TYPE_POSITIONS_SYNC, enqueue_job
from src.services.order_queue import append_order_event, now_utc
from src.utils.env_vars import get_int_env, get_str_env
from src.utils.ibkr_account import mask_ibkr_account

_SYSTEM_PROMPT = (
    "You are Tradebot, an operations assistant for a live trading desk. "
    "Use tools for factual data access and side effects. "
    "Never fabricate DB data, job IDs, order IDs, fills, or statuses. "
    "When a user asks for current state, call read tools first. "
    "You can enqueue positions sync jobs and queue CL orders. "
    "For CL order requests, call preview_cl_order before asking for confirmation. "
    "If the user specifies a contract month, pass it as tool arg contract_month (YYYY-MM or month name). "
    "Include contract month and account label in that confirmation message. "
    "If requested month is unavailable, explain that and ask whether to proceed with available month. "
    "Before submitting an order, ensure the user clearly asked to place/queue/submit it. "
    "Keep responses concise and operator-focused."
)
_MAX_MESSAGES = 16
_MAX_TOOL_STEPS = 8
_DEFAULT_LLM_MODEL = "gpt-4.1-mini"
_DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_TIMEOUT_SECONDS = 45
_TOOL_SOURCE = "tradebot-llm"

_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_accounts",
            "description": "List available brokerage accounts for order routing.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_positions",
            "description": "Read current positions from the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 25,
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_jobs",
            "description": "Read latest job queue records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 20,
                    },
                    "include_archived": {
                        "type": "boolean",
                        "default": False,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_orders",
            "description": "Read latest order records and optional recent events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 20,
                    },
                    "status": {"type": "string"},
                    "include_events": {
                        "type": "boolean",
                        "default": True,
                    },
                    "events_per_order": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 3,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enqueue_positions_sync_job",
            "description": "Enqueue a positions.sync job for worker:jobs to process.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_text": {"type": "string"},
                    "max_attempts": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 3,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_cl_order",
            "description": "Preview CL order routing details (contract month/account) without queueing an order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "side": {"type": "string", "enum": ["BUY", "SELL", "buy", "sell"]},
                    "quantity": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "account_ref": {"type": "string"},
                    "contract_month": {"type": "string"},
                },
                "required": ["side", "quantity"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_cl_order",
            "description": (
                "Queue a live CL futures order for worker:orders. "
                "This has real trading impact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "side": {"type": "string", "enum": ["BUY", "SELL", "buy", "sell"]},
                    "quantity": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "account_ref": {"type": "string"},
                    "contract_month": {"type": "string"},
                    "request_text": {"type": "string"},
                    "operator_confirmed": {"type": "boolean"},
                },
                "required": ["side", "quantity", "operator_confirmed"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass(frozen=True)
class ChatInputMessage:
    role: str
    text: str


@dataclass(frozen=True)
class _TradebotModelConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: int


@dataclass(frozen=True)
class _ClContractSelection:
    contract_month: str
    local_symbol: str | None
    contract_expiry: str | None
    con_id: int
    requested_contract_month: str | None
    requested_available: bool
    available_contract_months: tuple[str, ...]


class _GraphState(TypedDict):
    session: Session
    latest_user_text: str
    config: _TradebotModelConfig
    llm_messages: list[dict[str, Any]]
    completion: dict[str, Any] | None
    final_text: str | None
    tool_iterations: int


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _extract_latest_user_text(messages: Sequence[ChatInputMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user" and message.text.strip():
            return message.text.strip()
    raise ValueError("No user message found")


def _normalize_chat_role(role: str) -> str:
    lowered = role.lower().strip()
    if lowered == "assistant":
        return "assistant"
    return "user"


def _coerce_int_arg(
    args: dict[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = args.get(key, default)
    if not isinstance(raw, int):
        raise ValueError(f"'{key}' must be an integer.")
    if raw < minimum or raw > maximum:
        raise ValueError(f"'{key}' must be between {minimum} and {maximum}.")
    return raw


def _coerce_bool_arg(args: dict[str, Any], key: str, default: bool) -> bool:
    raw = args.get(key, default)
    if not isinstance(raw, bool):
        raise ValueError(f"'{key}' must be a boolean.")
    return raw


def _coerce_optional_str_arg(args: dict[str, Any], key: str) -> str | None:
    raw = args.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"'{key}' must be a string.")
    value = raw.strip()
    return value or None


def _resolve_account(session: Session, account_ref: str | None) -> Account:
    if account_ref is None or account_ref.lower() in {"[redacted]", "redacted"}:
        account = session.execute(select(Account).order_by(Account.id)).scalars().first()
        if account is None:
            raise ValueError("No account found. Ingest positions first so accounts are available.")
        return account

    if account_ref.isdigit():
        account = session.get(Account, int(account_ref))
        if account is not None:
            return account

    stmt = select(Account).where(
        (func.lower(Account.account) == account_ref.lower())
        | (func.lower(func.coalesce(Account.alias, "")) == account_ref.lower())
    )
    account = session.execute(stmt).scalars().first()
    if account is None:
        raise ValueError(f"Unknown account '{account_ref}'.")
    return account


def _normalize_contract_month_input(contract_month: str | None) -> str | None:
    if contract_month is None:
        return None

    raw = contract_month.strip().replace("/", "-").replace(",", " ")
    if not raw:
        return None

    compact = " ".join(raw.split())
    if len(compact) == 7 and compact[4] == "-" and compact[:4].isdigit() and compact[5:7].isdigit():
        year = int(compact[:4])
        month = int(compact[5:7])
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
        raise ValueError("contract_month must use a valid month.")

    if len(compact) == 6 and compact.isdigit():
        year = int(compact[:4])
        month = int(compact[4:6])
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
        raise ValueError("contract_month must use a valid month.")

    for fmt in ("%B %Y", "%b %Y"):
        try:
            parsed = dt.datetime.strptime(compact.title(), fmt)
            return parsed.strftime("%Y-%m")
        except ValueError:
            continue

    raise ValueError("contract_month must be YYYY-MM, YYYYMM, or a month name like 'March 2026'.")


def _display_contract_month(contract_month: str) -> str:
    try:
        parsed = dt.datetime.strptime(contract_month, "%Y-%m")
    except ValueError:
        return contract_month
    return parsed.strftime("%B %Y")


def _select_cl_contract(
    ib: IB,
    requested_contract_month: str | None,
    min_days_to_expiry: int,
    allow_fallback: bool,
) -> _ClContractSelection:
    contract_details = ib.reqContractDetails(Future("CL", exchange="NYMEX", currency="USD"))
    if not contract_details:
        raise RuntimeError("No CL futures contract details returned from IBKR")

    if min_days_to_expiry < 0:
        raise ValueError("BROKER_CL_MIN_DAYS_TO_EXPIRY must be >= 0.")

    candidates: list[tuple[dt.date, str, Contract]] = []
    for detail in contract_details:
        contract = detail.contract
        if contract is None or contract.secType != "FUT":
            continue

        expiry = parse_contract_expiry(contract.lastTradeDateOrContractMonth)
        days_to_expiry = contract_days_to_expiry(contract)
        contract_month = format_contract_month(contract)
        if (
            expiry is None
            or days_to_expiry is None
            or days_to_expiry < min_days_to_expiry
            or contract_month is None
        ):
            continue

        candidates.append((expiry, contract_month, contract))

    if not candidates:
        raise RuntimeError(
            "No CL futures contracts are available for order placement with the current expiry safety window."
        )

    candidates.sort(key=lambda item: item[0])
    contracts_by_month: dict[str, Contract] = {}
    for _, contract_month, contract in candidates:
        if contract_month not in contracts_by_month:
            contracts_by_month[contract_month] = contract

    available_months = tuple(contracts_by_month.keys())
    requested_available = (
        requested_contract_month in contracts_by_month
        if requested_contract_month is not None
        else True
    )

    if requested_contract_month and requested_available:
        selected_month = requested_contract_month
    elif requested_contract_month and not requested_available:
        if not allow_fallback:
            available_text = ", ".join(_display_contract_month(month) for month in available_months)
            raise ValueError(
                f"{_display_contract_month(requested_contract_month)} contract is not currently available for "
                f"order placement. Available contract months: {available_text}."
            )
        selected_month = available_months[0]
    else:
        selected_month = available_months[0]

    selected_contract = contracts_by_month[selected_month]
    qualified_contracts = ib.qualifyContracts(selected_contract)
    if len(qualified_contracts) != 1:
        raise RuntimeError(
            f"Expected exactly one qualified CL contract, got {len(qualified_contracts)}"
        )

    qualified = to_qualified_contract(qualified_contracts[0])
    return _ClContractSelection(
        contract_month=qualified.contract_month or selected_month,
        local_symbol=qualified.local_symbol,
        contract_expiry=qualified.contract_expiry,
        con_id=qualified.con_id,
        requested_contract_month=requested_contract_month,
        requested_available=requested_available,
        available_contract_months=available_months,
    )


def _qualify_cl_contract(
    host: str,
    port: int,
    client_id: int,
    requested_contract_month: str | None = None,
    allow_fallback: bool = True,
) -> _ClContractSelection:
    min_days_to_expiry = get_int_env(
        "BROKER_CL_MIN_DAYS_TO_EXPIRY", DEFAULT_CL_MIN_DAYS_TO_EXPIRY
    )
    if min_days_to_expiry is None:
        raise ValueError("BROKER_CL_MIN_DAYS_TO_EXPIRY must be set.")
    normalized_month = _normalize_contract_month_input(requested_contract_month)

    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id)
        return _select_cl_contract(
            ib=ib,
            requested_contract_month=normalized_month,
            min_days_to_expiry=min_days_to_expiry,
            allow_fallback=allow_fallback,
        )
    finally:
        if ib.isConnected():
            ib.disconnect()


def _tool_list_accounts(session: Session, _: str, args: dict[str, Any]) -> dict[str, Any]:
    if args:
        raise ValueError("list_accounts does not take arguments.")
    rows = session.execute(select(Account).order_by(Account.id)).scalars().all()
    return {
        "accounts": [
            {
                "id": account.id,
                "alias": account.alias,
                "masked_account": mask_ibkr_account(account.account),
            }
            for account in rows
        ]
    }


def _tool_list_positions(session: Session, _: str, args: dict[str, Any]) -> dict[str, Any]:
    limit = _coerce_int_arg(args, "limit", 25, 1, 200)
    stmt = (
        select(Position, Account)
        .outerjoin(Account, Position.account_id == Account.id)
        .order_by(func.abs(Position.position).desc())
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    positions = []
    for position, account in rows:
        account_alias = account.alias if account and account.alias else None
        positions.append(
            {
                "id": position.id,
                "account_id": position.account_id,
                "account_alias": account_alias,
                "symbol": position.symbol,
                "sec_type": position.sec_type,
                "position": position.position,
                "avg_cost": position.avg_cost,
                "local_symbol": position.local_symbol,
                "fetched_at": _iso(position.fetched_at),
            }
        )
    return {"positions": positions, "count": len(positions)}


def _tool_list_jobs(session: Session, _: str, args: dict[str, Any]) -> dict[str, Any]:
    limit = _coerce_int_arg(args, "limit", 20, 1, 200)
    include_archived = _coerce_bool_arg(args, "include_archived", False)
    stmt = select(Job)
    if not include_archived:
        stmt = stmt.where(Job.archived_at.is_(None))
    rows = session.execute(stmt.order_by(Job.created_at.desc()).limit(limit)).scalars().all()
    jobs = [
        {
            "id": job.id,
            "job_type": job.job_type,
            "status": job.status,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "last_error": job.last_error,
            "available_at": _iso(job.available_at),
            "started_at": _iso(job.started_at),
            "completed_at": _iso(job.completed_at),
            "created_at": _iso(job.created_at),
            "updated_at": _iso(job.updated_at),
        }
        for job in rows
    ]
    return {"jobs": jobs, "count": len(jobs)}


def _tool_list_orders(session: Session, _: str, args: dict[str, Any]) -> dict[str, Any]:
    limit = _coerce_int_arg(args, "limit", 20, 1, 200)
    include_events = _coerce_bool_arg(args, "include_events", True)
    events_per_order = _coerce_int_arg(args, "events_per_order", 3, 1, 20)
    status = _coerce_optional_str_arg(args, "status")

    stmt = (
        select(Order, Account)
        .outerjoin(Account, Order.account_id == Account.id)
        .order_by(Order.created_at.desc())
    )
    if status is not None:
        stmt = stmt.where(func.lower(Order.status) == status.lower())
    rows = session.execute(stmt.limit(limit)).all()

    orders: list[dict[str, Any]] = []
    for order, account in rows:
        row = {
            "id": order.id,
            "account_id": order.account_id,
            "account_alias": account.alias if account else None,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "status": order.status,
            "filled_quantity": order.filled_quantity,
            "avg_fill_price": order.avg_fill_price,
            "contract_month": order.contract_month,
            "local_symbol": order.local_symbol,
            "ib_order_id": order.ib_order_id,
            "ib_perm_id": order.ib_perm_id,
            "last_error": order.last_error,
            "created_at": _iso(order.created_at),
            "submitted_at": _iso(order.submitted_at),
            "completed_at": _iso(order.completed_at),
            "updated_at": _iso(order.updated_at),
        }
        if include_events:
            events = (
                session.execute(
                    select(OrderEvent)
                    .where(OrderEvent.order_id == order.id)
                    .order_by(OrderEvent.created_at.desc())
                    .limit(events_per_order)
                )
                .scalars()
                .all()
            )
            row["events"] = [
                {
                    "event_type": event.event_type,
                    "message": event.message,
                    "status": event.status,
                    "filled_quantity": event.filled_quantity,
                    "avg_fill_price": event.avg_fill_price,
                    "created_at": _iso(event.created_at),
                }
                for event in events
            ]
        orders.append(row)

    return {"orders": orders, "count": len(orders)}


def _tool_enqueue_positions_sync_job(session: Session, latest_user_text: str, args: dict[str, Any]) -> dict[str, Any]:
    max_attempts = _coerce_int_arg(args, "max_attempts", 3, 1, 10)
    request_text = _coerce_optional_str_arg(args, "request_text") or latest_user_text
    job = enqueue_job(
        session=session,
        job_type=JOB_TYPE_POSITIONS_SYNC,
        payload={},
        source=_TOOL_SOURCE,
        request_text=request_text,
        max_attempts=max_attempts,
    )
    session.commit()
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "max_attempts": job.max_attempts,
    }


def _tool_submit_cl_order(session: Session, latest_user_text: str, args: dict[str, Any]) -> dict[str, Any]:
    side_raw = args.get("side")
    if not isinstance(side_raw, str):
        raise ValueError("'side' must be a string.")
    side = side_raw.strip().upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("'side' must be BUY or SELL.")

    quantity = _coerce_int_arg(args, "quantity", 1, 1, 1000)
    operator_confirmed = _coerce_bool_arg(args, "operator_confirmed", False)
    if not operator_confirmed:
        raise ValueError("Order submission requires operator_confirmed=true.")

    account_ref = _coerce_optional_str_arg(args, "account_ref")
    requested_contract_month = _coerce_optional_str_arg(args, "contract_month")
    request_text = _coerce_optional_str_arg(args, "request_text") or latest_user_text
    account = _resolve_account(session, account_ref)

    host = "127.0.0.1"
    port = get_int_env("BROKER_TWS_PORT")
    if port is None:
        raise ValueError("Configuration error: BROKER_TWS_PORT is not set.")
    client_id = get_int_env("TRADEBOT_QUALIFY_CLIENT_ID", 29)

    selected_contract = _qualify_cl_contract(
        host=host,
        port=port,
        client_id=client_id,
        requested_contract_month=requested_contract_month,
        allow_fallback=False,
    )

    created_at = now_utc()
    order = Order(
        account_id=account.id,
        symbol="CL",
        sec_type="FUT",
        exchange="NYMEX",
        currency="USD",
        side=side,
        quantity=quantity,
        order_type="MKT",
        tif="DAY",
        status="queued",
        source=_TOOL_SOURCE,
        con_id=selected_contract.con_id,
        local_symbol=selected_contract.local_symbol,
        trading_class="CL",
        contract_month=selected_contract.contract_month,
        contract_expiry=selected_contract.contract_expiry,
        request_text=request_text,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(order)
    session.flush()

    append_order_event(
        session,
        order,
        event_type="order_created",
        message=(
            f"Queued by tradebot-llm for {side} {quantity} CL "
            f"({selected_contract.contract_month}, conId={selected_contract.con_id}, "
            f"account={mask_ibkr_account(account.account)})."
        ),
    )
    session.commit()

    account_label = account.alias or mask_ibkr_account(account.account)
    return {
        "order_id": order.id,
        "status": order.status,
        "side": order.side,
        "quantity": order.quantity,
        "symbol": order.symbol,
        "contract_month": order.contract_month,
        "contract_month_display": (
            _display_contract_month(order.contract_month)
            if order.contract_month is not None
            else None
        ),
        "account": account_label,
        "worker_handoff": "worker:orders",
    }


def _tool_preview_cl_order(session: Session, _: str, args: dict[str, Any]) -> dict[str, Any]:
    side_raw = args.get("side")
    if not isinstance(side_raw, str):
        raise ValueError("'side' must be a string.")
    side = side_raw.strip().upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("'side' must be BUY or SELL.")

    quantity = _coerce_int_arg(args, "quantity", 1, 1, 1000)
    account_ref = _coerce_optional_str_arg(args, "account_ref")
    requested_contract_month = _coerce_optional_str_arg(args, "contract_month")
    account = _resolve_account(session, account_ref)

    host = "127.0.0.1"
    port = get_int_env("BROKER_TWS_PORT")
    if port is None:
        raise ValueError("Configuration error: BROKER_TWS_PORT is not set.")
    client_id = get_int_env("TRADEBOT_QUALIFY_CLIENT_ID", 29)

    selected_contract = _qualify_cl_contract(
        host=host,
        port=port,
        client_id=client_id,
        requested_contract_month=requested_contract_month,
        allow_fallback=True,
    )

    account_label = account.alias or mask_ibkr_account(account.account)
    available_account_count = session.execute(select(func.count(Account.id))).scalar_one()
    selected_month_display = _display_contract_month(selected_contract.contract_month)
    requested_month_display = (
        _display_contract_month(selected_contract.requested_contract_month)
        if selected_contract.requested_contract_month is not None
        else None
    )
    quantity_label = "contract" if quantity == 1 else "contracts"
    action_label = "buying" if side == "BUY" else "selling"
    account_intro = (
        f"You have one brokerage account available: {account_label}."
        if available_account_count == 1
        else f"The selected brokerage account is {account_label}."
    )
    unavailable_response_text = None
    if (
        selected_contract.requested_contract_month is not None
        and not selected_contract.requested_available
        and requested_month_display is not None
    ):
        unavailable_response_text = (
            f"The available CL contract month for {action_label} {quantity} {quantity_label} is "
            f"{selected_month_display} on the {account_label} account. "
            f"{requested_month_display} contract is not currently available for order placement. "
            f"Would you like to proceed with the {selected_month_display} contract instead?"
        )

    return {
        "side": side,
        "quantity": quantity,
        "symbol": "CL",
        "account_id": account.id,
        "account": account_label,
        "contract_month": selected_contract.contract_month,
        "contract_month_display": selected_month_display,
        "contract_expiry": selected_contract.contract_expiry,
        "local_symbol": selected_contract.local_symbol,
        "con_id": selected_contract.con_id,
        "requested_contract_month": selected_contract.requested_contract_month,
        "requested_contract_month_display": requested_month_display,
        "requested_available": selected_contract.requested_available,
        "available_contract_months": list(selected_contract.available_contract_months),
        "available_contract_months_display": [
            _display_contract_month(month)
            for month in selected_contract.available_contract_months
        ],
        "unavailable_response_text": unavailable_response_text,
        "confirmation_text": (
            f"{account_intro} "
            f"The CL contract month to trade is {selected_month_display}. "
            f"Please confirm you want to {side.lower()} {quantity} CL contract"
            f"{'' if quantity == 1 else 's'} on this account, and I will submit the order."
        ),
    }


_TOOL_HANDLERS = {
    "list_accounts": _tool_list_accounts,
    "list_positions": _tool_list_positions,
    "list_jobs": _tool_list_jobs,
    "list_orders": _tool_list_orders,
    "enqueue_positions_sync_job": _tool_enqueue_positions_sync_job,
    "preview_cl_order": _tool_preview_cl_order,
    "submit_cl_order": _tool_submit_cl_order,
}


def _load_model_config() -> _TradebotModelConfig:
    api_key = get_str_env("TRADEBOT_LLM_API_KEY") or get_str_env("OPENAI_API_KEY")
    if api_key is None:
        raise ValueError("Missing TRADEBOT_LLM_API_KEY (or OPENAI_API_KEY).")

    base_url = get_str_env("TRADEBOT_LLM_BASE_URL", _DEFAULT_LLM_BASE_URL)
    model = get_str_env("TRADEBOT_LLM_MODEL", _DEFAULT_LLM_MODEL)
    timeout_seconds = get_int_env("TRADEBOT_LLM_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)
    return _TradebotModelConfig(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        model=model,
        timeout_seconds=timeout_seconds,
    )


def _call_llm(
    config: _TradebotModelConfig,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "model": config.model,
        "messages": messages,
        "tools": _TOOL_SPECS,
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "temperature": 0.1,
    }

    endpoint = f"{config.base_url}/chat/completions"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=config.timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Tradebot LLM HTTP {exc.code}: {details}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Tradebot LLM request failed: {exc.reason}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Tradebot LLM returned a non-JSON response.") from exc
    return parsed


def _execute_tool_call(
    session: Session,
    latest_user_text: str,
    tool_name: str,
    arguments_json: str,
) -> dict[str, Any]:
    handler = _TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return {"ok": False, "error": f"Unknown tool '{tool_name}'."}

    try:
        args_obj = json.loads(arguments_json) if arguments_json.strip() else {}
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Arguments for tool '{tool_name}' were not valid JSON."}

    if not isinstance(args_obj, dict):
        return {"ok": False, "error": f"Arguments for tool '{tool_name}' must be an object."}

    try:
        return {"ok": True, "result": handler(session, latest_user_text, args_obj)}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        return {"ok": False, "error": str(exc)}


def _extract_assistant_message(completion: dict[str, Any]) -> dict[str, Any]:
    choices = completion.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Tradebot LLM response did not include any choices.")

    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("Tradebot LLM response had an invalid message payload.")

    return message


def _model_node(state: _GraphState) -> _GraphState:
    completion = _call_llm(state["config"], state["llm_messages"])
    assistant_message = _extract_assistant_message(completion)
    assistant_text_raw = assistant_message.get("content")
    assistant_text = assistant_text_raw if isinstance(assistant_text_raw, str) else ""
    tool_calls = assistant_message.get("tool_calls")

    assistant_history_message: dict[str, Any] = {
        "role": "assistant",
        "content": assistant_text,
    }
    if isinstance(tool_calls, list) and tool_calls:
        assistant_history_message["tool_calls"] = tool_calls

    next_llm_messages = [
        *state["llm_messages"],
        assistant_history_message,
    ]

    if not isinstance(tool_calls, list) or not tool_calls:
        final_text = assistant_text.strip() or (
            "I could not complete that request with confidence. "
            "Please retry with a more specific instruction."
        )
        return {
            **state,
            "completion": completion,
            "llm_messages": next_llm_messages,
            "final_text": final_text,
        }

    return {
        **state,
        "completion": completion,
        "llm_messages": next_llm_messages,
    }


def _tools_node(state: _GraphState) -> _GraphState:
    completion = state["completion"]
    if completion is None:
        return {
            **state,
            "final_text": (
                "I could not complete that request with confidence. "
                "Please retry with a more specific instruction."
            ),
        }

    assistant_message = _extract_assistant_message(completion)
    tool_calls = assistant_message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return state

    next_llm_messages = list(state["llm_messages"])
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        call_id = call.get("id")
        function_payload = call.get("function")
        if not isinstance(call_id, str) or not isinstance(function_payload, dict):
            continue

        tool_name = function_payload.get("name")
        arguments_raw = function_payload.get("arguments")
        if not isinstance(tool_name, str):
            continue
        arguments_json = arguments_raw if isinstance(arguments_raw, str) else "{}"

        result = _execute_tool_call(
            session=state["session"],
            latest_user_text=state["latest_user_text"],
            tool_name=tool_name,
            arguments_json=arguments_json,
        )
        next_llm_messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(result),
            }
        )

    return {
        **state,
        "llm_messages": next_llm_messages,
        "tool_iterations": state["tool_iterations"] + 1,
    }


def _tool_limit_node(state: _GraphState) -> _GraphState:
    return {
        **state,
        "final_text": (
            "I reached the maximum number of tool steps for this request. "
            "Please retry with a more specific instruction."
        ),
    }


def _route_after_model(state: _GraphState) -> str:
    if state.get("final_text"):
        return "done"

    completion = state.get("completion")
    if completion is None:
        return "limit"

    message = _extract_assistant_message(completion)
    tool_calls = message.get("tool_calls")
    has_tool_calls = isinstance(tool_calls, list) and bool(tool_calls)
    if not has_tool_calls:
        return "done"

    if state["tool_iterations"] >= _MAX_TOOL_STEPS:
        return "limit"
    return "tools"


def _build_graph() -> Any:
    graph = StateGraph(_GraphState)
    graph.add_node("model", _model_node)
    graph.add_node("tools", _tools_node)
    graph.add_node("tool_limit", _tool_limit_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges(
        "model",
        _route_after_model,
        {
            "tools": "tools",
            "done": END,
            "limit": "tool_limit",
        },
    )
    graph.add_edge("tools", "model")
    graph.add_edge("tool_limit", END)
    return graph.compile()


_GRAPH_APP = _build_graph()


def run_tradebot_agent(session: Session, messages: Sequence[ChatInputMessage]) -> str:
    if not messages:
        raise ValueError("No chat messages provided.")

    latest_user_text = _extract_latest_user_text(messages)
    config = _load_model_config()

    llm_messages: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for message in list(messages)[-_MAX_MESSAGES:]:
        cleaned_text = message.text.strip()
        if not cleaned_text:
            continue
        llm_messages.append(
            {
                "role": _normalize_chat_role(message.role),
                "content": cleaned_text,
            }
        )

    initial_state: _GraphState = {
        "session": session,
        "latest_user_text": latest_user_text,
        "config": config,
        "llm_messages": llm_messages,
        "completion": None,
        "final_text": None,
        "tool_iterations": 0,
    }
    final_state = _GRAPH_APP.invoke(initial_state)
    final_text = final_state.get("final_text")
    if isinstance(final_text, str) and final_text.strip():
        return final_text.strip()

    return (
        "I could not complete that request with confidence. "
        "Please retry with a more specific instruction."
    )
