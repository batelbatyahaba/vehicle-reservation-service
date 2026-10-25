from datetime import UTC, datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    Field,
    ValidationInfo,
    field_validator,
)

from app.models import ReservationStatus
from app.schemas.common import InputSchema, ReadSchema

UtcDatetime = Annotated[
    AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))
]


class DateRange(InputSchema):
    start_date: UtcDatetime
    end_date: UtcDatetime

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
