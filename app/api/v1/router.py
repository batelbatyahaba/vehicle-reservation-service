from fastapi import APIRouter

from app.api.v1.endpoints import availability, reservations, vehicles
from app.schemas.common import ErrorResponse

api_router = APIRouter(prefix="/api/v1", responses={"4XX": {"model": ErrorResponse}})
api_router.include_router(vehicles.router)
api_router.include_router(reservations.router)
api_router.include_router(availability.router)
