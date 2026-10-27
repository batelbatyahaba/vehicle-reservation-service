from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from tests.helpers import (
    RESERVATIONS,
    TEST_DATABASE_URL,
    VEHICLES,
    confirmed_reservations,
    reservation,
)

FlushAction = Callable[[Session], None]


class FailNextFlush:
    def __init__(self) -> None:
        self.action: FlushAction | None = None

    def arm(self, action: FlushAction) -> None:
        self.action = action

    def __call__(self, session: Session, flush_context: object) -> None:
        action, self.action = self.action, None
        if action is not None:
            action(session)


@pytest.fixture
def fail_next_flush(engine: Engine) -> Iterator[FailNextFlush]:
    failure = FailNextFlush()
    event.listen(SessionLocal, "after_flush", failure)
    yield failure
    event.remove(SessionLocal, "after_flush", failure)


@pytest.fixture
def kill_connection(engine: Engine) -> FlushAction:
    def kill(session: Session) -> None:
        pid = session.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar()
        with engine.connect() as admin:
            admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})

    return kill


def raise_unexpected_error(session: Session) -> None:
    raise RuntimeError("simulated failure")


def test_connection_lost_while_creating_reservation_leaves_nothing_behind(
    client: TestClient,
    vehicle_id: int,
    fail_next_flush: FailNextFlush,
    kill_connection: FlushAction,
) -> None:
    payload = reservation(vehicle_id, 1, 5)
    fail_next_flush.arm(kill_connection)

    failed = client.post(RESERVATIONS, json=payload, headers={"Idempotency-Key": "k"})

    assert failed.status_code == 503
    assert failed.json()["code"] == "database_unavailable"
    assert confirmed_reservations(vehicle_id) == []

    retry = client.post(RESERVATIONS, json=payload, headers={"Idempotency-Key": "k"})

    assert retry.status_code == 201
    assert len(confirmed_reservations(vehicle_id)) == 1


def test_connection_lost_while_changing_status_keeps_vehicle_unchanged(
    client: TestClient,
    vehicle_id: int,
    fail_next_flush: FailNextFlush,
    kill_connection: FlushAction,
) -> None:
    fail_next_flush.arm(kill_connection)

    response = client.patch(
        f"{VEHICLES}/{vehicle_id}/status", json={"status": "maintenance"}
    )

    assert response.status_code == 503
    assert client.get(f"{VEHICLES}/{vehicle_id}").json()["status"] == "active"


def test_connection_lost_while_cancelling_keeps_reservation_confirmed(
    client: TestClient,
    vehicle_id: int,
    fail_next_flush: FailNextFlush,
    kill_connection: FlushAction,
) -> None:
    created = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5)).json()
    fail_next_flush.arm(kill_connection)

    response = client.post(f"{RESERVATIONS}/{created['id']}/cancel")

    assert response.status_code == 503
    assert client.get(f"{RESERVATIONS}/{created['id']}").json()["status"] == "confirmed"


def test_unexpected_error_mid_transaction_rolls_back_without_leaking_details(
    client: TestClient, vehicle_id: int, fail_next_flush: FailNextFlush
) -> None:
    fail_next_flush.arm(raise_unexpected_error)

    response = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5))

    assert response.status_code == 500
    assert response.json() == {
        "code": "internal_error",
        "message": "Internal server error",
    }
    assert confirmed_reservations(vehicle_id) == []


def test_exhausted_connection_pool_returns_503(
    client: TestClient, engine: Engine, vehicle_id: int
) -> None:
    tiny_engine = create_engine(
        TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.2
    )
    SessionLocal.configure(bind=tiny_engine)
    try:
        with tiny_engine.connect():
            response = client.get(f"{VEHICLES}/{vehicle_id}")
    finally:
        SessionLocal.configure(bind=engine)
        tiny_engine.dispose()

    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"
