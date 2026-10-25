import hashlib
import logging
from datetime import UTC, datetime

from psycopg.errors import ExclusionViolation
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    IdempotencyKeyReused,
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
    request_hash = hashlib.sha256(data.model_dump_json().encode()).hexdigest()
    try:
        with db.begin():
            if idempotency_key and (
                existing := _find_by_idempotency_key(db, data, idempotency_key)
            ):
                return _replay(existing, request_hash), False
            if data.start_date < datetime.now(UTC):
                raise StartDateInPast("start_date must not be in the past")
            vehicle = get_vehicle(db, data.vehicle_id, lock="share")
            if vehicle.status == VehicleStatus.MAINTENANCE:
                raise VehicleInMaintenance(f"Vehicle {vehicle.id} is in maintenance")
            reservation = Reservation(
                **data.model_dump(),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            db.add(reservation)
    except IntegrityError as e:
        if idempotency_key and (
            existing := _find_by_idempotency_key(db, data, idempotency_key)
        ):
            return _replay(existing, request_hash), False
        if isinstance(e.orig, ExclusionViolation):
            logger.info("reservation_conflict vehicle_id=%s", data.vehicle_id)
            raise VehicleUnavailable(
                f"Vehicle {data.vehicle_id} is already reserved for these dates"
            ) from e
        raise
    logger.info(
        "reservation_created reservation_id=%s vehicle_id=%s",
        reservation.id,
        reservation.vehicle_id,
    )
    return reservation, True


def get_reservation(db: Session, reservation_id: int) -> Reservation:
    reservation = db.get(Reservation, reservation_id)
    if reservation is None:
        raise ReservationNotFound(f"Reservation {reservation_id} not found")
    return reservation


def cancel_reservation(db: Session, reservation_id: int) -> Reservation:
    with db.begin():
        reservation = db.scalar(
            select(Reservation)
            .where(Reservation.id == reservation_id)
            .with_for_update()
        )
        if reservation is None:
            raise ReservationNotFound(f"Reservation {reservation_id} not found")
        if reservation.status == ReservationStatus.CANCELLED:
            raise InvalidStateTransition("Reservation is already cancelled")
        reservation.status = ReservationStatus.CANCELLED
    logger.info("reservation_cancelled reservation_id=%s", reservation_id)
    return reservation


def is_available(db: Session, vehicle_id: int, date_range: DateRange) -> bool:
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle.status == VehicleStatus.MAINTENANCE:
        return False
    overlapping = db.scalar(
        select(
            exists().where(
                Reservation.vehicle_id == vehicle_id,
                Reservation.status == ReservationStatus.CONFIRMED,
                Reservation.start_date < date_range.end_date,
                Reservation.end_date > date_range.start_date,
            )
        )
    )
    return not overlapping


def _find_by_idempotency_key(
    db: Session, data: ReservationCreate, idempotency_key: str
) -> Reservation | None:
    return db.scalar(
        select(Reservation).where(
            Reservation.customer_id == data.customer_id,
            Reservation.idempotency_key == idempotency_key,
        )
    )


def _replay(existing: Reservation, request_hash: str) -> Reservation:
    if existing.request_hash != request_hash:
        raise IdempotencyKeyReused(
            "Idempotency key was already used with a different request"
        )
    logger.info("reservation_replayed reservation_id=%s", existing.id)
    return existing
