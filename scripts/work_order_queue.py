"""
Order queue worker.

Polls queued orders, submits them to TWS/Gateway, and stores status/fill progress.

Usage:
  uv run python scripts/work_order_queue.py --env dev
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Any

from dotenv import load_dotenv
from ib_async import Contract, IB, MarketOrder, Trade
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from src.db import get_engine
from src.models import Account, Order
from src.services.cl_contracts import select_front_month_contract
from src.services.order_queue import apply_order_progress, append_order_event, now_utc
from src.services.worker_heartbeat import WORKER_TYPE_ORDERS, upsert_worker_heartbeat
from src.utils.env_vars import get_int_env
from src.utils.ibkr_account import mask_ibkr_account


def load_env(env_name: str) -> None:
    env_file = f".env.{env_name}"
    if not os.path.exists(env_file):
        raise FileNotFoundError(f"{env_file} not found")
    load_dotenv(env_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process queued orders and execute in TWS.")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--client-id", type=int, default=30)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--order-timeout-seconds", type=float, default=45.0)
    parser.add_argument("--once", action="store_true", help="Process one queue pass and exit.")
    return parser.parse_args()


def check_db_ready() -> None:
    inspector = inspect(get_engine())
    tables = inspector.get_table_names()
    for required in ("accounts", "orders", "order_events", "worker_heartbeats"):
        if required not in tables:
            raise SystemExit(f"Missing '{required}' table. Run: task migrate")


def parse_float(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_or_qualify_contract(ib: IB, order: Order) -> Contract:
    if order.con_id:
        contract_kwargs: dict[str, Any] = {
            "conId": order.con_id,
            "symbol": order.symbol,
            "secType": order.sec_type,
            "exchange": order.exchange,
            "currency": order.currency,
        }
        if order.local_symbol is not None:
            contract_kwargs["localSymbol"] = order.local_symbol
        if order.trading_class is not None:
            contract_kwargs["tradingClass"] = order.trading_class
        contract = Contract(**contract_kwargs)
        qualified = ib.qualifyContracts(contract)
        if len(qualified) == 1:
            return qualified[0]

    if order.symbol == "CL":
        return select_front_month_contract(ib)

    fallback = Contract(
        symbol=order.symbol,
        secType=order.sec_type,
        exchange=order.exchange,
        currency=order.currency,
    )
    qualified = ib.qualifyContracts(fallback)
    if len(qualified) != 1:
        raise RuntimeError(f"Could not qualify contract for order {order.id}")
    return qualified[0]


def sync_trade_progress(session: Session, order: Order, trade: Trade, event_type: str) -> None:
    changed = apply_order_progress(
        order=order,
        ib_status=trade.orderStatus.status,
        filled_quantity=trade.filled(),
        avg_fill_price=parse_float(trade.orderStatus.avgFillPrice),
        ib_order_id=trade.order.orderId,
        ib_perm_id=trade.order.permId,
    )
    if changed:
        append_order_event(
            session,
            order,
            event_type=event_type,
            message=(
                f"IB status={trade.orderStatus.status}, filled={trade.filled()}, "
                f"remaining={trade.remaining()}, avgFill={trade.orderStatus.avgFillPrice}"
            ),
        )


def process_order(
    ib: IB,
    session: Session,
    order: Order,
    timeout_seconds: float,
) -> None:
    account = session.get(Account, order.account_id)
    if account is None:
        order.status = "failed"
        order.last_error = f"Missing account row for account_id={order.account_id}"
        order.updated_at = now_utc()
        append_order_event(session, order, "order_error", order.last_error or "Unknown error")
        return

    try:
        contract = get_or_qualify_contract(ib, order)
        order.con_id = contract.conId
        order.local_symbol = contract.localSymbol
        order.trading_class = contract.tradingClass
        order.contract_expiry = contract.lastTradeDateOrContractMonth
        order.updated_at = now_utc()
        append_order_event(
            session,
            order,
            "contract_qualified",
            f"Qualified conId={contract.conId}, localSymbol={contract.localSymbol}",
        )

        tws_account = account.account
        managed_accounts = ib.managedAccounts()
        if tws_account not in managed_accounts:
            masked = mask_ibkr_account(tws_account)
            raise RuntimeError(f"Account {masked} is not managed by this IBKR session")

        ib_order = MarketOrder(order.side, order.quantity)
        ib_order.account = tws_account
        ib_order.tif = order.tif

        trade = ib.placeOrder(contract, ib_order)
        order.submitted_at = now_utc()
        sync_trade_progress(session, order, trade, event_type="order_submitted")
        session.flush()

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and not trade.isDone():
            ib.waitOnUpdate(timeout=1.0)
            sync_trade_progress(session, order, trade, event_type="order_progress")
            session.flush()

        sync_trade_progress(session, order, trade, event_type="order_final")
        if trade.advancedError:
            order.last_error = trade.advancedError
            order.updated_at = now_utc()
            append_order_event(session, order, "ib_advanced_error", trade.advancedError)
    except Exception as exc:
        order.status = "failed"
        order.last_error = str(exc)
        order.updated_at = now_utc()
        if order.completed_at is None:
            order.completed_at = now_utc()
        append_order_event(session, order, "order_error", f"Worker error: {exc}")


def run_worker(args: argparse.Namespace) -> int:
    port = args.port if args.port is not None else get_int_env("BROKER_TWS_PORT")
    if port is None:
        raise SystemExit("BROKER_TWS_PORT is not set. Pass --port or set BROKER_TWS_PORT.")
    engine = get_engine()
    ib = IB()
    upsert_worker_heartbeat(
        engine,
        WORKER_TYPE_ORDERS,
        status="starting",
        details=f"connecting to {args.host}:{port}",
    )

    try:
        ib.connect(args.host, port, clientId=args.client_id)
        print(f"Connected to TWS/Gateway at {args.host}:{port}.")
        upsert_worker_heartbeat(
            engine,
            WORKER_TYPE_ORDERS,
            status="running",
            details=f"connected to {args.host}:{port}",
        )

        while True:
            processed = 0
            with Session(engine) as session:
                stmt = (
                    select(Order)
                    .where(Order.status == "queued")
                    .order_by(Order.created_at.asc())
                    .limit(20)
                )
                orders = list(session.execute(stmt).scalars().all())
                for order in orders:
                    process_order(
                        ib=ib,
                        session=session,
                        order=order,
                        timeout_seconds=args.order_timeout_seconds,
                    )
                    processed += 1
                session.commit()

            upsert_worker_heartbeat(
                engine,
                WORKER_TYPE_ORDERS,
                status="running",
                details=f"processed={processed}, tws_connected={ib.isConnected()}",
            )

            if args.once:
                print(f"Processed {processed} order(s).")
                return 0

            if processed == 0:
                time.sleep(args.poll_seconds)
    finally:
        try:
            upsert_worker_heartbeat(
                engine,
                WORKER_TYPE_ORDERS,
                status="stopped",
                details="worker exiting",
            )
        except Exception:
            pass
        if ib.isConnected():
            ib.disconnect()
            print("Disconnected from TWS/Gateway.")


def main() -> int:
    args = parse_args()
    load_env(args.env)
    check_db_ready()
    return run_worker(args)


if __name__ == "__main__":
    raise SystemExit(main())
