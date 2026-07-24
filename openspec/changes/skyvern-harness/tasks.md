## 1. OpenSpec and scaffolding

- [x] 1.1 Create change `skyvern-harness` with proposal, design, specs
- [x] 1.2 Validate with `openspec validate skyvern-harness --strict`

## 2. Harness adapter

- [x] 2.1 Add `backend/harnesses/skyvern_harness.py` with capability probe, CDP URL builder, auth headers, connect/navigate runner
- [x] 2.2 Wire FastAPI routes under `/api/harnesses/skyvern` (capabilities, bind, run)
- [x] 2.3 Keep `skyvern` as optional import; never vendor AGPL sources

## 3. Tests and proof

- [x] 3.1 Unit tests for CDP binding, unavailable path, license field
- [x] 3.2 Proof runner: launch Cloak profile → Skyvern harness CDP path → screenshot to `.proof/2026-07-24-skyvern-harness.png`
- [x] 3.3 Write `SKYVERN-HARNESS-2026-07-24.md` with architecture + AGPL notice

## 4. Ship

- [x] 4.1 Commit on `feat/skyvern-harness`, push, open PR against default branch
