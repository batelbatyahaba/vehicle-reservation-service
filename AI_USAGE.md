# AI Assistance & Verification

During the development of this project, Claude Code (Anthropic) and Gemini were utilized as accelerated development tools (pair programmers) for drafting initial scaffolding, Pydantic schemas, and boilerplate code. However, all technical directions, architectural trade-offs, and critical decisions were strictly driven by me.

## How the AI was guided and used

- **Collaborative Tech Selection:** We evaluated different technological alternatives together (such as database vs. cache for idempotency, and concurrency models), and I made the final decisions based on strict data-integrity requirements.
- **Strict Code Conventions:** The assistant was directed to follow clean-code principles, typing guidelines, and explicit conventions before writing code.
- **Iterative Code Reviews:** Every major block of code went through code reviews where I challenged architectural choices, such as verifying why availability checks must live in the database (exclusion constraints) rather than in application-level validation.

## Rigorous Verification Process (Moving beyond AI output)

I did not accept any AI-generated code at face value. Every component was rigorously verified and tested against a live PostgreSQL 17 instance:

1. **Real-Database Integration:** Zero mocks were used for concurrency and transactional boundaries. All exclusion constraints, row locks (`FOR UPDATE`), and error mappings were verified against a live database.
2. **Mutation Testing:** To prove the test suite actually guards against failures, I directed the assistant to deliberately break critical parts of the code one by one (e.g., removing vehicle locks, altering date comparisons) and confirmed the automated tests caught every single breakage.
3. **Catching and Fixing AI Design Flaws:** Repeated concurrency runs exposed a subtle bug in the assistant's initial design—bursts of overlapping requests could cause deadlocks (`503` errors) within the exclusion constraint. The assistant's first fix (retry on deadlock) only made it rarer; I pushed for a root-cause fix: per-vehicle serialization (`FOR UPDATE`), reproduced with a deterministic test and verified over 40 consecutive stable runs.
4. **Clean-Room CI Simulation:** The GitHub Actions workflow and migration pipelines were simulated locally on a fresh, isolated clone with a new virtual environment and no local `.env` file, to confirm CI would pass on a clean environment.
