# Decisions

## 1. Overlap prevention is enforced by a PostgreSQL exclusion constraint

**Decision.** The invariant "a vehicle cannot have two active reservations with overlapping date ranges" is enforced by the database, not by application code:

```sql
EXCLUDE USING gist (
  vehicle_id WITH =,
  tstzrange(start_date, end_date, '[)') WITH &&
) WHERE (status = 'confirmed')
```

Two overlapping reservations can never both commit. The losing insert fails with SQLSTATE `23P01` (`exclusion_violation`), which the service maps to `409 vehicle_unavailable`. The error message includes the conflicting range (never the other customer's ID), so the client can see why the request failed. The partial `WHERE` clause means cancelled reservations stop blocking availability as soon as the cancellation commits.

**Reservation creation is also serialized per vehicle** (`SELECT ... FOR UPDATE` on the vehicle row). This is not what guarantees correctness; the constraint is. It prevents deadlocks:

- An exclusion constraint inserts its index entry first and checks for conflicts afterwards. Two concurrent transactions with overlapping ranges can each see the other's uncommitted row and wait for each other. Postgres then aborts one with `deadlock detected`.
- Without the lock, a burst of overlapping requests intermittently returned `503` instead of `409`. No double booking ever occurred, but the response was wrong. A retry on deadlock made it rarer but not impossible: under heavy overlap a request could lose three times in a row.
- With the lock, reservations for one vehicle run one at a time. Each sees only committed rows and never waits on another in-flight insert, so the cycle cannot form.
- The lock also covers writers that bypass the service. Every insert into `reservations` takes `FOR KEY SHARE` on the vehicle row through the foreign key, and `FOR UPDATE` conflicts with it.
- Cost: reservations of the same vehicle are serialized, even non-overlapping ones. Reservations of different vehicles still run in parallel. For a rental fleet, where each vehicle sees a few reservations a day, this is negligible.

`test_interleaved_overlapping_inserts_do_not_deadlock` reproduces the deadlocking interleaving deterministically. It fails if the lock is removed.

**Alternatives considered.**

| Option | Why not chosen |
|---|---|
| Check-then-insert in application code | Not safe under concurrency; explicitly ruled out by the requirements. |
| `SELECT ... FOR UPDATE` on the vehicle row as the only mechanism | As the sole guarantee, it depends on every code path remembering to take the lock. It is used, but only to prevent deadlocks; the constraint still holds if a path forgets it. |
| `SERIALIZABLE` isolation with retry | Correct, but requires retry loops and produces serialization failures that are harder to map to meaningful errors. |
| Advisory locks | Same "every path must remember" weakness as row locks, with less visibility. |

**Trade-offs.** Ties the solution to PostgreSQL and the `btree_gist` extension (created in the first migration). Tests must run against real PostgreSQL rather than SQLite; `docker-compose.yml` provides it.

**Verified** by `tests/test_concurrency.py` against PostgreSQL 17:

- Of 10 concurrent requests for the same slot, exactly one succeeds.
- A burst of overlapping ranges never produces two overlapping confirmed reservations.
- A request competing with an uncommitted overlapping row waits, then gets `409` if that row commits or `201` if it rolls back.

## 2. Half-open, timezone-aware date ranges

**Decision.** A reservation covers `[start_date, end_date)`. A reservation ending exactly when another starts does not overlap it. Both bounds are `TIMESTAMP WITH TIME ZONE`; the API accepts ISO-8601 timestamps with an offset and stores them in UTC. `end_date > start_date` is enforced by both input validation and a `CHECK` constraint.

**Alternatives considered.** Inclusive ranges `[start, end]` make back-to-back rentals ambiguous ("returned on the 5th, picked up on the 5th"). Plain `DATE` columns are simpler but cannot express pick-up and return times.

**Trade-offs.** Clients must send timezone-aware timestamps; naive timestamps are rejected rather than guessed.

## 3. Idempotency keys stored on the reservation, scoped per customer

**Decision.** Reservation creation accepts an optional `Idempotency-Key`. The key is stored on the reservation row together with a hash of the request payload, under `UNIQUE (customer_id, idempotency_key)`.

- **Same key, same payload:** the existing reservation is returned; no duplicate is created.
- **Same key, different payload:** `422 idempotency_key_reused`, following the IETF `Idempotency-Key` header draft.
- **Same key, different customer:** independent; keys are scoped per customer.
- **Concurrent retries:** they are serialized by the vehicle lock, so every retry after the first finds the key and returns the same reservation. The unique constraint is the backstop: if two inserts with the same key ever race, only one commits, and the loser re-reads and returns the winner's reservation.

Because the key and the reservation are written in the same row, they commit or roll back together. A failure can never leave a recorded key without its reservation, or the reverse.

**Alternatives considered.** A separate `idempotency_keys` table storing serialized responses is more general (it can cover every endpoint), but adds a second write that must share the transaction, and is more than this service needs.

**Retention.** Keys are stored on the reservation row and retained for as long as the reservation exists. They are not expired separately: each key adds a few bytes to a row that exists anyway, and there is no legitimate reason to reuse a key. A client can safely retry with the same key at any time; reusing a key for a different request always returns `422`. Clients should generate a new key (for example a UUID) for every new intent.

Assumption: reservations are not deleted. If a data-retention policy is added later, keys are removed together with their reservations. A separate keys table (for example to cover more endpoints) would need its own expiry; the common convention there is at least 24 hours, with a periodic cleanup job.

**Why the database, not a cache.** Storing keys in Redis or a similar cache would put the key and the reservation in two systems with no shared transaction. A crash between the two writes, or cache eviction, would allow a duplicate reservation on retry. Keeping both in one row makes them atomic and adds no infrastructure.

## Transactions and failure handling

### Concurrency model

- Endpoints are synchronous (`def`). FastAPI runs them in a thread pool, so requests execute in parallel.
- Each request gets its own SQLAlchemy `Session` from the `get_db` dependency. A session is never shared between threads, and it is closed (rolling back anything uncommitted) when the request ends.
- Sessions take connections from SQLAlchemy's connection pool.
- Postgres runs at its default `READ COMMITTED` isolation. Correctness does not depend on a stricter isolation level; it comes from constraints and row locks inside the database. Because of that, the guarantees hold across threads, worker processes and multiple API instances. An in-process lock would not.
- Async SQLAlchemy was not used: the synchronous stack is simpler, and the database is the arbiter either way. Using `async def` endpoints with synchronous database calls would block the event loop, so all endpoints are plain `def`.

### Transaction boundaries

Every write is exactly one database transaction (`with db.begin():`). It commits only if the whole block succeeds; any exception rolls everything back. No workflow has side effects outside the database, so a failure can never leave a partially applied change.

| Operation | Inside one transaction |
|---|---|
| Create vehicle | `INSERT` vehicle |
| Change vehicle status | Lock vehicle (`FOR UPDATE`), check for active reservations, `UPDATE` status |
| Create reservation | Idempotency lookup, past-date check, lock vehicle (`FOR UPDATE`), maintenance check, `INSERT` reservation together with its idempotency key and request hash |
| Cancel reservation | Lock reservation (`FOR UPDATE`), check status, `UPDATE` status |

Reads (get vehicle, get reservation, availability) need no explicit transaction.

### Failure behavior

| Failure | What happens | Response |
|---|---|---|
| Business rule violated (maintenance, past date, already cancelled) | Exception inside the block; rollback, nothing written | `4xx` with the domain error code |
| Overlap rejected by the exclusion constraint | Rollback; the conflicting range is looked up for the message | `409 vehicle_unavailable` |
| `CHECK` constraint violated (only reachable if input validation is bypassed) | Rollback | `422 invalid_date_range` |
| Concurrent retry with the same idempotency key | Waits for the vehicle lock, then finds the key | `200` with the original reservation |
| Database unreachable or connection lost mid-transaction | Postgres discards the uncommitted transaction | `503 database_unavailable` |
| Connection pool exhausted (wait exceeds the pool timeout) | No transaction started | `503 database_unavailable` |
| Any other unexpected error | Rollback; full traceback logged, never returned | `500 internal_error` |

**Unknown commit outcome.** If the connection drops while `COMMIT` is in flight, the client receives `503`, but the reservation may in fact have been committed. This is exactly the case idempotency keys exist for: retrying with the same key returns the committed reservation instead of creating a second one or failing with `409`.

## Other business rules

### Maintenance

Maintenance is a vehicle status (`active` / `maintenance`) with no date range.

- A vehicle in `maintenance` cannot receive a new reservation, for any dates: `409 vehicle_in_maintenance`.
- Moving a vehicle to `maintenance` is rejected while it has a confirmed reservation that has not ended (`end_date > now()`), including one in progress: `409 invalid_state_transition`. Existing reservations are never silently cancelled or left pointing at an unusable vehicle.
- Moving a vehicle back to `active` is always allowed.

**Race between "set maintenance" and "create reservation".** Both paths take `SELECT ... FOR UPDATE` on the vehicle row, so they run one after the other:

- If a reservation is in flight, the status change waits for it to commit, then checks for active reservations and sees it.
- If the status change commits first, a waiting reservation re-reads the locked row, sees `maintenance`, and is rejected.

An explicit lock is needed: the lock that the foreign key takes on insert (`FOR KEY SHARE`) does not conflict with an `UPDATE` of a non-key column such as `status`. Both orders are covered by deterministic tests in `tests/test_concurrency.py`.

**Alternative considered.** Maintenance windows with a start and end, where reservations only have to avoid the window. This is more flexible, but the "no overlap" invariant would then span two tables, which an exclusion constraint cannot enforce. It would need a shared "vehicle blocks" table or explicit locking. The requirements frame maintenance as a status, so the simpler model was chosen.

### Reservations in the past

A new reservation must not start in the past: `start_date < now()` is rejected with `422 start_date_in_past`. A reservation starting exactly now is accepted. Clients that want an immediate rental should send a time slightly in the future to absorb network latency.

The check runs in the service **after** the idempotency lookup, not in the request schema. Otherwise a legitimate retry of a reservation that started a few seconds ago would get `422` instead of the reservation that was already created. Availability queries for past ranges are allowed, since they have no side effects.

### Cancellation

- `confirmed → cancelled` is the only transition.
- Cancelling an already-cancelled reservation returns `409 invalid_state_transition`. It is not treated as an idempotent success, so the client can tell "I cancelled it" apart from "it was already cancelled".
- A cancelled reservation immediately stops blocking availability (see decision 1).

## Known limitations

**Concurrency and time**

- **Per-vehicle serialization.** Reservation creation for one vehicle runs one request at a time (see decision 1). This is fine for a rental fleet, but would become a bottleneck for a resource booked many times per second.
- **Application clock.** "Now" comes from the API server's clock. It is used by the past-start check and by "has active reservations" when entering maintenance. With several instances, clock skew could let a reservation start a few seconds in the past. Using the database's `now()` instead would give every instance the same clock.
- **Availability is a snapshot, not a hold.** `GET .../availability` answers for the moment of the query. Another client can take the slot a millisecond later; the guarantee is only enforced when a reservation is created.

**Idempotency**

- **Only reservation creation takes an `Idempotency-Key`.** Retries of other writes are still safe, but answer differently:
  - Vehicle creation returns `409 duplicate_plate_number`, because the plate number is unique.
  - Cancellation returns `409 invalid_state_transition`; the client can `GET` the reservation to confirm its state.
- **A retry without a key after an unknown outcome gets `409`,** even though the reservation that blocks it is the client's own.

**Scope**

- **No authentication or authorization.** `customer_id` is a client-supplied string, and any caller can read or cancel any reservation by ID.
- **Maintenance has no date range.** A vehicle with a reservation months ahead cannot enter maintenance until that reservation ends or is cancelled.
- **No list or search endpoints,** for example reservations by vehicle or by customer.

**Operations**

- **Migrations run on container start.** That is fine for a single instance. With several replicas, migrations should run as a separate deployment step, so instances do not race to apply them.
- **Metrics are identified but not instrumented.** The service emits structured log events; the metrics in the README would be derived from them or added with a metrics library.

**Tests**

- **Deterministic concurrency tests use a timing check.** They assert that a request is still blocked after 0.5 seconds. On a very slow machine this could pass for the wrong reason, but it cannot cause a false failure: the outcome asserted after the lock is released is the real guarantee.
- **The connection is killed before `COMMIT`, not during it.** The transaction tests cover a connection lost mid-transaction. The rarer case of a connection lost while `COMMIT` itself is in flight (an unknown outcome) is not reproduced; it is handled by idempotent retries, which are tested separately.
