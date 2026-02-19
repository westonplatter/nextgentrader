"""Tradebot chat router for position queries and queued order creation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from ib_async import IB
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.api.deps import get_db
from src.models import Account, Job, Order, OrderEvent, Position
from src.services.cl_contracts import select_front_month_contract, to_qualified_contract
from src.services.jobs import JOB_TYPE_POSITIONS_SYNC, enqueue_job
from src.services.order_queue import append_order_event, now_utc
from src.utils.env_vars import get_int_env
from src.utils.ibkr_account import mask_ibkr_account

router = APIRouter()


class ChatPart(BaseModel):
    type: str
    text: str | None = None


class ChatMessage(BaseModel):
    role: str
    parts: list[ChatPart]


class TradebotChatRequest(BaseModel):
    messages: list[ChatMessage]


@dataclass(frozen=True)
class OrderIntent:
    side: str
    quantity: int
    symbol: str
    account_ref: str | None


ORDER_INTENT_RE = re.compile(
    r"\b(?P<side>buy|sell)\b"
    r"(?:\s+(?P<qty>\d+))?"
    r"(?:\s+(?:more|additional))?"
    r"\s+(?P<symbol>[a-z]{1,10})\b",
    re.IGNORECASE,
)
ACCOUNT_REF_RE = re.compile(r"\baccount\s+(?P<account>[a-zA-Z0-9_-]+)\b")
ORDER_ID_RE = re.compile(r"\border\s+#?(?P<order_id>\d+)\b", re.IGNORECASE)


def extract_latest_user_text(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role != "user":
            continue
        parts = [
            part.text.strip()
            for part in message.parts
            if part.type == "text" and part.text and part.text.strip()
        ]
        if parts:
            return " ".join(parts)
    raise HTTPException(status_code=400, detail="No user message found")


def parse_order_intent(message: str) -> OrderIntent | None:
    match = ORDER_INTENT_RE.search(message.lower())
    if match is None:
        return None
    side = match.group("side").upper()
    symbol = match.group("symbol").upper()
    quantity = int(match.group("qty") or 1)
    account_match = ACCOUNT_REF_RE.search(message)
    account_ref = account_match.group("account") if account_match else None
    return OrderIntent(side=side, quantity=quantity, symbol=symbol, account_ref=account_ref)


def resolve_account(session: Session, account_ref: str | None) -> Account:
    if account_ref is None:
        account = session.execute(select(Account).order_by(Account.id)).scalars().first()
        if account is None:
            raise HTTPException(
                status_code=400,
                detail="No account found. Ingest positions first so accounts are available.",
            )
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
        raise HTTPException(status_code=400, detail=f"Unknown account '{account_ref}'")
    return account


def summarize_positions(session: Session) -> str:
    stmt = (
        select(Position, Account)
        .outerjoin(Account, Position.account_id == Account.id)
        .order_by(func.abs(Position.position).desc())
    )
    rows = session.execute(stmt).all()
    if not rows:
        return "No positions found in the database."

    lines = [f"Loaded {len(rows)} position(s). Largest positions:"]
    for idx, (position, account) in enumerate(rows[:10], start=1):
        account_name = account.alias if account and account.alias else f"account_id={position.account_id}"
        symbol = position.symbol or "UNKNOWN"
        sec_type = position.sec_type or "?"
        lines.append(
            f"{idx}. {account_name}: {symbol} {sec_type} qty={position.position:.2f} avg_cost={position.avg_cost:.2f}"
        )
    return "\n".join(lines)


def is_position_sync_request(lowered_text: str) -> bool:
    has_positions_word = "position" in lowered_text or "portfolio" in lowered_text
    has_sync_verb = any(
        token in lowered_text
        for token in (
            "fetch",
            "refresh",
            "sync",
            "download",
            "update",
            "pull",
        )
    )
    return has_positions_word and has_sync_verb


def queue_position_sync_job(session: Session, prompt: str) -> str:
    job = enqueue_job(
        session=session,
        job_type=JOB_TYPE_POSITIONS_SYNC,
        payload={},
        source="tradebot",
        request_text=prompt,
    )
    session.commit()
    return f"Queued job {job.id} (positions.sync). Worker will fetch current positions from TWS."


def summarize_jobs(session: Session) -> str:
    jobs = (
        session.execute(select(Job).order_by(Job.created_at.desc()).limit(8))
        .scalars()
        .all()
    )
    if not jobs:
        return "No jobs found yet."

    lines = ["Latest jobs:"]
    for job in jobs:
        lines.append(
            f"- Job {job.id}: {job.job_type} status={job.status} attempts={job.attempts}/{job.max_attempts}"
        )
    return "\n".join(lines)


def qualify_cl_contract(host: str, port: int, client_id: int) -> tuple[str, str | None, str | None, int]:
    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id)
        qualified = to_qualified_contract(select_front_month_contract(ib))
        return (
            qualified.contract_month or "unknown",
            qualified.local_symbol,
            qualified.contract_expiry,
            qualified.con_id,
        )
    finally:
        if ib.isConnected():
            ib.disconnect()


def format_order_progress(session: Session, order_id: int | None = None) -> str:
    if order_id is not None:
        order = session.get(Order, order_id)
        if order is None:
            return f"No order found for id={order_id}."
        order_ids = [order.id]
    else:
        stmt = select(Order.id).order_by(Order.created_at.desc()).limit(5)
        order_ids = list(session.execute(stmt).scalars().all())
        if not order_ids:
            return "No orders found yet."

    lines: list[str] = []
    for oid in order_ids:
        order = session.get(Order, oid)
        if order is None:
            continue
        lines.append(
            f"Order {order.id}: {order.side} {order.quantity} {order.symbol} "
            f"status={order.status} filled={order.filled_quantity:.2f} avg_fill={order.avg_fill_price}"
        )
        events = (
            session.execute(
                select(OrderEvent)
                .where(OrderEvent.order_id == order.id)
                .order_by(OrderEvent.created_at.desc())
                .limit(3)
            )
            .scalars()
            .all()
        )
        for event in reversed(events):
            lines.append(f"- {event.created_at.isoformat()} [{event.event_type}] {event.message}")
    return "\n".join(lines)


def create_queued_cl_order(session: Session, intent: OrderIntent, prompt: str) -> str:
    if intent.symbol != "CL":
        return "Tradebot currently supports only CL futures orders. Example: buy 1 CL account 1"

    account = resolve_account(session, intent.account_ref)

    host = "127.0.0.1"
    try:
        port = get_int_env("BROKER_TWS_PORT")
        client_id = get_int_env("TRADEBOT_QUALIFY_CLIENT_ID", 29)
    except ValueError as exc:
        return f"Configuration error: {exc}"
    if port is None:
        return "Configuration error: BROKER_TWS_PORT is not set."

    try:
        contract_month, local_symbol, contract_expiry, con_id = qualify_cl_contract(
            host=host, port=port, client_id=client_id
        )
    except Exception as exc:
        return (
            "Could not qualify the CL contract from TWS. "
            f"Ensure TWS/Gateway is running on {host}:{port}. Error: {exc}"
        )

    created_at = now_utc()
    order = Order(
        account_id=account.id,
        symbol="CL",
        sec_type="FUT",
        exchange="NYMEX",
        currency="USD",
        side=intent.side,
        quantity=intent.quantity,
        order_type="MKT",
        tif="DAY",
        status="queued",
        source="tradebot",
        con_id=con_id,
        local_symbol=local_symbol,
        trading_class="CL",
        contract_month=contract_month,
        contract_expiry=contract_expiry,
        request_text=prompt,
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
            f"Queued by tradebot for {intent.side} {intent.quantity} CL "
            f"({contract_month}, conId={con_id}, account={mask_ibkr_account(account.account)})."
        ),
    )
    session.commit()

    account_label = account.alias or mask_ibkr_account(account.account)
    return (
        f"Queued order {order.id}: {intent.side} {intent.quantity} CL ({contract_month}). "
        f"Account={account_label}. Worker will submit to TWS and update progress/fills."
    )


@router.post("/tradebot/chat", response_class=PlainTextResponse)
def tradebot_chat(body: TradebotChatRequest, db: Session = Depends(get_db)) -> str:
    user_text = extract_latest_user_text(body.messages)
    lowered = user_text.lower()

    if is_position_sync_request(lowered):
        return queue_position_sync_job(db, user_text)

    order_intent = parse_order_intent(user_text)
    if order_intent is not None:
        return create_queued_cl_order(db, order_intent, user_text)

    if "position" in lowered or "portfolio" in lowered:
        return summarize_positions(db)

    if "status" in lowered or "progress" in lowered or "fill" in lowered:
        if "job" in lowered:
            return summarize_jobs(db)
        order_match = ORDER_ID_RE.search(lowered)
        order_id = int(order_match.group("order_id")) if order_match else None
        return format_order_progress(db, order_id)

    return (
        "I can help with:\n"
        "1) position queries (e.g. 'show positions')\n"
        "2) queue position sync jobs (e.g. 'refresh positions')\n"
        "3) queue CL orders (e.g. 'buy 1 more CL contracts account 1')\n"
        "4) progress checks (e.g. 'job status' or 'status for order 12')"
    )
