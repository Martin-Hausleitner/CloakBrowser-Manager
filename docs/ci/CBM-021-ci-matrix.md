# CBM-021 CI Matrix

This CI surface is intentionally split into small lanes so failures identify the broken boundary quickly.

## Lanes

- `project-gates`: schema validation through the Pydantic model tests, tracked env/token/secret filename guard, frontend semver plus backend CloakBrowser requirement guard, and a minimum 2 GiB workspace disk check.
- `backend-tests`: the full `backend/tests` pytest suite on Python 3.12 with `pytest-asyncio`, `pytest-trio`, and `trio` installed from `backend/requirements-dev.txt`.
- `migration-tests`: focused workspace migration and database regression tests.
- `frontend-tests`: `npm ci` plus the repository Vitest suite.
- `frontend-build`: `npm ci` plus `npm run build`, uploaded as a build artifact.
- `lint`: Ruff correctness lint for syntax and undefined-name classes only (`E9,F63,F7,F82`), scoped to `backend` and `scripts`.
- `secret-scan`: dependency-free `git grep` high-confidence token/private-key regex scan with lockfiles excluded.
- `docker-smoke`: builds the production Docker image using `deploy/ci/docker-compose.smoke.yml`, starts it, and waits for `GET /health`.

## Local equivalents

```bash
python3 -m pip install -r backend/requirements-dev.txt
python3 -m pytest backend/tests -q
python3 -m pytest backend/tests/test_workspace_migration.py backend/tests/test_database.py -q
python3 -m ruff check backend scripts --select E9,F63,F7,F82

cd frontend
npm ci
npm test
npm run build

cd ..
docker compose -f deploy/ci/docker-compose.smoke.yml config
docker compose -f deploy/ci/docker-compose.smoke.yml up -d --build
curl -fsS http://127.0.0.1:18080/health
docker compose -f deploy/ci/docker-compose.smoke.yml down -v --remove-orphans
```

## Deferred live-only checks

The Docker smoke lane performs the real image build, CloakBrowser binary download, container boot, and HTTP health probe in CI. Local validation for this change only checks compose syntax unless Docker is explicitly available and the live image build is acceptable for the machine.
