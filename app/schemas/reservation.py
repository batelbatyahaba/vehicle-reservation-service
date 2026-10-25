from datetime import datetime
from typing import Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.models import ReservationStatus
from app.schemas.common import InputSchema, ReadSchema


class DateRange(InputSchema):
    start_date: AwareDatetime
    end_date: AwareDatetime

    @model_validator(mode="after")
    def check_end_after_start(self) -> Self:
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


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
