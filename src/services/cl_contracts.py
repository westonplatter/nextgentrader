"""Helpers for qualifying CL futures contracts via IBKR."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ib_async import Contract, Future, IB


@dataclass(frozen=True)
class QualifiedContract:
    con_id: int
    symbol: str
    sec_type: str
    exchange: str
    currency: str
    local_symbol: str | None
    trading_class: str | None
    contract_month: str | None
    contract_expiry: str | None


def parse_contract_expiry(last_trade_or_month: str) -> dt.date | None:
    value = (last_trade_or_month or "").strip()
    if len(value) >= 8 and value[:8].isdigit():
        try:
            return dt.datetime.strptime(value[:8], "%Y%m%d").date()
        except ValueError:
            return None
    if len(value) >= 6 and value[:6].isdigit():
        try:
            year = int(value[:4])
            month = int(value[4:6])
            if month == 12:
                next_month = dt.date(year + 1, 1, 1)
            else:
                next_month = dt.date(year, month + 1, 1)
            return next_month - dt.timedelta(days=1)
        except ValueError:
            return None
    return None


def format_contract_month(contract: Contract) -> str | None:
    raw_value = (contract.lastTradeDateOrContractMonth or "").strip()
    if len(raw_value) >= 6 and raw_value[:6].isdigit():
        return f"{raw_value[:4]}-{raw_value[4:6]}"

    expiry = parse_contract_expiry(raw_value)
    if expiry is not None:
        return expiry.strftime("%Y-%m")
    return None


def select_front_month_contract(ib: IB) -> Contract:
    contract_details = ib.reqContractDetails(Future("CL", exchange="NYMEX", currency="USD"))
    if not contract_details:
        raise RuntimeError("No CL futures contract details returned from IBKR")

    today = dt.date.today()
    candidates: list[tuple[dt.date, Contract]] = []
    for detail in contract_details:
        contract = detail.contract
        if contract is None:
            continue
        expiry = parse_contract_expiry(contract.lastTradeDateOrContractMonth)
        if contract.secType != "FUT" or expiry is None or expiry < today:
            continue
        candidates.append((expiry, contract))

    if not candidates:
        raise RuntimeError("No non-expired CL futures contracts found")

    candidates.sort(key=lambda item: item[0])
    front_month_contract = candidates[0][1]
    qualified_contracts = ib.qualifyContracts(front_month_contract)
    if len(qualified_contracts) != 1:
        raise RuntimeError(
            f"Expected exactly one qualified front-month contract, got {len(qualified_contracts)}"
        )
    return qualified_contracts[0]


def to_qualified_contract(contract: Contract) -> QualifiedContract:
    raw_expiry = (contract.lastTradeDateOrContractMonth or "").strip()
    return QualifiedContract(
        con_id=contract.conId,
        symbol=contract.symbol or "CL",
        sec_type=contract.secType or "FUT",
        exchange=contract.exchange or "NYMEX",
        currency=contract.currency or "USD",
        local_symbol=contract.localSymbol,
        trading_class=contract.tradingClass,
        contract_month=format_contract_month(contract),
        contract_expiry=raw_expiry or None,
    )
