from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from app.api.deps import SessionDep
from app.models import Reservation
from app.schemas.reservation import ReservationCreate, ReservationRead
from app.services import reservation_service

router = APIRouter(prefix="/reservations", tags=["reservations"])


@router.post("", response_model=ReservationRead, status_code=status.HTTP_201_CREATED)
def create_reservation(
    data: ReservationCreate,
    response: Response,
    db: SessionDep,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=255)] = None,
) -> Reservation:
    reservation, created = reservation_service.create_reservation(
        db, data, idempotency_key
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return reservation


@router.get("/{reservation_id}", response_model=ReservationRead)
def get_reservation(reservation_id: int, db: SessionDep) -> Reservation:
    return reservation_service.get_reservation(db, reservation_id)


@router.post("/{reservation_id}/cancel", response_model=ReservationRead)
def cancel_reservation(reservation_id: int, db: SessionDep) -> Reservation:
    return reservation_service.cancel_reservation(db, reservation_id)
