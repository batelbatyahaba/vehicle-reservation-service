import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import Reservation, ReservationStatus

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/vehicle_reservations_test",
)
VEHICLES = "/api/v1/vehicles"
RESERVATIONS = "/api/v1/reservations"


def new_client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def at(day: int, hour: int = 10) -> str:
    return (
        datetime(2030, 1, 1, hour, tzinfo=UTC) + timedelta(days=day - 1)
    ).isoformat()


def reservation(
    vehicle_id: int, start_day: int, end_day: int, customer_id: str = "customer-1"
) -> dict[str, Any]:
    return {
        "vehicle_id": vehicle_id,
        "customer_id": customer_id,
        "start_date": at(start_day),
        "end_date": at(end_day),
    }


def confirmed_reservations(vehicle_id: int) -> list[Reservation]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(Reservation)
                .where(
                    Reservation.vehicle_id == vehicle_id,
                    Reservation.status == ReservationStatus.CONFIRMED,
                )
                .order_by(Reservation.start_date)
            )
        )


def run_concurrently(calls: list[Callable[[], Any]]) -> list[Any]:
    barrier = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)

    def run(index: int, call: Callable[[], Any]) -> None:
        barrier.wait()
        results[index] = call()

    threads = [
        threading.Thread(target=run, args=(index, call))
        for index, call in enumerate(calls)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results
