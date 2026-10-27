import pytest
from fastapi.testclient import TestClient

from tests.helpers import (
    RESERVATIONS,
    VEHICLES,
    at,
    confirmed_reservations,
    reservation,
)


def test_create_and_get_reservation(client: TestClient, vehicle_id: int) -> None:
    response = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5))

    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "confirmed"
    assert created["start_date"] == "2030-01-01T10:00:00Z"
    assert created["end_date"] == "2030-01-05T10:00:00Z"
    assert "request_hash" not in created
    assert client.get(f"{RESERVATIONS}/{created['id']}").json() == created


@pytest.mark.parametrize(
    ("start_day", "end_day"),
    [(1, 5), (2, 4), (0, 3), (4, 8), (0, 10)],
    ids=["identical", "inside", "overlaps-start", "overlaps-end", "contains"],
)
def test_overlapping_reservation_is_rejected(
    client: TestClient, vehicle_id: int, start_day: int, end_day: int
) -> None:
    client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5))

    response = client.post(
        RESERVATIONS,
        json=reservation(vehicle_id, start_day, end_day, customer_id="customer-2"),
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "vehicle_unavailable",
        "message": f"Vehicle {vehicle_id} is already reserved "
        "from 2030-01-01T10:00:00Z to 2030-01-05T10:00:00Z",
    }
    assert len(confirmed_reservations(vehicle_id)) == 1


@pytest.mark.parametrize(
    ("start_day", "end_day"),
    [(5, 8), (0, 1), (10, 12)],
    ids=["adjacent-after", "adjacent-before", "disjoint"],
)
def test_non_overlapping_reservations_both_succeed(
    client: TestClient, vehicle_id: int, start_day: int, end_day: int
) -> None:
    first = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5))
    second = client.post(
        RESERVATIONS,
        json=reservation(vehicle_id, start_day, end_day, customer_id="customer-2"),
    )

    assert (first.status_code, second.status_code) == (201, 201)
    assert len(confirmed_reservations(vehicle_id)) == 2


def test_same_dates_on_different_vehicles_both_succeed(
    client: TestClient, vehicle_id: int
) -> None:
    other = client.post(VEHICLES, json={"plate_number": "98-765-43", "model": "Golf"})

    first = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5))
    second = client.post(RESERVATIONS, json=reservation(other.json()["id"], 1, 5))

    assert (first.status_code, second.status_code) == (201, 201)


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        (at(5), at(5)),
        (at(5), at(1)),
        ("2030-01-01T10:00:00", at(5)),
        ("not-a-date", at(5)),
    ],
    ids=["end-equals-start", "end-before-start", "no-timezone", "malformed"],
)
def test_invalid_dates_are_rejected(
    client: TestClient, vehicle_id: int, start_date: str, end_date: str
) -> None:
    response = client.post(
        RESERVATIONS,
        json={
            "vehicle_id": vehicle_id,
            "customer_id": "customer-1",
            "start_date": start_date,
            "end_date": end_date,
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert confirmed_reservations(vehicle_id) == []


def test_reservation_starting_in_the_past_is_rejected(
    client: TestClient, vehicle_id: int
) -> None:
    response = client.post(
        RESERVATIONS,
        json={
            "vehicle_id": vehicle_id,
            "customer_id": "customer-1",
            "start_date": "2020-01-01T10:00:00Z",
            "end_date": "2020-01-05T10:00:00Z",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "start_date_in_past"


def test_vehicle_in_maintenance_cannot_be_reserved(
    client: TestClient, vehicle_id: int
) -> None:
    client.patch(f"{VEHICLES}/{vehicle_id}/status", json={"status": "maintenance"})

    response = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5))

    assert response.status_code == 409
    assert response.json()["code"] == "vehicle_in_maintenance"
    assert confirmed_reservations(vehicle_id) == []


def test_unknown_vehicle_cannot_be_reserved(client: TestClient) -> None:
    response = client.post(RESERVATIONS, json=reservation(999, 1, 5))

    assert response.status_code == 404
    assert response.json()["code"] == "vehicle_not_found"


def test_cancel_reservation(client: TestClient, vehicle_id: int) -> None:
    created = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5)).json()

    response = client.post(f"{RESERVATIONS}/{created['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.get(f"{RESERVATIONS}/{created['id']}").json()["status"] == "cancelled"


def test_cancelling_twice_is_an_invalid_transition(
    client: TestClient, vehicle_id: int
) -> None:
    created = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5)).json()
    client.post(f"{RESERVATIONS}/{created['id']}/cancel")

    response = client.post(f"{RESERVATIONS}/{created['id']}/cancel")

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state_transition"


def test_unknown_reservation_returns_404(client: TestClient) -> None:
    assert client.get(f"{RESERVATIONS}/999").status_code == 404
    assert client.post(f"{RESERVATIONS}/999/cancel").status_code == 404


def test_cancelled_reservation_frees_the_slot(
    client: TestClient, vehicle_id: int
) -> None:
    created = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5)).json()
    client.post(f"{RESERVATIONS}/{created['id']}/cancel")

    response = client.post(
        RESERVATIONS, json=reservation(vehicle_id, 2, 4, customer_id="customer-2")
    )

    assert response.status_code == 201


def check_availability(
    client: TestClient, vehicle_id: int, start_day: int, end_day: int
):
    return client.get(
        f"{VEHICLES}/{vehicle_id}/availability",
        params={"start_date": at(start_day), "end_date": at(end_day)},
    )


@pytest.mark.parametrize(
    ("start_day", "end_day", "available"),
    [(2, 3, False), (0, 2, False), (5, 6, True), (0, 1, True)],
    ids=["inside", "overlapping", "adjacent-after", "adjacent-before"],
)
def test_availability_follows_half_open_ranges(
    client: TestClient, vehicle_id: int, start_day: int, end_day: int, available: bool
) -> None:
    client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5))

    response = check_availability(client, vehicle_id, start_day, end_day)

    assert response.status_code == 200
    assert response.json()["available"] is available


def test_cancelled_reservation_does_not_affect_availability(
    client: TestClient, vehicle_id: int
) -> None:
    created = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5)).json()
    client.post(f"{RESERVATIONS}/{created['id']}/cancel")

    assert check_availability(client, vehicle_id, 1, 5).json()["available"] is True


def test_vehicle_in_maintenance_is_not_available(
    client: TestClient, vehicle_id: int
) -> None:
    client.patch(f"{VEHICLES}/{vehicle_id}/status", json={"status": "maintenance"})

    assert check_availability(client, vehicle_id, 1, 5).json()["available"] is False


def test_availability_rejects_invalid_range(
    client: TestClient, vehicle_id: int
) -> None:
    response = check_availability(client, vehicle_id, 5, 1)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
