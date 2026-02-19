"""Sync positions from IBKR into Postgres."""

from __future__ import annotations

from datetime import datetime, timezone

from ib_async import IB
from sqlalchemy import Engine, inspect, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models import Account, Position


def check_positions_tables_ready(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    for required in ("positions", "accounts"):
        if required not in tables:
            raise RuntimeError(f"'{required}' table does not exist. Run: task migrate")


def get_or_create_accounts(session: Session, account_strings: set[str]) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for account_string in account_strings:
        row = session.execute(
            select(Account).where(Account.account == account_string)
        ).scalar_one_or_none()
        if row is None:
            row = Account(account=account_string)
            session.add(row)
            session.flush()
        lookup[account_string] = row.id
    return lookup


def sync_positions_once(
    engine: Engine,
    host: str,
    port: int,
    client_id: int,
) -> int:
    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id)
        positions = ib.positions()
        if not positions:
            return 0

        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            unique_accounts = {position.account for position in positions}
            account_lookup = get_or_create_accounts(session, unique_accounts)

            for position in positions:
                contract = position.contract
                account_id = account_lookup[position.account]
                stmt = (
                    insert(Position)
                    .values(
                        account_id=account_id,
                        con_id=contract.conId,
                        symbol=contract.symbol,
                        sec_type=contract.secType,
                        exchange=contract.exchange,
                        primary_exchange=contract.primaryExchange,
                        currency=contract.currency,
                        local_symbol=contract.localSymbol,
                        trading_class=contract.tradingClass,
                        last_trade_date=contract.lastTradeDateOrContractMonth,
                        strike=contract.strike,
                        right=contract.right,
                        multiplier=contract.multiplier,
                        position=position.position,
                        avg_cost=position.avgCost,
                        fetched_at=now,
                    )
                    .on_conflict_do_update(
                        constraint="uq_account_id_con_id",
                        set_={
                            "symbol": contract.symbol,
                            "sec_type": contract.secType,
                            "exchange": contract.exchange,
                            "primary_exchange": contract.primaryExchange,
                            "currency": contract.currency,
                            "local_symbol": contract.localSymbol,
                            "trading_class": contract.tradingClass,
                            "last_trade_date": contract.lastTradeDateOrContractMonth,
                            "strike": contract.strike,
                            "right": contract.right,
                            "multiplier": contract.multiplier,
                            "position": position.position,
                            "avg_cost": position.avgCost,
                            "fetched_at": now,
                        },
                    )
                )
                session.execute(stmt)

            session.commit()
        return len(positions)
    finally:
        if ib.isConnected():
            ib.disconnect()
