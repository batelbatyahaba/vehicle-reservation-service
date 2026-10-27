import logging
from datetime import UTC, datetime
from typing import NoReturn

from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    DuplicatePlateNumber,
    InvalidStateTransition,
    VehicleNotFound,
)
from app.models import Reservation, ReservationStatus, Vehicle, VehicleStatus
from app.schemas.vehicle import VehicleCreate

logger = logging.getLogger(__name__)


def create_vehicle(db: Session, data: VehicleCreate) -> Vehicle:
    vehicle = Vehicle(**data.model_dump())
    try:
        with db.begin():
            db.add(vehicle)
    except IntegrityError as e:
        _raise_for_integrity_error(e)
    logger.info("vehicle_created vehicle_id=%s", vehicle.id)
    return vehicle


def get_vehicle(db: Session, vehicle_id: int, for_update: bool = False) -> Vehicle:
    query = select(Vehicle).where(Vehicle.id == vehicle_id)
    if for_update:
        query = query.with_for_update()
    vehicle = db.scalar(query)
    if vehicle is None:
        raise VehicleNotFound(f"Vehicle {vehicle_id} not found")
    return vehicle


def update_vehicle_status(
    db: Session, vehicle_id: int, status: VehicleStatus
) -> Vehicle:
    with db.begin():
        vehicle = get_vehicle(db, vehicle_id, for_update=True)
        _ensure_status_change_allowed(db, vehicle_id, status)
        vehicle.status = status
    logger.info("vehicle_status_changed vehicle_id=%s status=%s", vehicle_id, status)
    return vehicle


def _raise_for_integrity_error(error: IntegrityError) -> NoReturn:
    if isinstance(error.orig, UniqueViolation):
        raise DuplicatePlateNumber("A vehicle with this plate number exists") from error
    raise error


def _ensure_status_change_allowed(
    db: Session, vehicle_id: int, status: VehicleStatus
) -> None:
    if status == VehicleStatus.MAINTENANCE and _has_active_reservations(db, vehicle_id):
        raise InvalidStateTransition("Vehicle has active or upcoming reservations")


def _has_active_reservations(db: Session, vehicle_id: int) -> bool:
    upcoming = select(Reservation).where(
        Reservation.vehicle_id == vehicle_id,
        Reservation.status == ReservationStatus.CONFIRMED,
        Reservation.end_date > datetime.now(UTC),
    )
    return db.scalar(select(upcoming.exists()))
