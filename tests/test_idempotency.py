from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.services import reservation_service
from tests.helpers import RESERVATIONS, confirmed_reservations, reservation


def create(client: TestClient, payload: dict, key: str | None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(RESERVATIONS, json=payload, headers=headers)


def test_retry_with_same_key_returns_original_reservation(
    client: TestClient, vehicle_id: int
) -> None:
    payload = reservation(vehicle_id, 1, 5)

    first = create(client, payload, "key-1")
    retry = create(client, payload, "key-1")

    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json() == first.json()
    assert len(confirmed_reservations(vehicle_id)) == 1


def test_retry_with_equivalent_timestamps_is_a_replay(
    client: TestClient, vehicle_id: int
) -> None:
    payload = reservation(vehicle_id, 1, 5)
    same_instant = {**payload, "start_date": "2030-01-01T12:00:00+02:00"}

    first = create(client, payload, "key-1")
    retry = create(client, same_instant, "key-1")

    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]


def test_same_key_with_different_payload_is_rejected(
    client: TestClient, vehicle_id: int
) -> None:
    create(client, reservation(vehicle_id, 1, 5), "key-1")

    response = create(client, reservation(vehicle_id, 10, 12), "key-1")

    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"
    assert len(confirmed_reservations(vehicle_id)) == 1


def test_keys_are_scoped_per_customer(client: TestClient, vehicle_id: int) -> None:
    first = create(client, reservation(vehicle_id, 1, 5, "customer-1"), "key-1")
    second = create(client, reservation(vehicle_id, 10, 12, "customer-2"), "key-1")

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["id"] != second.json()["id"]


def test_retry_after_start_time_passed_returns_original(
    client: TestClient, vehicle_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = reservation(vehicle_id, 1, 5)
    first = create(client, payload, "key-1")

    class AfterStart(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2030, 1, 2, tzinfo=UTC)

    monkeypatch.setattr(reservation_service, "datetime", AfterStart)
    retry = create(client, payload, "key-1")
    new_request = create(client, payload, "key-2")

    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    assert new_request.status_code == 422
    assert new_request.json()["code"] == "start_date_in_past"


def test_retry_without_key_is_rejected_as_conflict(
    client: TestClient, vehicle_id: int
) -> None:
    payload = reservation(vehicle_id, 1, 5)

    first = create(client, payload, None)
    retry = create(client, payload, None)

    assert (first.status_code, retry.status_code) == (201, 409)
    assert len(confirmed_reservations(vehicle_id)) == 1


def test_empty_key_is_rejected(client: TestClient, vehicle_id: int) -> None:
    response = client.post(
        RESERVATIONS,
        json=reservation(vehicle_id, 1, 5),
        headers={"Idempotency-Key": ""},
    )

    assert response.status_code == 422
    assert confirmed_reservations(vehicle_id) == []
