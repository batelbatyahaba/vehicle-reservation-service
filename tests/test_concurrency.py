from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from app.models import Reservation, VehicleStatus
from app.services.vehicle_service import get_vehicle
from tests.helpers import (
    RESERVATIONS,
    VEHICLES,
    confirmed_reservations,
    new_client,
    reservation,
    run_concurrently,
)


def post_reservation(payload: dict, key: str | None = None) -> Response:
    headers = {"Idempotency-Key": key} if key else {}
    return new_client().post(RESERVATIONS, json=payload, headers=headers)


def is_blocked(future: Future) -> bool:
    done, _ = wait([future], timeout=0.5)
    return not done


def uncommitted_reservation(
    db: Session, vehicle_id: int, start_day: int = 1, end_day: int = 5
) -> None:
    db.add(
        Reservation(
            vehicle_id=vehicle_id,
            customer_id="customer-1",
            start_date=datetime(2030, 1, start_day, 10, tzinfo=UTC),
            end_date=datetime(2030, 1, end_day, 10, tzinfo=UTC),
        )
    )
    db.flush()


def test_concurrent_requests_for_same_slot_only_one_succeeds(vehicle_id: int) -> None:
    responses = run_concurrently(
        [
            lambda i=i: post_reservation(reservation(vehicle_id, 1, 5, f"customer-{i}"))
            for i in range(10)
        ]
    )

    assert Counter(r.status_code for r in responses) == {201: 1, 409: 9}
    assert len(confirmed_reservations(vehicle_id)) == 1


def test_concurrent_overlapping_requests_never_double_book(vehicle_id: int) -> None:
    ranges = [(1, 5), (3, 7), (4, 9), (6, 10), (8, 12), (2, 3), (9, 11), (1, 12)]

    responses = run_concurrently(
        [
            lambda start=start, end=end: post_reservation(
                reservation(vehicle_id, start, end, f"customer-{start}-{end}")
            )
            for start, end in ranges
        ]
    )

    assert {r.status_code for r in responses} <= {201, 409}
    booked = confirmed_reservations(vehicle_id)
    assert booked
    for earlier, later in zip(booked, booked[1:], strict=False):
        assert earlier.end_date <= later.start_date


def test_concurrent_adjacent_requests_all_succeed(vehicle_id: int) -> None:
    responses = run_concurrently(
        [
            lambda day=day: post_reservation(
                reservation(vehicle_id, day, day + 1, f"customer-{day}")
            )
            for day in range(1, 11)
        ]
    )

    assert [r.status_code for r in responses] == [201] * 10
    assert len(confirmed_reservations(vehicle_id)) == 10


def test_concurrent_retries_with_same_key_create_one_reservation(
    vehicle_id: int,
) -> None:
    payload = reservation(vehicle_id, 1, 5)

    responses = run_concurrently(
        [lambda: post_reservation(payload, "key-1") for _ in range(10)]
    )

    assert Counter(r.status_code for r in responses) == {201: 1, 200: 9}
    assert len({r.json()["id"] for r in responses}) == 1
    assert len(confirmed_reservations(vehicle_id)) == 1


def test_concurrent_cancellations_only_one_succeeds(
    client: TestClient, vehicle_id: int
) -> None:
    created = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5)).json()

    responses = run_concurrently(
        [
            lambda: new_client().post(f"{RESERVATIONS}/{created['id']}/cancel")
            for _ in range(5)
        ]
    )

    assert Counter(r.status_code for r in responses) == {200: 1, 409: 4}


@pytest.mark.parametrize(
    ("finish", "expected_status"),
    [("commit", 409), ("rollback", 201)],
)
def test_reservation_waits_for_uncommitted_overlapping_reservation(
    db: Session, vehicle_id: int, finish: str, expected_status: int
) -> None:
    transaction = db.begin()
    uncommitted_reservation(db, vehicle_id)

    with ThreadPoolExecutor() as executor:
        future = executor.submit(
            post_reservation, reservation(vehicle_id, 2, 4, "customer-2")
        )
        assert is_blocked(future)
        getattr(transaction, finish)()
        response = future.result(timeout=10)

    assert response.status_code == expected_status
    assert len(confirmed_reservations(vehicle_id)) == 1


def test_maintenance_switch_waits_for_in_flight_reservation(
    db: Session, client: TestClient, vehicle_id: int
) -> None:
    transaction = db.begin()
    get_vehicle(db, vehicle_id, for_update=True)
    uncommitted_reservation(db, vehicle_id)

    with ThreadPoolExecutor() as executor:
        future = executor.submit(
            new_client().patch,
            f"{VEHICLES}/{vehicle_id}/status",
            json={"status": "maintenance"},
        )
        assert is_blocked(future)
        transaction.commit()
        response = future.result(timeout=10)

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state_transition"
    assert client.get(f"{VEHICLES}/{vehicle_id}").json()["status"] == "active"


def test_reservation_waits_for_maintenance_switch(db: Session, vehicle_id: int) -> None:
    transaction = db.begin()
    vehicle = get_vehicle(db, vehicle_id, for_update=True)
    vehicle.status = VehicleStatus.MAINTENANCE
    db.flush()

    with ThreadPoolExecutor() as executor:
        future = executor.submit(post_reservation, reservation(vehicle_id, 1, 5))
        assert is_blocked(future)
        transaction.commit()
        response = future.result(timeout=10)

    assert response.status_code == 409
    assert response.json()["code"] == "vehicle_in_maintenance"
    assert confirmed_reservations(vehicle_id) == []


@pytest.mark.parametrize(
    ("finish", "expected_status", "expected_count"),
    [("commit", 409, 2), ("rollback", 201, 1)],
)
def test_interleaved_overlapping_inserts_do_not_deadlock(
    db: Session,
    vehicle_id: int,
    finish: str,
    expected_status: int,
    expected_count: int,
) -> None:
    transaction = db.begin()
    uncommitted_reservation(db, vehicle_id, 1, 5)

    with ThreadPoolExecutor() as executor:
        future = executor.submit(
            post_reservation, reservation(vehicle_id, 3, 7, "customer-2")
        )
        assert is_blocked(future)
        uncommitted_reservation(db, vehicle_id, 6, 8)
        assert is_blocked(future)
        getattr(transaction, finish)()
        response = future.result(timeout=10)

    assert response.status_code == expected_status
    assert len(confirmed_reservations(vehicle_id)) == expected_count
