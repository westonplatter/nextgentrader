"""
Download current positions from IBKR TWS and store in Postgres.

Usage:
  op run --env-file=.env.dev -- uv run python scripts/download_positions.py --env dev
"""

import argparse
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from ib_async import IB
from sqlalchemy import inspect, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db import get_engine
from src.models import Account, Position


def load_env(env_name: str) -> None:
    env_file = f".env.{env_name}"
    if not os.path.exists(env_file):
        raise FileNotFoundError(f"{env_file} not found")
    load_dotenv(env_file)


def check_db_ready(engine):
    """Check that the database and positions table exist."""
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        for required in ("positions", "accounts"):
            if required not in tables:
                print(f"Error: '{required}' table does not exist.")
                print("Run: task migrate")
                raise SystemExit(1)
    except Exception as e:
        if "does not exist" in str(e) or "could not connect" in str(e):
            print(f"Error: Cannot connect to database: {e}")
            print("Run: task migrate")
            raise SystemExit(1)
        raise


def get_or_create_accounts(session: Session, account_strings: set[str]) -> dict[str, int]:
    """Get or create Account rows, return {account_str: account_id} mapping."""
    lookup = {}
    for acct_str in account_strings:
        row = session.execute(
            select(Account).where(Account.account == acct_str)
        ).scalar_one_or_none()
        if row is None:
            row = Account(account=acct_str)
            session.add(row)
            session.flush()
        lookup[acct_str] = row.id
    return lookup


def main():
    parser = argparse.ArgumentParser(description="Download IBKR positions to DB")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    args = parser.parse_args()

    load_env(args.env)

    engine = get_engine()
    check_db_ready(engine)

    host = "127.0.0.1"
    port = int(os.environ.get("BROKER_TWS_PORT", "7497"))

    print(f"Connecting to TWS at {host}:{port} ...")
    ib = IB()
    try:
        ib.connect(host, port, clientId=2)
        print("Connected to TWS.")

        positions = ib.positions()
        print(f"Fetched {len(positions)} position(s) from TWS.")

        if not positions:
            print("No positions to save.")
            return

        now = datetime.now(timezone.utc)

        with Session(engine) as session:
            unique_accounts = {pos.account for pos in positions}
            account_lookup = get_or_create_accounts(session, unique_accounts)

            for pos in positions:
                contract = pos.contract
                account_id = account_lookup[pos.account]
                stmt = insert(Position).values(
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
                    position=pos.position,
                    avg_cost=pos.avgCost,
                    fetched_at=now,
                ).on_conflict_do_update(
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
                        "position": pos.position,
                        "avg_cost": pos.avgCost,
                        "fetched_at": now,
                    },
                )
                session.execute(stmt)

            session.commit()

        print(f"Saved {len(positions)} position(s) to database.")

    except Exception as e:
        print(f"Error: {e}")
        raise SystemExit(1)
    finally:
        if ib.isConnected():
            ib.disconnect()
            print("Disconnected from TWS.")


if __name__ == "__main__":
    main()
