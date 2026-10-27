from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.reservation import AvailabilityRead, DateRange
from app.services import reservation_service

router = APIRouter(tags=["availability"])


@router.get("/vehicles/{vehicle_id}/availability", response_model=AvailabilityRead)
def check_availability(
    vehicle_id: int, date_range: Annotated[DateRange, Query()], db: SessionDep
) -> AvailabilityRead:
    return AvailabilityRead(
        vehicle_id=vehicle_id,
        start_date=date_range.start_date,
        end_date=date_range.end_date,
        available=reservation_service.is_available(db, vehicle_id, date_range),
    )
