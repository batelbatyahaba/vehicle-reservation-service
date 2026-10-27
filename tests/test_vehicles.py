import pytest
from fastapi.testclient import TestClient

from tests.helpers import RESERVATIONS, VEHICLES, reservation


def test_create_and_get_vehicle(client: TestClient) -> None:
    response = client.post(
        VEHICLES, json={"plate_number": "12-345-67", "model": "Corolla"}
    )

    assert response.status_code == 201
    vehicle = response.json()
    assert vehicle == {
        "id": vehicle["id"],
        "plate_number": "12-345-67",
        "model": "Corolla",
        "status": "active",
    }
    assert client.get(f"{VEHICLES}/{vehicle['id']}").json() == vehicle


def test_duplicate_plate_number_is_rejected(
    client: TestClient, vehicle_id: int
) -> None:
    response = client.post(
        VEHICLES, json={"plate_number": "12-345-67", "model": "Golf"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_plate_number"


def test_unknown_vehicle_returns_404(client: TestClient) -> None:
    response = client.get(f"{VEHICLES}/999")

    assert response.status_code == 404
    assert response.json()["code"] == "vehicle_not_found"


@pytest.mark.parametrize(
    "payload",
    [
        {"plate_number": "   ", "model": "Corolla"},
        {"model": "Corolla"},
        {"plate_number": "12-345-67", "model": "Corolla", "status": "maintenance"},
    ],
    ids=["blank-plate", "missing-plate", "unknown-field"],
)
def test_invalid_vehicle_payload_is_rejected(client: TestClient, payload: dict) -> None:
    response = client.post(VEHICLES, json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_vehicle_without_reservations_can_enter_and_leave_maintenance(
    client: TestClient, vehicle_id: int
) -> None:
    to_maintenance = client.patch(
        f"{VEHICLES}/{vehicle_id}/status", json={"status": "maintenance"}
    )
    to_active = client.patch(
        f"{VEHICLES}/{vehicle_id}/status", json={"status": "active"}
    )

    assert to_maintenance.status_code == 200
    assert to_maintenance.json()["status"] == "maintenance"
    assert to_active.json()["status"] == "active"


def test_vehicle_with_upcoming_reservation_cannot_enter_maintenance(
    client: TestClient, vehicle_id: int
) -> None:
    client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5))

    response = client.patch(
        f"{VEHICLES}/{vehicle_id}/status", json={"status": "maintenance"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state_transition"
    assert client.get(f"{VEHICLES}/{vehicle_id}").json()["status"] == "active"


def test_cancelled_reservation_does_not_block_maintenance(
    client: TestClient, vehicle_id: int
) -> None:
    created = client.post(RESERVATIONS, json=reservation(vehicle_id, 1, 5)).json()
    client.post(f"{RESERVATIONS}/{created['id']}/cancel")

    response = client.patch(
        f"{VEHICLES}/{vehicle_id}/status", json={"status": "maintenance"}
    )

    assert response.status_code == 200
