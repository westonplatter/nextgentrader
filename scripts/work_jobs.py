"""
Generic jobs worker.

Polls queued jobs and dispatches handlers by `job_type`.
Initial handler: `positions.sync`

Usage:
  uv run python scripts/work_jobs.py --env dev
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable

from dotenv import load_dotenv
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.db import get_engine
from src.models import Job
from src.services.jobs import (
    JOB_TYPE_POSITIONS_SYNC,
    claim_next_job,
    complete_job,
    fail_or_retry_job,
)
from src.services.position_sync import check_positions_tables_ready, sync_positions_once
from src.services.worker_heartbeat import WORKER_TYPE_JOBS, upsert_worker_heartbeat
from src.utils.env_vars import get_int_env


def load_env(env_name: str) -> None:
    env_file = f".env.{env_name}"
    if not os.path.exists(env_file):
        raise FileNotFoundError(f"{env_file} not found")
    load_dotenv(env_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process queued jobs.")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--once", action="store_true", help="Process one queue pass and exit.")
    return parser.parse_args()


def check_db_ready() -> None:
    engine = get_engine()
    check_positions_tables_ready(engine)
    tables = inspect(engine).get_table_names()
    for required in ("jobs", "worker_heartbeats"):
        if required not in tables:
            raise SystemExit(f"Missing '{required}' table. Run: task migrate")


def handle_positions_sync(job: Job, engine: Engine) -> dict:
    payload = job.payload or {}
    host = str(payload.get("host") or "127.0.0.1")
    port_raw = payload.get("port")
    client_id_raw = payload.get("client_id")
    connect_timeout_raw = payload.get("connect_timeout_seconds")

    if isinstance(port_raw, int):
        port = port_raw
    else:
        port = get_int_env("BROKER_TWS_PORT")
    if port is None:
        raise RuntimeError("BROKER_TWS_PORT is not set and no port was provided in job payload.")

    if isinstance(client_id_raw, int):
        client_id = client_id_raw
    else:
        client_id = 31

    if isinstance(connect_timeout_raw, (int, float)):
        connect_timeout_seconds = float(connect_timeout_raw)
    else:
        connect_timeout_seconds = 20.0

    fetched_positions_count = sync_positions_once(
        engine=engine,
        host=host,
        port=port,
        client_id=client_id,
        connect_timeout_seconds=connect_timeout_seconds,
    )
    return {
        "fetched_positions_count": fetched_positions_count,
        "host": host,
        "port": port,
        "client_id": client_id,
        "connect_timeout_seconds": connect_timeout_seconds,
    }


def get_handler(job_type: str) -> Callable[[Job, Engine], dict] | None:
    handlers: dict[str, Callable[[Job, Engine], dict]] = {
        JOB_TYPE_POSITIONS_SYNC: handle_positions_sync,
    }
    return handlers.get(job_type)


def main() -> int:
    args = parse_args()
    load_env(args.env)
    check_db_ready()

    engine = get_engine()
    upsert_worker_heartbeat(
        engine,
        WORKER_TYPE_JOBS,
        status="starting",
        details="worker boot",
    )

    try:
        while True:
            processed = 0
            while True:
                with Session(engine) as session:
                    claimed_job = claim_next_job(session)
                    if claimed_job is None:
                        break
                    job_id = claimed_job.id
                    session.commit()

                processed += 1
                with Session(engine) as session:
                    job = session.get(Job, job_id)
                    if job is None:
                        session.rollback()
                        continue

                    handler = get_handler(job.job_type)
                    if handler is None:
                        fail_or_retry_job(
                            session,
                            job,
                            f"Unsupported job_type '{job.job_type}'",
                            retry_delay_seconds=0,
                        )
                        session.commit()
                        continue

                    try:
                        result = handler(job, engine)
                        complete_job(session, job, result)
                    except Exception as exc:
                        fail_or_retry_job(session, job, str(exc))
                    session.commit()

            upsert_worker_heartbeat(
                engine,
                WORKER_TYPE_JOBS,
                status="running",
                details=f"processed={processed}",
            )

            if args.once:
                print(f"Processed {processed} job(s).")
                return 0

            if processed == 0:
                time.sleep(args.poll_seconds)
    finally:
        try:
            upsert_worker_heartbeat(
                engine,
                WORKER_TYPE_JOBS,
                status="stopped",
                details="worker exiting",
            )
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
