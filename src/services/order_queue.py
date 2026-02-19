"""Order queue lifecycle helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.models import Order, OrderEvent

IB_SUBMITTED_STATUSES = {"submitted", "presubmitted", "pendingsubmit"}
IB_CANCELLED_STATUSES = {"cancelled", "apicancelled", "inactive"}
IB_REJECTED_STATUSES = {"rejected"}
TERMINAL_ORDER_STATUSES = {"filled", "cancelled", "rejected", "failed"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_ib_status(ib_status: str | None, filled_qty: float) -> str:
    value = (ib_status or "").strip().lower()
    if value == "filled":
        return "filled"
    if value in IB_REJECTED_STATUSES:
        return "rejected"
    if value in IB_CANCELLED_STATUSES:
        return "cancelled"
    if value in IB_SUBMITTED_STATUSES:
        if filled_qty > 0:
            return "partially_filled"
        return "submitted"
    if value:
        return "submitted"
    return "queued"


def append_order_event(
    session: Session,
    order: Order,
    event_type: str,
    message: str,
) -> None:
    session.add(
        OrderEvent(
            order_id=order.id,
            event_type=event_type,
            message=message,
            status=order.status,
            filled_quantity=order.filled_quantity,
            avg_fill_price=order.avg_fill_price,
            ib_order_id=order.ib_order_id,
            created_at=now_utc(),
        )
    )


def apply_order_progress(
    order: Order,
    ib_status: str | None,
    filled_quantity: float | None,
    avg_fill_price: float | None,
    ib_order_id: int | None = None,
    ib_perm_id: int | None = None,
) -> bool:
    previous = (
        order.status,
        order.filled_quantity,
        order.avg_fill_price,
        order.ib_order_id,
        order.ib_perm_id,
    )

    normalized_filled = max(0.0, float(filled_quantity or 0.0))
    order.status = normalize_ib_status(ib_status, normalized_filled)
    order.filled_quantity = normalized_filled
    order.avg_fill_price = avg_fill_price
    if ib_order_id is not None:
        order.ib_order_id = ib_order_id
    if ib_perm_id is not None:
        order.ib_perm_id = ib_perm_id
    order.updated_at = now_utc()

    if order.status in TERMINAL_ORDER_STATUSES and order.completed_at is None:
        order.completed_at = now_utc()

    current = (
        order.status,
        order.filled_quantity,
        order.avg_fill_price,
        order.ib_order_id,
        order.ib_perm_id,
    )
    return previous != current
