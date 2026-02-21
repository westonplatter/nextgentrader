"""Pre-trade margin and pricing checks via IBKR whatIfOrder."""

from __future__ import annotations

import math

from ib_async import IB, Contract, MarketOrder, Ticker
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.models import Account, ContractRef


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


def get_what_if_margin(
    ib: IB,
    contract: Contract,
    side: str,
    quantity: int,
    account: str,
) -> dict:
    order = MarketOrder(side, quantity)
    order.account = account
    order.tif = "DAY"

    response = ib.whatIfOrder(contract, order)
    if isinstance(response, list):
        raise RuntimeError(
            "IBKR did not return what-if margin state. This is often caused by "
            "IBKR warning code 10349 when TIF is not set explicitly."
        )

    numeric = response.numeric(digits=2)
    return {
        "init_margin_before": parse_float(numeric.initMarginBefore),
        "init_margin_after": parse_float(numeric.initMarginAfter),
        "maint_margin_before": parse_float(numeric.maintMarginBefore),
        "maint_margin_after": parse_float(numeric.maintMarginAfter),
        "commission": parse_float(numeric.commission),
        "warning_text": response.warningText or None,
    }


def _build_ib_contract(ref: ContractRef) -> Contract:
    """Build an ib_async Contract object from a ContractRef DB row."""
    c = Contract()
    c.conId = ref.con_id
    c.symbol = ref.symbol
    c.secType = ref.sec_type
    c.exchange = ref.exchange
    c.currency = ref.currency
    if ref.local_symbol:
        c.localSymbol = ref.local_symbol
    if ref.trading_class:
        c.tradingClass = ref.trading_class
    if ref.multiplier:
        c.multiplier = ref.multiplier
    if ref.contract_expiry:
        c.lastTradeDateOrContractMonth = ref.contract_expiry
    if ref.strike is not None:
        c.strike = ref.strike
    if ref.right:
        c.right = ref.right
    if ref.primary_exchange:
        c.primaryExchange = ref.primary_exchange
    return c


def run_pretrade_check(
    engine: Engine,
    host: str,
    port: int,
    client_id: int,
    con_id: int,
    side: str,
    quantity: int,
    account_id: int,
    connect_timeout_seconds: float = 20.0,
) -> dict:
    """Run pre-trade margin check for a specific contract and return results."""
    with Session(engine) as session:
        contract_ref = session.execute(
            select(ContractRef).where(ContractRef.con_id == con_id)
        ).scalar_one_or_none()
        if contract_ref is None:
            raise ValueError(
                f"No contract found with con_id={con_id}. Run contracts.sync first."
            )

        account = session.get(Account, account_id)
        if account is None:
            raise ValueError(f"No account found with id={account_id}.")

        account_string = account.account
        contract_info = {
            "con_id": contract_ref.con_id,
            "symbol": contract_ref.symbol,
            "local_symbol": contract_ref.local_symbol,
            "contract_month": contract_ref.contract_month,
            "contract_expiry": contract_ref.contract_expiry,
            "multiplier": contract_ref.multiplier,
            "sec_type": contract_ref.sec_type,
            "exchange": contract_ref.exchange,
        }
        ib_contract = _build_ib_contract(contract_ref)

    ib = IB()
    try:
        try:
            ib.connect(host, port, clientId=client_id, timeout=connect_timeout_seconds)
        except TimeoutError as exc:
            raise RuntimeError(
                f"Timed out connecting to TWS/Gateway for pretrade check "
                f"(host={host}, port={port}, client_id={client_id})."
            ) from exc

        qualified = ib.qualifyContracts(ib_contract)
        if len(qualified) != 1:
            raise RuntimeError(
                f"Expected exactly one qualified contract for conId={con_id}, got {len(qualified)}"
            )

        margin_info = get_what_if_margin(
            ib, qualified[0], side, quantity, account_string
        )

        tickers = ib.reqTickers(qualified[0])
        reference_price = get_reference_price(tickers[0]) if tickers else None
        multiplier = parse_float(qualified[0].multiplier)
        notional = (
            quantity * reference_price * multiplier
            if reference_price is not None and multiplier is not None
            else None
        )

        return {
            "contract": contract_info,
            "margin": margin_info,
            "pricing": {
                "reference_price": reference_price,
                "multiplier": qualified[0].multiplier,
                "notional": notional,
            },
            "side": side,
            "quantity": quantity,
        }
    finally:
        if ib.isConnected():
            ib.disconnect()
