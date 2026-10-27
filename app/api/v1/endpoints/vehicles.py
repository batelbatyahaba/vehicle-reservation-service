from fastapi import APIRouter, status

from app.api.deps import SessionDep
from app.models import Vehicle
from app.schemas.vehicle import VehicleCreate, VehicleRead, VehicleStatusUpdate
from app.services import vehicle_service

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.post("", response_model=VehicleRead, status_code=status.HTTP_201_CREATED)
def create_vehicle(data: VehicleCreate, db: SessionDep) -> Vehicle:
    return vehicle_service.create_vehicle(db, data)


@router.get("/{vehicle_id}", response_model=VehicleRead)
def get_vehicle(vehicle_id: int, db: SessionDep) -> Vehicle:
    return vehicle_service.get_vehicle(db, vehicle_id)


@router.patch("/{vehicle_id}/status", response_model=VehicleRead)
def update_vehicle_status(
    vehicle_id: int, data: VehicleStatusUpdate, db: SessionDep
) -> Vehicle:
    return vehicle_service.update_vehicle_status(db, vehicle_id, data.status)
