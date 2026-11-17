# Job Aid

Authenticated job discovery and application tracking. Next.js serves the dashboard; FastAPI owns business operations; a persistent Python worker processes durable Supabase runs and submits applications using Playwright.

## Local startup

1. Create a Python 3.11 virtual environment and install `backend/requirements.txt`.
2. Install browser dependencies with `python -m playwright install chromium`.
3. Configure the root `.env` from `.env.example`. Set a Supabase URL, service role key, and at least one supported model provider key. Never expose the service key through `NEXT_PUBLIC_*`.
4. Apply the database migrations to a development Supabase project as described in `docs/DEPLOYMENT.md`.
5. In `frontend/.env.local`, configure `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, and server-only `BACKEND_URL=http://127.0.0.1:8000`.
6. Run the API from the repository root: `.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000`.
7. Run the frontend: `cd frontend && npm ci && npm run dev`.
8. Start the worker only when the database is ready and its enabled profiles are authorized for real applications: `.venv/bin/python -m backend.worker.run_sweep`.

Open http://localhost:3000 and create/sign into an account. Confirm the signup email if required by your Supabase Auth settings. Upload a text-based PDF, review preferences and questions, and start a search. Auto-apply defaults to off for new profiles.

## Execution and evidence

A search creates a durable `agent_runs` row and returns immediately. The worker can schedule eligible searches every four hours when `WORKER_SCHEDULE_ENABLED=true`; the live deployment has scheduling disabled. One active run is allowed per candidate. Atomic database functions claim runs and applications. Daily limits count attempts, including failed and interrupted attempts, conservatively preventing repeat submissions.

Only a new explicit confirmation after Submit can produce `applied`. Screenshots must upload successfully and the confirmation must persist. Unclear click outcomes, missing confirmation, and interrupted attempts become `submission_uncertain`; they never automatically retry. Existing historical `applied` records without confirmation remain preserved and are flagged for review.

Screenshots are private and dashboard responses contain signed links valid for five minutes. Refresh the dashboard for a new link. CVs remain private. Users can only access their own dashboard through verified Supabase access tokens.

Matching uses one versioned embedding implementation. `local` requires a 384-dimensional sentence-transformer and fails visibly if unavailable. Explicit `hash` mode is less accurate and intended for offline development. Changing the embedding version rebuilds the candidate embedding during discovery; discovered jobs are re-embedded. AI scoring has no fabricated heuristic-success fallback. Explicit eligibility exclusions prevent queueing; unresolved eligibility remains for screening verification.

## Validation

- `python -m pytest backend/tests -q`: offline unit/API tests; no real submissions.
- `JOB_AID_BROWSER_TESTS=1 python -m pytest backend/tests/test_browser_confirmation.py -q`: controlled local forms using installed Chrome.
- PostgreSQL queue tests and staging instructions: `docs/DEPLOYMENT.md`.
- `cd frontend && npm run build`: production build and type checks.
- `python -m backend.worker.preflight`: read-only provider/model availability check; no CV is sent.

The worker supports directly accessible single-page forms. Multi-step applications, sign-in walls, CAPTCHA, and unsupported widgets may require manual completion. A timeout after a submit click is never proof of failure or permission to retry.
