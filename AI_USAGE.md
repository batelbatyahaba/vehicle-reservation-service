# AI assistance

## Tools used

I built this project with **Claude Code** (Anthropic, Claude Opus 5.5), used as a pair programmer in the Claude desktop app. It wrote most of the first drafts: project scaffolding, models and migration, services, endpoints, tests, and the first versions of the README and DECISIONS.md. Commits it helped write carry a `Co-Authored-By` trailer.

## What I decided

The design and business decisions were mine. The assistant proposed options, and I chose between them, often after asking it to justify or reconsider:

- Half-open date ranges, and rejecting reservations that start in the past.
- Cancelling an already-cancelled reservation returns `409`, not an idempotent `200`.
- Maintenance has no date range, so it is blocked while active reservations exist.
- Idempotency keys stay on the reservation row for its whole lifetime, stored in the database rather than a cache. I rejected a 24-hour expiry, which this design does not need.
- A branch per stage, with small commits and a pull request for each.

I also asked for a code review at every stage, and I questioned choices I did not understand: for example, why overlap is checked in the database and not in the Pydantic model, and how transactions behave with threads.

## How I verified the output

I did not accept code because it looked right. Each claim was checked against a real PostgreSQL database:

1. **Real database, no mocks.** Every constraint and lock was exercised against PostgreSQL 17, first with throwaway scripts during development, then with the automated test suite.
2. **Tests for the hard cases.** The 60 tests include deterministic interleavings. A test holds a lock or an uncommitted row open, and asserts that the competing request waits and then gets the right outcome. They also include connections killed in the middle of a transaction.
3. **Mutation checks.** To confirm the tests actually test something, I deliberately broke critical lines one at a time, and checked that a test failed each time. The lines covered the vehicle lock, the idempotency fallback, the order of the past-date check, the half-open comparison, the cancellation lock, and the deadlock-prevention lock.
4. **Repeated runs.** Running the suite repeatedly exposed a real bug in the assistant's original design. Concurrent overlapping inserts could deadlock in the exclusion constraint and return `503` instead of `409`. The first fix it proposed, retrying on deadlock, only made the failure rarer. I asked for a fix that removes the cause: per-vehicle serialization. I accepted it only after a deterministic test reproduced the deadlock, and 40 consecutive runs passed.
5. **Reviews that found real issues,** each fixed and re-verified:
   - Enum values stored as names (`ACTIVE`) instead of values.
   - Malformed JSON reported as a numeric field position.
   - Unmapped database errors (a `CHECK` violation, pool exhaustion) returning `500`.
   - Error logs written without tracebacks.
6. **Clean-room CI check.** Before relying on the CI workflow, I simulated it on a fresh clone with a fresh virtual environment and no local `.env`.

## Where the assistant was wrong or incomplete

- The original `FOR SHARE` locking allowed deadlocks under concurrent overlapping requests (see above and DECISIONS.md, decision 1).
- It first proposed a 24-hour idempotency-key expiry, which turned out to be unnecessary with keys stored on the reservation row.
- Transaction behavior was implemented before it was documented. I asked for the documentation explicitly.
