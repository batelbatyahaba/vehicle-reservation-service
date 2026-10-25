import enum

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VehicleStatus(enum.StrEnum):
    ACTIVE = "active"
    MAINTENANCE = "maintenance"


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    plate_number: Mapped[str] = mapped_column(unique=True)
    model: Mapped[str]
    status: Mapped[VehicleStatus] = mapped_column(default=VehicleStatus.ACTIVE)
