import hashlib
import logging
from datetime import UTC, datetime
from typing import NoReturn

from psycopg.errors import CheckViolation, ExclusionViolation
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    IdempotencyKeyReused,
    InvalidDateRange,
    InvalidStateTransition,
    ReservationNotFound,
    StartDateInPast,
    VehicleInMaintenance,
    VehicleUnavailable,
)
from app.models import Reservation, ReservationStatus, VehicleStatus
from app.schemas.reservation import DateRange, ReservationCreate
from app.services.vehicle_service import get_vehicle

logger = logging.getLogger(__name__)


def create_reservation(
    db: Session, data: ReservationCreate, idempotency_key: str | None = None
) -> tuple[Reservation, bool]:
    try:
        return _create_or_replay(db, data, idempotency_key)
    except IntegrityError as e:
        if existing := _find_replay(db, data, idempotency_key):
            return existing, False
        _raise_for_integrity_error(db, data, e)


def get_reservation(
    db: Session, reservation_id: int, for_update: bool = False
) -> Reservation:
    query = select(Reservation).where(Reservation.id == reservation_id)
    if for_update:
        query = query.with_for_update()
    reservation = db.scalar(query)
    if reservation is None:
        raise ReservationNotFound(f"Reservation {reservation_id} not found")
    return reservation


def cancel_reservation(db: Session, reservation_id: int) -> Reservation:
    with db.begin():
        reservation = get_reservation(db, reservation_id, for_update=True)
        if reservation.status == ReservationStatus.CANCELLED:
            raise InvalidStateTransition("Reservation is already cancelled")
        reservation.status = ReservationStatus.CANCELLED
    logger.info("reservation_cancelled reservation_id=%s", reservation_id)
    return reservation


def is_available(db: Session, vehicle_id: int, date_range: DateRange) -> bool:
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle.status == VehicleStatus.MAINTENANCE:
        return False
    return not db.scalar(select(_overlapping(vehicle_id, date_range).exists()))


def _create_or_replay(
    db: Session, data: ReservationCreate, idempotency_key: str | None
) -> tuple[Reservation, bool]:
    with db.begin():
        if existing := _find_replay(db, data, idempotency_key):
            return existing, False
        _ensure_reservable(db, data)
        reservation = _add_reservation(db, data, idempotency_key)
    logger.info(
        "reservation_created reservation_id=%s vehicle_id=%s",
        reservation.id,
        reservation.vehicle_id,
    )
    return reservation, True


def _ensure_reservable(db: Session, data: ReservationCreate) -> None:
    if data.start_date < datetime.now(UTC):
        raise StartDateInPast("start_date must not be in the past")
    vehicle = get_vehicle(db, data.vehicle_id, for_update=True)
    if vehicle.status == VehicleStatus.MAINTENANCE:
        raise VehicleInMaintenance(f"Vehicle {vehicle.id} is in maintenance")


def _add_reservation(
    db: Session, data: ReservationCreate, idempotency_key: str | None
) -> Reservation:
    reservation = Reservation(
        **data.model_dump(),
        idempotency_key=idempotency_key,
        request_hash=_request_hash(data),
    )
    db.add(reservation)
    return reservation


def _raise_for_integrity_error(
    db: Session, data: ReservationCreate, error: IntegrityError
) -> NoReturn:
    if isinstance(error.orig, ExclusionViolation):
        raise _unavailable(db, data) from error
    if isinstance(error.orig, CheckViolation):
        raise InvalidDateRange("end_date must be after start_date") from error
    raise error


def _overlapping(vehicle_id: int, date_range: DateRange) -> Select[tuple[Reservation]]:
    return select(Reservation).where(
        Reservation.vehicle_id == vehicle_id,
        Reservation.status == ReservationStatus.CONFIRMED,
        Reservation.start_date < date_range.end_date,
        Reservation.end_date > date_range.start_date,
    )


def _unavailable(db: Session, data: ReservationCreate) -> VehicleUnavailable:
    conflict = db.scalar(
        _overlapping(data.vehicle_id, data).order_by(Reservation.start_date).limit(1)
    )
    return VehicleUnavailable(
        f"Vehicle {data.vehicle_id} is already reserved {_describe_period(conflict)}"
    )


def _describe_period(conflict: Reservation | None) -> str:
    if conflict is None:
        return "for these dates"
    start, end = _format_utc(conflict.start_date), _format_utc(conflict.end_date)
    return f"from {start} to {end}"


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_replay(
    db: Session, data: ReservationCreate, idempotency_key: str | None
) -> Reservation | None:
    if not idempotency_key:
        return None
    existing = _find_by_idempotency_key(db, data, idempotency_key)
    if existing is None:
        return None
    return _replay(existing, data)


def _find_by_idempotency_key(
    db: Session, data: ReservationCreate, idempotency_key: str
) -> Reservation | None:
    return db.scalar(
        select(Reservation).where(
            Reservation.customer_id == data.customer_id,
            Reservation.idempotency_key == idempotency_key,
        )
    )


def _replay(existing: Reservation, data: ReservationCreate) -> Reservation:
    if existing.request_hash != _request_hash(data):
        raise IdempotencyKeyReused(
            "Idempotency key was already used with a different request"
        )
    logger.info("reservation_replayed reservation_id=%s", existing.id)
    return existing


def _request_hash(data: ReservationCreate) -> str:
    return hashlib.sha256(data.model_dump_json().encode()).hexdigest()
