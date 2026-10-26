# Vehicle Reservation Service

A small FastAPI + PostgreSQL service for reserving vehicles for date ranges. The interesting parts are correctness under concurrency, idempotent retries, and transaction safety. See [DECISIONS.md](DECISIONS.md).

## Requirements

- Docker (with Docker Compose)
- Python 3.12, for running and debugging outside Docker

## Run everything in Docker

```bash
docker compose up --build
```

- API: http://localhost:8000
- Interactive docs (Swagger UI): http://localhost:8000/docs

The `api` container applies migrations (`alembic upgrade head`) before starting the server.

## Run locally (for development and debugging)

Run only the database in Docker, and the API on your machine:

```bash
docker compose up -d db
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt     # Linux/macOS: .venv/bin/pip
cp .env.example .env
.venv/Scripts/alembic upgrade head
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```

If the `api` container is also running, stop it first so port 8000 is free: `docker compose stop api`.

### Configuration

Read from environment variables or `.env`:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | (required) | SQLAlchemy URL, e.g. `postgresql+psycopg://postgres:postgres@localhost:5432/vehicle_reservations` |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `DB_ECHO` | `false` | Log every SQL statement (useful when debugging) |

### Debugging in PyCharm

The repository includes shared run configurations in `.run/`:

- **API**: runs `uvicorn app.main:app --port 8000` with the project interpreter. Start it with **Debug** (Shift+F9) and breakpoints work in endpoints and services.
- **Migrate DB**: runs `alembic upgrade head`.

The debug configuration deliberately runs without `--reload`: reload starts the app in a child process, which makes breakpoints less reliable.

## API

| Method | Path | Success | Errors |
|---|---|---|---|
| `POST` | `/api/v1/vehicles` | 201 | 409 `duplicate_plate_number` |
| `GET` | `/api/v1/vehicles/{id}` | 200 | 404 |
| `PATCH` | `/api/v1/vehicles/{id}/status` | 200 | 409 `invalid_state_transition` |
| `GET` | `/api/v1/vehicles/{id}/availability?start_date=&end_date=` | 200 | 404, 422 |
| `POST` | `/api/v1/reservations` (optional `Idempotency-Key` header) | 201 created, 200 replayed | 409 `vehicle_unavailable` / `vehicle_in_maintenance`, 422 |
| `GET` | `/api/v1/reservations/{id}` | 200 | 404 |
| `POST` | `/api/v1/reservations/{id}/cancel` | 200 | 409 `invalid_state_transition` |

All errors share one shape:

```json
{"code": "vehicle_unavailable", "message": "Vehicle 1 is already reserved for these dates"}
```

Timestamps must include a timezone (ISO-8601, e.g. `2030-01-01T10:00:00Z`). Ranges are half-open: `[start_date, end_date)`.

### Example

```bash
curl -X POST http://localhost:8000/api/v1/vehicles \
  -H "Content-Type: application/json" \
  -d '{"plate_number": "12-345-67", "model": "Toyota Corolla"}'

curl -X POST http://localhost:8000/api/v1/reservations \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 3f2b7c1e-reservation-1" \
  -d '{"vehicle_id": 1, "customer_id": "customer-42", "start_date": "2030-01-01T10:00:00Z", "end_date": "2030-01-05T10:00:00Z"}'
```

Sending the second request again with the same `Idempotency-Key` returns the same reservation with `200` instead of creating a duplicate.
