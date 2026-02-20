"""Helpers for qualifying CL futures contracts via IBKR."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ib_async import Contract, Future, IB

DEFAULT_CL_MIN_DAYS_TO_EXPIRY = 7


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


def days_until_contract_expiry(last_trade_or_month: str, today: dt.date | None = None) -> int | None:
    expiry = parse_contract_expiry(last_trade_or_month)
    if expiry is None:
        return None
    comparison_day = today or dt.date.today()
    return (expiry - comparison_day).days


def contract_days_to_expiry(contract: Contract, today: dt.date | None = None) -> int | None:
    return days_until_contract_expiry(contract.lastTradeDateOrContractMonth, today=today)


def format_contract_month(contract: Contract) -> str | None:
    raw_value = (contract.lastTradeDateOrContractMonth or "").strip()
    if len(raw_value) >= 6 and raw_value[:6].isdigit():
        return f"{raw_value[:4]}-{raw_value[4:6]}"

    expiry = parse_contract_expiry(raw_value)
    if expiry is not None:
        return expiry.strftime("%Y-%m")
    return None


def select_front_month_contract(
    ib: IB, min_days_to_expiry: int = DEFAULT_CL_MIN_DAYS_TO_EXPIRY
) -> Contract:
    if min_days_to_expiry < 0:
        raise ValueError("min_days_to_expiry must be >= 0")

    contract_details = ib.reqContractDetails(Future("CL", exchange="NYMEX", currency="USD"))
    if not contract_details:
        raise RuntimeError("No CL futures contract details returned from IBKR")

    candidates: list[tuple[dt.date, Contract]] = []
    non_expired: list[tuple[dt.date, Contract]] = []
    for detail in contract_details:
        contract = detail.contract
        if contract is None:
            continue
        expiry = parse_contract_expiry(contract.lastTradeDateOrContractMonth)
        days_to_expiry = contract_days_to_expiry(contract)
        if contract.secType != "FUT" or expiry is None or days_to_expiry is None or days_to_expiry < 0:
            continue
        non_expired.append((expiry, contract))
        if days_to_expiry < min_days_to_expiry:
            continue
        candidates.append((expiry, contract))

    if not candidates:
        if non_expired:
            nearest_expiry, nearest_contract = min(non_expired, key=lambda item: item[0])
            nearest_days = contract_days_to_expiry(nearest_contract)
            raise RuntimeError(
                "No CL futures contracts found outside the near-expiry safety window "
                f"(min_days_to_expiry={min_days_to_expiry}). "
                f"Nearest non-expired contract: {nearest_contract.localSymbol or nearest_contract.symbol} "
                f"expiring {nearest_expiry.isoformat()} ({nearest_days} days)."
            )
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
