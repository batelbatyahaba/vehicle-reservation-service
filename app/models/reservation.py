import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReservationStatus(enum.StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("end_date > start_date", name="ck_reservations_valid_range"),
        ExcludeConstraint(
            ("vehicle_id", "="),
            (text("tstzrange(start_date, end_date, '[)')"), "&&"),
            where=text("status = 'confirmed'"),
            using="gist",
            name="ex_reservations_no_overlap",
        ),
        UniqueConstraint(
            "customer_id",
            "idempotency_key",
            name="uq_reservations_customer_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    customer_id: Mapped[str]
    start_date: Mapped[datetime]
    end_date: Mapped[datetime]
    status: Mapped[ReservationStatus] = mapped_column(
        default=ReservationStatus.CONFIRMED
    )
    idempotency_key: Mapped[str | None]
    request_hash: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
