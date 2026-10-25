from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field, ValidationInfo, field_validator

from app.models import ReservationStatus
from app.schemas.common import InputSchema, ReadSchema


class DateRange(InputSchema):
    start_date: AwareDatetime
    end_date: AwareDatetime

    @field_validator("end_date")
    @classmethod
    def check_end_after_start(
        cls, end_date: datetime, info: ValidationInfo
    ) -> datetime:
        start_date = info.data.get("start_date")
        if start_date is not None and end_date <= start_date:
            raise ValueError("must be after start_date")
        return end_date


class ReservationCreate(DateRange):
    vehicle_id: int = Field(gt=0)
    customer_id: str = Field(min_length=1, max_length=64)


class ReservationRead(ReadSchema):
    id: int
    vehicle_id: int
    customer_id: str
    start_date: datetime
    end_date: datetime
    status: ReservationStatus
    created_at: datetime


class AvailabilityRead(BaseModel):
    vehicle_id: int
    start_date: datetime
    end_date: datetime
    available: bool
