# Vehicle Reservation Service

A small FastAPI + PostgreSQL service for reserving vehicles for date ranges. The interesting parts are correctness under concurrency, idempotent retries, and transaction safety. See [DECISIONS.md](DECISIONS.md) for the design decisions and known limitations, and [AI_USAGE.md](AI_USAGE.md) for how AI assistance was used and verified.

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

## Tests

The tests run against a real PostgreSQL. The overlap guarantee is an exclusion constraint, and concurrency is enforced by row locks, so a substitute database such as SQLite would not test the real behavior.

```bash
docker compose up -d db
.venv/Scripts/pytest
```

- Each run drops and recreates a separate database, `vehicle_reservations_test`, and applies the Alembic migrations to it. The development database is never touched.
- Tables are truncated after every test. Transaction rollback cannot be used for isolation here, because the code under test commits and the concurrency tests use several connections at once.
- Override the database with `TEST_DATABASE_URL`, for example in CI.

| File | What it covers |
|---|---|
| `test_reservations.py` | Overlap cases rejected, adjacent and disjoint ranges accepted (half-open), invalid and past dates, maintenance, cancellation, availability |
| `test_vehicles.py` | Registration, duplicates, validation, maintenance transitions |
| `test_idempotency.py` | Replay with the same key, equivalent timestamps, different payload, per-customer scope, retry after the start time has passed |
| `test_concurrency.py` | Parallel requests for the same slot, overlapping and adjacent bursts, parallel retries and cancellations, plus deterministic interleavings that hold a lock or an uncommitted row open and assert the competing request waits and then gets the right outcome |
| `test_transactions.py` | A database connection killed mid-transaction (create, status change, cancel) leaves no partial state and the request can be retried; an unexpected error rolls back and leaks no details; an exhausted pool returns `503` |

The deterministic concurrency tests assert that a request is still blocked after 0.5 seconds. On a very slow machine that check could in principle pass for the wrong reason (the request simply has not reached the database yet), but it cannot produce a false failure: the outcome asserted after the lock is released is the actual guarantee.

## Operations

### Logging

The service logs one line per meaningful event, with IDs only. Customer IDs, request bodies and idempotency keys are never logged.

| Event | Level | Fields |
|---|---|---|
| `vehicle_created` | INFO | `vehicle_id` |
| `vehicle_status_changed` | INFO | `vehicle_id`, `status` |
| `reservation_created` | INFO | `reservation_id`, `vehicle_id` |
| `reservation_replayed` | INFO | `reservation_id` (idempotent retry) |
| `reservation_cancelled` | INFO | `reservation_id` |
| `request_rejected` | INFO | `code`, `status`, `path` (every 4xx: conflicts, maintenance, validation, key reuse, not found) |
| `database_unavailable` | ERROR | `path`, with traceback |
| `unhandled_error` | ERROR | `path`, with traceback (the client only sees `internal_error`) |

### Metrics worth watching

These signals would help diagnose reservation failures in production. They are not instrumented yet. Each one maps to the log events above, or to Postgres's own statistics.

| Signal | Source | What it tells you |
|---|---|---|
| Reservation outcomes per minute, by result: created, replayed, `vehicle_unavailable`, `vehicle_in_maintenance`, `validation_error` | `reservation_created`, `reservation_replayed`, `request_rejected` | Health of the main flow. A jump in `vehicle_unavailable` is real demand or a client double-submitting; a jump in `validation_error` usually means a client release broke its payload. |
| Replay rate (replayed ÷ created) | `reservation_replayed` | Clients timing out and retrying. A rise points at slow responses or network problems before customers complain. |
| `idempotency_key_reused` count | `request_rejected` | Clients generating keys wrongly. It should be near zero. |
| 5xx rate, split into `database_unavailable` and `unhandled_error` | ERROR logs | `database_unavailable` means a database outage or pool exhaustion; `unhandled_error` means a bug. |
| p95 / p99 latency of `POST /reservations` | Access logs or middleware | Lock waits show up here first: requests for a busy vehicle queue behind each other. |
| Connection pool: in use, waiting, timeouts | SQLAlchemy pool events | Pool exhaustion before it turns into `503`. |
| Lock waits and deadlocks | `pg_stat_activity` (`wait_event_type = 'Lock'`), `pg_stat_database.deadlocks`, `log_lock_waits = on` | Contention on vehicle rows. Deadlocks should stay at zero (see DECISIONS.md, decision 1). |

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
