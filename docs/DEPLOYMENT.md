# Deployment and recovery

## Database rollout

Back up the target database and record its migration history before applying changes. Use a separate Supabase staging project first. The two existing initial migration files are identical historical artifacts; leave their recorded history intact. Do not delete or rename an applied migration. On a fresh project, apply the existing baseline using the project's migration tool, then the new migrations in order:

1. `20261117001_submission_state.sql` adds the enum value and must commit separately.
2. `20261117002_execution.sql` adds runs/attempts/worker heartbeat, private evidence access, ownership policies, atomic claims, and recovery functions.

The second migration is a one-time migration, not a repeatedly executable bootstrap. It preserves historical application status and flags missing evidence. It makes the proofs bucket private, so old public links cease working. Historical proof URLs are deliberately not signed automatically; review their actual objects and confirmation before migrating a trusted path. Do not claim that a historical screenshot proves submission merely because a file exists.

Test ownership with two staging accounts: account A must not see or update B through the API or database. Existing demo-user records are not automatically assigned to a newly created account. A deliberate, audited ownership migration is required if those records should be retained under a real account.

## Services

Deploy the API and persistent worker from the same source and environment. The API has no background scheduler. Do not enable the archived GitHub Actions schedule or a Vercel cron. The worker is the only scheduler. `docker compose up --build` defines three services; the frontend image receives only public Supabase configuration and the backend URL, not service-role or model-provider keys.

The live VPS uses `deploy/compose.vps.yml` with bounded CPU, memory, and logs. It proxies `/job-aid/` through `deploy/nginx-job-aid.conf` on `resolver.doxad.cloud`. The corresponding Vercel server-only `BACKEND_URL` is `https://resolver.doxad.cloud/job-aid`. VPS `.env` must remain mode `0600`. The worker has `WORKER_SCHEDULE_ENABLED=false`; `WORKER_AUTO_APPLY_ENABLED=true` allows only manually requested submission runs. The frontend action labels those runs Search and apply when the candidate has enabled Auto-Apply. With the preference off, the action queues discovery only.

For Vercel, configure public Supabase values and server-only `BACKEND_URL` pointing to the HTTPS API. The Next.js API proxies do not require Supabase administrative credentials. CV processing can take up to 120 seconds; choose hosting that supports that request duration. Discovery and application execution run outside HTTP request lifetimes. Configure Supabase Auth site/redirect URLs and email confirmation for the actual deployment domain.

Run the provider preflight before worker activation. Defaults were checked against provider documentation and account model listings. Availability does not verify completion quotas or all structured-output behavior. Optional OpenRouter/Cerebras routes require explicit model configuration.

Provider references:
- https://console.groq.com/docs/model/openai/gpt-oss-120b
- https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-lite

## Isolated PostgreSQL queue checks

Use an empty disposable PostgreSQL database only; the fixture creates roles and minimal schemas and is not a Supabase replacement.

```
psql "$JOB_AID_TEST_DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f backend/tests/fixtures/queue_base.sql \
  -f supabase/migrations/20261117001_submission_state.sql \
  -f supabase/migrations/20261117002_execution.sql
JOB_AID_TEST_DATABASE_URL="$JOB_AID_TEST_DATABASE_URL" python -m pytest backend/tests/test_queue_postgres.py -q
```

The fixture validates new SQL, concurrent claims, ownership tokens, quota enforcement, and stale recovery. It does not validate the original pgvector extension, Supabase Auth/Storage, or production migration history.

## Staging acceptance

Use staging-only credentials and a controlled employer form. Create and confirm an account, upload a test PDF, save preferences, discover controlled matches, queue a run, and observe worker progress. Verify an actual confirmation creates an attempt, timestamp, private screenshot, and confirmed status. Verify validation failure, missing answers, unavailable provider, database failure, and worker termination produce visible non-success outcomes. Confirm two accounts cannot cross boundaries. Confirm refreshing and repeated clicks reuse the active run. Never test submission against an employer without explicit authorization.

## Recovery

The worker reports a heartbeat during execution and while idle. The dashboard labels it offline after 90 seconds without a heartbeat. Runs stale for ten minutes are failed, and their in-flight application attempts become uncertain. Completed confirmations remain intact. Failed or uncertain attempts are not automatically requeued.

For an uncertain application, first check employer email/account confirmation and stored evidence. If submitted, preserve the audit trail and record independently verified confirmation through an audited maintenance operation. Only requeue when non-submission is established. Do not reset uncertain applications in bulk.

Use `/api/health` for API liveness, worker heartbeat age for execution monitoring, and centralized worker/API logs for operational errors. Redact credentials and candidate content from log exports. Keep worker hosts isolated from internal services; job pages are external untrusted content.

## Release boundary

Code and local tests do not establish production readiness. Apply migrations, validate staging end-to-end with real Supabase Auth/Storage, and review enabled candidate profiles before activating production workers. No production migration or employer submission is performed by the local implementation checks.

## Submission activation

`WORKER_AUTO_APPLY_ENABLED` defaults to `false` in the general configuration.
The live worker sets it to `true`, allowing submissions only when the candidate
has enabled Auto-Apply and explicitly clicks Search and apply. Background
scheduling remains disabled. If this setting is later turned off on the worker,
submission runs fail before claiming applications or opening a browser.
Candidate preferences and existing application records are preserved.

## Target discovery (2026-11-17)

The linked project is `pdfrrrr` (`zovizzgqngiasajjzhcw`). It is shared with
campaign, analytics, tracker, and blog tables. Recorded migrations are `20260616`,
`20260617`, and `20261116001`; the two execution migrations remain pending.
There is one enabled candidate profile and six legacy applied records. Only the
main database branch exists. Do not replay the duplicate baseline or repair
unrelated migration history to force a push.

Vercel project `autonomous-job-agent` is accessible, but has no `BACKEND_URL`
configured. Choose the staging database and persistent API/worker host before
publishing the new frontend. No unrelated project is designated as staging.
