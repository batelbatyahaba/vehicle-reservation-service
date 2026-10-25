from pydantic import Field

from app.models import VehicleStatus
from app.schemas.common import InputSchema, ReadSchema


class VehicleCreate(InputSchema):
    plate_number: str = Field(min_length=1, max_length=20)
    model: str = Field(min_length=1, max_length=100)


class VehicleStatusUpdate(InputSchema):
    status: VehicleStatus


class VehicleRead(ReadSchema):
    id: int
    plate_number: str
    model: str
    status: VehicleStatus
