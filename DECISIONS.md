# Decisions

> Draft. Sections marked **TBD** will be completed as the service layer is implemented.

## 1. Overlap prevention is enforced by a PostgreSQL exclusion constraint

**Decision.** The invariant "a vehicle cannot have two active reservations with overlapping date ranges" is enforced by the database, not by application code:

```sql
EXCLUDE USING gist (
  vehicle_id WITH =,
  tstzrange(start_date, end_date, '[)') WITH &&
) WHERE (status = 'confirmed')
```

Two concurrent inserts for the same slot cannot both commit: the second one blocks on the first, then fails with SQLSTATE `23P01` (`exclusion_violation`), which the service maps to `409 vehicle_unavailable`. The partial `WHERE` clause means cancelled reservations stop blocking availability as soon as the cancellation commits.

**Alternatives considered.**

| Option | Why not chosen |
|---|---|
| Check-then-insert in application code | Not safe under concurrency; explicitly ruled out by the requirements. |
| `SELECT ... FOR UPDATE` on the vehicle row, then check and insert | Correct, but the guarantee depends on every code path remembering to take the lock. Serializes all reservations of a vehicle, even non-overlapping ones. |
| `SERIALIZABLE` isolation with retry | Correct, but requires retry loops and produces serialization failures that are harder to map to meaningful errors. |
| Advisory locks | Same "every path must remember" weakness as row locks, with less visibility. |

**Trade-offs.** Ties the solution to PostgreSQL and the `btree_gist` extension (created in the first migration). Tests must run against real PostgreSQL rather than SQLite; `docker-compose.yml` provides it.

**Verified.** Against PostgreSQL 17: an overlapping insert is rejected, an adjacent range (`[1,5)` then `[5,8)`) is accepted, a cancelled reservation does not block, and of 5 concurrent inserts for the same slot exactly one succeeds.

## 2. Half-open, timezone-aware date ranges

**Decision.** A reservation covers `[start_date, end_date)`. A reservation ending exactly when another starts does not overlap it. Both bounds are `TIMESTAMP WITH TIME ZONE`; the API accepts ISO-8601 timestamps with an offset and stores them in UTC. `end_date > start_date` is enforced by both input validation and a `CHECK` constraint.

**Alternatives considered.** Inclusive ranges `[start, end]` make back-to-back rentals ambiguous ("returned on the 5th, picked up on the 5th"). Plain `DATE` columns are simpler but cannot express pick-up and return times.

**Trade-offs.** Clients must send timezone-aware timestamps; naive timestamps are rejected rather than guessed.

## 3. Idempotency keys stored on the reservation, scoped per customer

**Decision.** Reservation creation accepts an optional `Idempotency-Key`. The key is stored on the reservation row together with a hash of the request payload, under `UNIQUE (customer_id, idempotency_key)`.

- **Same key, same payload:** the existing reservation is returned; no duplicate is created.
- **Same key, different payload:** `422 idempotency_key_reused`, following the IETF `Idempotency-Key` header draft.
- **Same key, different customer:** independent; keys are scoped per customer.
- **Concurrent retries:** the unique constraint guarantees only one insert commits; the loser re-reads and returns the winner's reservation.

Because the key and the reservation are written in the same row, they commit or roll back together. A failure can never leave a recorded key without its reservation, or the reverse.

**Alternatives considered.** A separate `idempotency_keys` table storing serialized responses is more general (it can cover every endpoint), but adds a second write that must share the transaction, and is more than this service needs.

**Retention — assumption.** Keys live as long as the reservation row (no TTL). **TBD:** document the expected client retry window and how a TTL would be added if the table grows.

## Other business rules

### Maintenance

Maintenance is a vehicle status (`active` / `maintenance`) with no date range.

- A vehicle in `maintenance` cannot receive a new reservation, for any dates: `409 vehicle_in_maintenance`.
- Moving a vehicle to `maintenance` is rejected while it has a confirmed reservation that has not ended (`end_date > now()`), including one in progress: `409 invalid_state_transition`. Existing reservations are never silently cancelled or left pointing at an unusable vehicle.
- Moving a vehicle back to `active` is always allowed.

**Race between "set maintenance" and "create reservation".** Both paths lock the vehicle row:

- Reservation creation reads the vehicle with `SELECT ... FOR SHARE`. Shared locks are compatible, so reservations of the same vehicle still run in parallel; overlap between them is handled by the exclusion constraint.
- The status change reads it with `SELECT ... FOR UPDATE`, which conflicts with `FOR SHARE`. It waits for in-flight reservations to commit, then checks for active reservations and sees them.
- If the status change commits first, a waiting reservation re-reads the locked row, sees `maintenance`, and is rejected.

An explicit `FOR SHARE` is needed: the lock that the foreign key takes on insert (`FOR KEY SHARE`) does not conflict with an `UPDATE` of a non-key column such as `status`.

**Alternative considered.** Maintenance windows with a start and end, where reservations only have to avoid the window. This is more flexible, but the "no overlap" invariant would then span two tables, which an exclusion constraint cannot enforce. It would need a shared "vehicle blocks" table or explicit locking. The requirements frame maintenance as a status, so the simpler model was chosen.

### Cancellation

- `confirmed → cancelled` is the only transition.
- Cancelling an already-cancelled reservation returns `409 invalid_state_transition`. It is not treated as an idempotent success, so the client can tell "I cancelled it" apart from "it was already cancelled".
- A cancelled reservation immediately stops blocking availability (see decision 1).
