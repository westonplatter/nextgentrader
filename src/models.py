"""SQLAlchemy models for ngtrader."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("account", "con_id", name="uq_account_con_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String, nullable=False)
    con_id: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String)
    sec_type: Mapped[str | None] = mapped_column(String)
    exchange: Mapped[str | None] = mapped_column(String)
    primary_exchange: Mapped[str | None] = mapped_column(String)
    currency: Mapped[str | None] = mapped_column(String)
    local_symbol: Mapped[str | None] = mapped_column(String)
    trading_class: Mapped[str | None] = mapped_column(String)
    last_trade_date: Mapped[str | None] = mapped_column(String)
    strike: Mapped[float | None] = mapped_column(Float)
    right: Mapped[str | None] = mapped_column(String)
    multiplier: Mapped[str | None] = mapped_column(String)
    position: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
