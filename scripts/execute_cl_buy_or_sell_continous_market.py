"""
Place a market BUY or SELL order for the current CL front-month futures contract (NYMEX).

Usage:
  # Load .env.dev, show margin + notional, then prompt for confirmation:
  uv run python scripts/execute_cl_buy_or_sell_continous_market.py --env dev --side buy --qty 1

  # Submit with 1Password secret resolution:
  op run --env-file=.env.dev -- uv run python scripts/execute_cl_buy_or_sell_continous_market.py --env dev --side sell --qty 1
"""

from __future__ import annotations

import argparse
import math
import os
import time

from dotenv import load_dotenv
from ib_async import Contract, IB, MarketOrder, Order, OrderState, Ticker, Trade

from src.services.cl_contracts import (
    DEFAULT_CL_MIN_DAYS_TO_EXPIRY,
    format_contract_month,
    select_front_month_contract,
)
from src.utils.env_vars import get_int_env
from src.utils.ibkr_account import mask_ibkr_account


def load_env(env_name: str) -> None:
    env_file = f".env.{env_name}"
    if not os.path.exists(env_file):
        raise FileNotFoundError(f"{env_file} not found")
    load_dotenv(env_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Place a market BUY or SELL order for the current CL front-month futures contract."
    )
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument(
        "--side",
        choices=["buy", "sell"],
        default="buy",
        help="Order direction.",
    )
    parser.add_argument("--qty", type=int, default=1, help="Number of contracts.")
    parser.add_argument(
        "--host", default="127.0.0.1", help="TWS/Gateway host. Default: 127.0.0.1."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="TWS/Gateway port. If omitted, uses BROKER_TWS_PORT or 7497.",
    )
    parser.add_argument("--client-id", type=int, default=2, help="IBKR API client ID.")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=10.0,
        help="How long to wait for status updates before printing final snapshot.",
    )
    parser.add_argument(
        "--account",
        default=None,
        help="IBKR account ID. If omitted, the first managed account is used.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive prompt and submit immediately after checks.",
    )
    return parser.parse_args()


def parse_float(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or abs(parsed) > 1e307:
        return None
    return parsed


def format_money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def choose_account(ib: IB, requested_account: str | None) -> str:
    accounts = ib.managedAccounts()
    if requested_account:
        if requested_account not in accounts:
            raise RuntimeError(
                "Requested account "
                f"{mask_ibkr_account(requested_account)} is not managed by this session"
            )
        return requested_account
    if not accounts:
        raise RuntimeError("No managed accounts found from IBKR session")
    return accounts[0]


def get_current_margin(ib: IB, account: str) -> tuple[float | None, float | None]:
    init_margin: float | None = None
    maint_margin: float | None = None
    for value in ib.accountSummary(account):
        if value.tag == "InitMarginReq":
            init_margin = parse_float(value.value)
        elif value.tag == "MaintMarginReq":
            maint_margin = parse_float(value.value)
    return init_margin, maint_margin


def get_reference_price(ticker: Ticker) -> float | None:
    for price_candidate in (ticker.marketPrice(), ticker.last, ticker.close):
        price = parse_float(price_candidate)
        if price is not None and price > 0:
            return price
    return None


def get_what_if_state(ib: IB, contract: Contract, order: Order) -> OrderState:
    response = ib.whatIfOrder(contract, order)
    if isinstance(response, list):
        raise RuntimeError(
            "IBKR did not return what-if margin state. This is often caused by "
            "IBKR warning code 10349 when TIF is not set explicitly."
        )
    return response


def print_trade_snapshot(trade: Trade) -> None:
    print(
        "Trade status:"
        f" status={trade.orderStatus.status}"
        f", filled={trade.filled()}"
        f", remaining={trade.remaining()}"
    )


def wait_for_updates(ib: IB, trade: Trade, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status = ""

    while not trade.isDone() and time.monotonic() < deadline:
        ib.waitOnUpdate(timeout=1.0)
        current_status = trade.orderStatus.status
        if current_status != last_status:
            print_trade_snapshot(trade)
            last_status = current_status


def main() -> int:
    args = parse_args()
    if args.qty <= 0:
        raise SystemExit("--qty must be >= 1")

    load_env(args.env)
    port = args.port or get_int_env("BROKER_TWS_PORT", 7497)
    min_days_to_expiry = get_int_env(
        "BROKER_CL_MIN_DAYS_TO_EXPIRY", DEFAULT_CL_MIN_DAYS_TO_EXPIRY
    )
    if min_days_to_expiry is None or min_days_to_expiry < 0:
        raise SystemExit("BROKER_CL_MIN_DAYS_TO_EXPIRY must be >= 0.")

    action = args.side.upper()

    ib = IB()
    try:
        ib.connect(args.host, port, clientId=args.client_id)
        print("Connected to TWS/Gateway.")

        account = choose_account(ib, args.account)
        print(f"Using account: {mask_ibkr_account(account)}")

        qualified_contract = select_front_month_contract(
            ib, min_days_to_expiry=min_days_to_expiry
        )
        contract_month = format_contract_month(qualified_contract) or "unknown"
        contract_expiry = qualified_contract.lastTradeDateOrContractMonth

        print("Order intent:")
        print(f"  Action: {action}")
        print(f"  Quantity: {args.qty}")
        print(f"  Contract: CL {contract_month} (NYMEX)")
        print(f"  Min Days To Expiry: {min_days_to_expiry}")
        print(f"  Connection: {args.host}:{port} (clientId={args.client_id})")

        print(
            "Qualified contract:"
            f" conId={qualified_contract.conId}"
            f", localSymbol={qualified_contract.localSymbol}"
            f", expiry={contract_expiry}"
            f", tradingClass={qualified_contract.tradingClass}"
        )

        order = MarketOrder(action, args.qty)
        order.account = account
        order.tif = "DAY"

        current_init_margin, current_maint_margin = get_current_margin(ib, account)
        what_if_numeric = get_what_if_state(ib, qualified_contract, order).numeric(
            digits=2
        )
        expected_init_margin = parse_float(what_if_numeric.initMarginAfter)
        expected_maint_margin = parse_float(what_if_numeric.maintMarginAfter)

        tickers = ib.reqTickers(qualified_contract)
        reference_price = get_reference_price(tickers[0]) if tickers else None
        multiplier = parse_float(qualified_contract.multiplier)
        notional = (
            args.qty * reference_price * multiplier
            if reference_price is not None and multiplier is not None
            else None
        )

        print()
        print("Pre-trade checks:")
        print(f"  Current Initial Margin: {format_money(current_init_margin)}")
        print(f"  Current Maintenance Margin: {format_money(current_maint_margin)}")
        print(f"  Expected Initial Margin (post-trade): {format_money(expected_init_margin)}")
        print(
            "  Expected Maintenance Margin (post-trade):"
            f" {format_money(expected_maint_margin)}"
        )
        if reference_price is not None:
            print(f"  Reference Price: ${reference_price:,.2f}")
        else:
            print("  Reference Price: N/A (market data unavailable)")
        if multiplier is not None:
            print(f"  Contract Multiplier: {multiplier:,.0f}")
        else:
            print("  Contract Multiplier: N/A")
        print(f"  Notional Traded Size (est.): {format_money(notional)}")

        if not args.yes:
            print()
            confirmation = input(
                f"Type {action} to submit this live order: "
            ).strip().upper()
            if confirmation != action:
                print("Order cancelled by user.")
                return 0

        trade = ib.placeOrder(qualified_contract, order)
        print(f"Submitted orderId={trade.order.orderId} (type=MKT).")

        wait_for_updates(ib, trade, args.timeout_seconds)
        print_trade_snapshot(trade)
        if trade.advancedError:
            print(f"Advanced error: {trade.advancedError}")
    finally:
        if ib.isConnected():
            ib.disconnect()
            print("Disconnected.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
